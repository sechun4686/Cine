"""
single_agent_service.py
Single vs Multi agent 비교 실험용 Single-agent 평가

multi-agent(agent_service.py)와 같은 것
    - 후보, TMDB 상세정보, 도구 함수(데이터 값)
    - 관점별 평가 기준 문구, 데이터 사용 규칙, 점수 기준 (agent_prompts.py)
    - 최종 점수 계산(calculate_final_score)과 가중치, 실패 처리 방식
    - 출력 형식 (technical / narrative / public / tool_usage / final_score / failed_agents)

multi-agent와 다른 것
    - 에이전트 3개 대신 에이전트 1개가 세 관점을 모두 맡는다

모드 (evaluate_candidates_single의 인자로 고른다)
    full  (select_tools=True)  : 에이전트 1개가 세 관점의 도구 14개 중에서 직접 고르고, 한 번에 평가한다
                                 도구 선택 제약(관점마다 2~4개, 필수 도구, 기본값)은 multi와 같은 validate_tools로 검증한다
                                 -> 완전한 multi-agent vs single-agent 비교 (도구 선택 + 채점 모두 단일)
                                 LLM 호출: 후보당 2회 (multi는 6회)
    score (tool_data_list)     : multi의 세 에이전트가 고른 데이터를 그대로 받아 한 번에 평가한다
                                 -> 채점을 나눈 효과만 비교 (도구 선택은 multi와 같음)
                                 LLM 호출: 후보당 1회
    all   (둘 다 없음)          : 도구 선택 없이 등록된 도구를 전부 실행한다 (비교 실험에는 쓰지 않는다)

사용 예 (full)
    single = await evaluate_candidates_single(client, copy.deepcopy(candidates), query, weights,
                                              select_tools=True)

사용 예 (score)
    multi_cands = copy.deepcopy(candidates)
    multi = await evaluate_candidates(client, multi_cands, query, weights)
    # evaluate_candidates는 입력 dict에 결과를 붙이므로 multi_cands가 원래 순서 그대로 데이터를 갖고 있다
    # (평가에 모두 실패해 순위에서 빠진 후보도 데이터는 남아 있다)
    data = [tool_data_from_multi(c) for c in multi_cands]
    single = await evaluate_candidates_single(client, copy.deepcopy(candidates), query, weights,
                                              tool_data_list=data)
"""

import asyncio
import json

import httpx

from .agent_prompts import build_single_prompt, build_single_select_prompt
from .agent_service import (
    AGENT_TYPES,
    EVAL_TEMPERATURE,
    SELECT_TEMPERATURE,
    FAIL_RESULT,
    MAX_CONCURRENT_LLM_CALLS,
    PARSE_RETRIES,
    ProgressCallback,
    _emit,
    calculate_final_score,
    parse_agent_response,
)
from .agent_tools import TOOL_REGISTRY, ToolContext, run_tools, validate_tools
from .llm_service import call_luxia, extract_json
from .tmdb_details import attach_details

# 세 관점의 reason(각 3~4문장)을 한 번에 받으므로 multi(500)보다 넉넉하게
SINGLE_MAX_TOKENS = 1500


def parse_single_response(raw: str | None) -> dict:
    """
    {"technical": {...}, "narrative": {...}, "public": {...}} 응답을 관점별 결과로 나눈다.
    빠지거나 형식이 틀린 관점만 FAIL_RESULT가 된다.
    """
    parsed = extract_json(raw or "")
    results = {}
    for agent_type in AGENT_TYPES:
        part = parsed.get(agent_type) if isinstance(parsed, dict) else None
        if isinstance(part, dict):
            results[agent_type] = parse_agent_response(json.dumps(part, ensure_ascii=False))
        else:
            results[agent_type] = dict(FAIL_RESULT)
    return results


def tool_data_from_multi(evaluated: dict) -> dict:
    """multi-agent 평가 결과 후보 1개에서 에이전트별로 실제 사용한 데이터를 꺼낸다.
    return: {관점: {도구명: 값}}"""
    usage = evaluated.get("tool_usage") or {}
    return {a: dict((usage.get(a) or {}).get("data") or {}) for a in AGENT_TYPES}


def validate_single_tools(raw: str | None) -> dict:
    """
    Single-agent가 고른 도구 목록을 관점별로 나눠, multi-agent와 같은 validate_tools로 검증한다.
    return: {관점: {"tools", "reason", "status", "rejected"}}
            어느 관점에도 없는 이름은 모든 관점의 rejected에 기록한다.
    """
    parsed = extract_json(raw) if raw else None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tools"), list):
        return {a: validate_tools(a, None) for a in AGENT_TYPES}

    names = [str(n).strip() for n in parsed["tools"]]
    known = {t for a in AGENT_TYPES for t in TOOL_REGISTRY[a]}
    unknown = [n for n in names if n not in known]
    reason = str(parsed.get("reason", "")).strip() or "-"

    plans = {}
    for a in AGENT_TYPES:
        own = [n for n in names if n in TOOL_REGISTRY[a]]
        plan = validate_tools(a, json.dumps({"tools": own, "reason": reason}, ensure_ascii=False))
        if unknown:
            plan = {**plan, "rejected": plan["rejected"] + unknown, "status": "filtered"}
        plans[a] = plan
    return plans


async def select_tools_single(
    client: httpx.AsyncClient,
    candidate: dict,
    query: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """LLM 한 번 호출로 세 관점의 도구를 고른다. return: {관점: plan}"""
    try:
        async with semaphore:
            raw = await call_luxia(
                client, "너는 데이터 조회 계획을 세우는 AI야.", build_single_select_prompt(candidate, query),
                temperature=SELECT_TEMPERATURE, max_tokens=300,
            )
    except Exception as e:
        print(f"[single_agent] 도구 선택 오류 ({candidate.get('title')}): {e}")
        raw = None

    plans = validate_single_tools(raw)
    changed = {a: p["status"] for a, p in plans.items() if p["status"] != "ok"}
    if changed:
        print(f"[single_agent] 도구 선택 수정 ({candidate.get('title')}): {changed}")
    return plans


async def collect_all_tool_data(ctx: ToolContext) -> tuple[dict, dict]:
    """등록된 모든 도구를 실행한다. return: ({관점: {도구: 값}}, {관점: 실패 도구 목록})"""
    results = await asyncio.gather(*(
        run_tools(ctx, agent_type, list(TOOL_REGISTRY[agent_type]))
        for agent_type in AGENT_TYPES
    ))
    data = {a: r[0] for a, r in zip(AGENT_TYPES, results)}
    failed = {a: r[1] for a, r in zip(AGENT_TYPES, results)}
    return data, failed


async def score_candidate_single(
    client: httpx.AsyncClient,
    candidate: dict,
    query: str,
    tool_data: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """LLM 한 번 호출로 세 관점 점수를 받는다. 세 관점이 모두 실패하면 PARSE_RETRIES만큼 다시 요청한다."""
    prompt = build_single_prompt(candidate, query, tool_data)
    results = {a: dict(FAIL_RESULT) for a in AGENT_TYPES}

    for attempt in range(PARSE_RETRIES + 1):
        try:
            async with semaphore:
                raw = await call_luxia(
                    client, "너는 영화 평가 AI야.", prompt,
                    temperature=EVAL_TEMPERATURE, max_tokens=SINGLE_MAX_TOKENS,
                )
        except Exception as e:
            print(f"[single_agent] 평가 오류 ({candidate.get('title')}): {e}")
            return results

        results = parse_single_response(raw)
        if not all(r["failed"] for r in results.values()):
            break
        print(f"[single_agent] 응답 해석 실패 ({candidate.get('title')}) "
              f"시도 {attempt + 1}/{PARSE_RETRIES + 1}: {(raw or '')[:80]!r}")

    failed = [a for a, r in results.items() if r["failed"]]
    if failed and len(failed) < len(AGENT_TYPES):
        print(f"[single_agent] 일부 관점 평가 실패 ({candidate.get('title')}): {failed}")
    return results


async def evaluate_candidates_single(
    client: httpx.AsyncClient,
    candidates: list[dict],
    query: str,
    weights: dict | None = None,
    on_progress: ProgressCallback | None = None,
    tool_data_list: list[dict] | None = None,
    select_tools: bool = False,
) -> list[dict]:
    """
    agent_service.evaluate_candidates와 같은 입력·출력 형식의 single-agent 버전

    select_tools  : True면 full 모드. 에이전트 1개가 세 관점의 도구를 직접 고른다.
    tool_data_list: score 모드. candidates와 같은 순서의 {관점: {도구명: 값}} 목록 (tool_data_from_multi 결과).
                    주어지면 TMDB 조회와 도구 실행 없이 이 데이터로 평가한다.
    """
    if not candidates:
        return []
    if tool_data_list is not None and select_tools:
        raise ValueError("tool_data_list와 select_tools는 함께 쓸 수 없습니다.")
    if tool_data_list is not None and len(tool_data_list) != len(candidates):
        raise ValueError("tool_data_list는 candidates와 길이가 같아야 합니다.")

    if tool_data_list is None:
        all_details = await attach_details(client, candidates)
    else:
        all_details = [{} for _ in candidates]
    _emit(on_progress, {"type": "stage", "stage": "details", "status": "done"})

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)
    person_cache: dict = {}

    async def evaluate_one(index: int, candidate: dict, details: dict) -> dict:
        base_event = {"type": "agent", "index": index, "title": candidate.get("title")}
        given = tool_data_list[index] if tool_data_list is not None else None
        plans = await select_tools_single(client, candidate, query, semaphore) if select_tools else None

        # 진행 이벤트는 multi와 같은 형식으로 보낸다 (로딩 화면 호환)
        for a in AGENT_TYPES:
            if given is not None:
                names = list(given[a])
            elif plans is not None:
                names = plans[a]["tools"]
            else:
                names = list(TOOL_REGISTRY[a])
            labels = [TOOL_REGISTRY[a][t]["label"] for t in names if t in TOOL_REGISTRY[a]]
            _emit(on_progress, {**base_event, "agent": a, "phase": "tools_selected", "tools": labels})

        if given is not None:
            tool_data = {a: dict(given.get(a) or {}) for a in AGENT_TYPES}
            tool_failed = {a: [] for a in AGENT_TYPES}
        elif plans is not None:
            ctx = ToolContext(client, candidate, details, person_cache)
            ran = await asyncio.gather(*(run_tools(ctx, a, plans[a]["tools"]) for a in AGENT_TYPES))
            tool_data = {a: r[0] for a, r in zip(AGENT_TYPES, ran)}
            tool_failed = {a: r[1] for a, r in zip(AGENT_TYPES, ran)}
        else:
            ctx = ToolContext(client, candidate, details, person_cache)
            tool_data, tool_failed = await collect_all_tool_data(ctx)
        for a in AGENT_TYPES:
            _emit(on_progress, {**base_event, "agent": a, "phase": "data_ready"})

        results = await score_candidate_single(client, candidate, query, tool_data, semaphore)
        for a in AGENT_TYPES:
            _emit(on_progress, {**base_event, "agent": a, "phase": "scored",
                                "score": results[a]["score"], "failed": results[a]["failed"]})

        source = "from_multi" if given is not None else ("self_selected" if plans is not None else "all")
        return {"results": results, "tool_data": tool_data, "tool_failed": tool_failed,
                "source": source, "plans": plans}

    outputs = await asyncio.gather(*(
        evaluate_one(i, c, d) for i, (c, d) in enumerate(zip(candidates, all_details))
    ))

    evaluated = []
    for c, out in zip(candidates, outputs):
        results = out["results"]
        for a in AGENT_TYPES:
            c[a] = results[a]
        reasons = {"from_multi": "single-agent: multi-agent가 고른 데이터를 그대로 사용",
                   "all": "single-agent: 도구 선택 없이 전체 도구 사용"}
        c["tool_usage"] = {}
        for a in AGENT_TYPES:
            plan = (out["plans"] or {}).get(a) or {}
            c["tool_usage"][a] = {
                "tools": list(out["tool_data"][a]),
                "reason": plan.get("reason") or reasons.get(out["source"], "-"),
                "status": out["source"] if not plan else f"self_selected:{plan['status']}",
                "rejected": plan.get("rejected", []),
                "failed": out["tool_failed"][a],
                "data": out["tool_data"][a],
            }
        c["final_score"] = calculate_final_score({a: results[a]["score"] for a in AGENT_TYPES}, weights)
        c["failed_agents"] = [a for a in AGENT_TYPES if results[a]["failed"]]

        if c["final_score"] is None:
            print(f"[single_agent] 세 관점 모두 평가 실패 -> 순위에서 제외 ({c.get('title')})")
            continue
        evaluated.append(c)

    return sorted(evaluated, key=lambda x: x["final_score"], reverse=True)
