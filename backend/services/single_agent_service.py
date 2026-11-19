"""
single_agent_service.py
Single vs Multi agent 비교 실험용 Single-agent 평가

multi-agent(agent_service.py)와 같은 것
    - 후보, TMDB 상세정보, 도구 함수(데이터 값)
    - 관점별 평가 기준 문구, 데이터 사용 규칙, 점수 기준 (agent_prompts.py)
    - 최종 점수 계산(calculate_final_score)과 가중치, 실패 처리 방식
    - 출력 형식 (technical / narrative / public / tool_usage / final_score / failed_agents)

multi-agent와 다른 것 (비교하려는 변인은 이것 하나)
    - 에이전트 3개 대신 LLM 한 번 호출로 세 관점을 함께 평가한다

평가 데이터
    비교 실험에서는 multi-agent의 세 에이전트가 고른 도구 데이터를 그대로 넘긴다 (tool_data_list).
    두 방식이 똑같은 데이터를 보므로, 결과 차이는 역할 분리에서만 생긴다.
    tool_data_list 없이 단독으로 실행하면 등록된 도구를 전부 실행한다 (비교 실험에는 쓰지 않는다).

LLM 호출 수: 후보당 1회 (multi-agent는 후보당 6회: 에이전트 3개 x 도구 선택·평가)

사용 예 (비교 실험)
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

from .agent_prompts import build_single_prompt
from .agent_service import (
    AGENT_TYPES,
    EVAL_TEMPERATURE,
    FAIL_RESULT,
    MAX_CONCURRENT_LLM_CALLS,
    PARSE_RETRIES,
    ProgressCallback,
    _emit,
    calculate_final_score,
    parse_agent_response,
)
from .agent_tools import TOOL_REGISTRY, ToolContext, run_tools
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
) -> list[dict]:
    """
    agent_service.evaluate_candidates와 같은 입력·출력 형식의 single-agent 버전

    tool_data_list: candidates와 같은 순서의 {관점: {도구명: 값}} 목록 (tool_data_from_multi 결과).
                    주어지면 TMDB 조회와 도구 실행 없이 이 데이터로 평가한다.
    """
    if not candidates:
        return []
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

        # 진행 이벤트는 multi와 같은 형식으로 보낸다 (로딩 화면 호환)
        for a in AGENT_TYPES:
            names = list(given[a]) if given is not None else list(TOOL_REGISTRY[a])
            labels = [TOOL_REGISTRY[a][t]["label"] for t in names if t in TOOL_REGISTRY[a]]
            _emit(on_progress, {**base_event, "agent": a, "phase": "tools_selected", "tools": labels})

        if given is not None:
            tool_data = {a: dict(given.get(a) or {}) for a in AGENT_TYPES}
            tool_failed = {a: [] for a in AGENT_TYPES}
        else:
            ctx = ToolContext(client, candidate, details, person_cache)
            tool_data, tool_failed = await collect_all_tool_data(ctx)
        for a in AGENT_TYPES:
            _emit(on_progress, {**base_event, "agent": a, "phase": "data_ready"})

        results = await score_candidate_single(client, candidate, query, tool_data, semaphore)
        for a in AGENT_TYPES:
            _emit(on_progress, {**base_event, "agent": a, "phase": "scored",
                                "score": results[a]["score"], "failed": results[a]["failed"]})

        return {"results": results, "tool_data": tool_data, "tool_failed": tool_failed,
                "source": "from_multi" if given is not None else "all"}

    outputs = await asyncio.gather(*(
        evaluate_one(i, c, d) for i, (c, d) in enumerate(zip(candidates, all_details))
    ))

    evaluated = []
    for c, out in zip(candidates, outputs):
        results = out["results"]
        for a in AGENT_TYPES:
            c[a] = results[a]
        c["tool_usage"] = {
            a: {
                "tools": list(out["tool_data"][a]),
                "reason": ("single-agent: multi-agent가 고른 데이터를 그대로 사용"
                           if out["source"] == "from_multi"
                           else "single-agent: 도구 선택 없이 전체 도구 사용"),
                "status": out["source"],
                "rejected": [],
                "failed": out["tool_failed"][a],
                "data": out["tool_data"][a],
            }
            for a in AGENT_TYPES
        }
        c["final_score"] = calculate_final_score({a: results[a]["score"] for a in AGENT_TYPES}, weights)
        c["failed_agents"] = [a for a in AGENT_TYPES if results[a]["failed"]]

        if c["final_score"] is None:
            print(f"[single_agent] 세 관점 모두 평가 실패 -> 순위에서 제외 ({c.get('title')})")
            continue
        evaluated.append(c)

    return sorted(evaluated, key=lambda x: x["final_score"], reverse=True)
