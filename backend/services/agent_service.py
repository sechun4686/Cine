"""
agent_service.py
Technical / Narrative / Public 에이전트 평가 서비스 (LangGraph StateGraph)

그래프 구조 (후보 1개당 1회 실행)

            ┌─> technical_agent ─┐
    START ──┼─> narrative_agent ─┼─> aggregate ─> END
            └─> public_agent ────┘

각 에이전트 노드 내부 흐름
    select_tools   : LLM이 허용된 도구 목록 안에서 필요한 데이터를 고른다   (프롬프트: agent_prompts.py)
    validate_tools : 코드에서 선택 결과를 다시 검증한다                     (agent_tools.py)
    run_tools      : 선택된 도구만 실행해 데이터를 모은다                   (agent_tools.py)
    score          : 모은 데이터만 근거로 0~10점과 이유를 생성한다

파일 구성
    agent_service.py : 그래프, 노드, 점수 계산, evaluate_candidates (이 파일)
    agent_tools.py   : 도구 정의(TOOL_REGISTRY), 검증, 실행
    tmdb_details.py  : TMDB 상세정보 조회와 필드 추출
    agent_prompts.py : 도구 선택 / 평가 프롬프트
    llm_service.py   : Luxia 호출, JSON 파싱

사용 예 (main.py)
    ranked = await evaluate_candidates(client, candidates, query, weights)

출력: 각 candidate에 아래 키가 추가되어 final_score 내림차순으로 반환된다
    director / cast / keywords        : 기존과 동일 (다른 서비스 호환용)
    technical / narrative / public    = {"score": float, "reason": str}
    tool_usage = {에이전트: {"tools", "reason", "status", "rejected", "failed", "data"}}
    final_score = float
"""

import asyncio
from typing import TypedDict

import httpx
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from .agent_prompts import build_prompt, build_select_prompt
from .agent_tools import ToolContext, run_tools, validate_tools
from .llm_service import call_luxia, extract_json
from .tmdb_details import attach_details

load_dotenv()  # main.py에서 이미 불러오고 있다면 이 줄은 지워도 된다

# ------------------------------------------------------------------
# 설정
# ------------------------------------------------------------------
AGENT_TYPES = ["technical", "narrative", "public"]

# 프로토타입과 동일한 기본 가중치 (35 : 35 : 30)
DEFAULT_WEIGHTS = {"technical": 0.35, "narrative": 0.35, "public": 0.30}

# 동시에 보낼 Luxia 호출 수 제한 (5개 후보 x 3개 에이전트 x 2회 = 30회)
MAX_CONCURRENT_LLM_CALLS = 6

FAIL_RESULT = {"score": 5.0, "reason": "평가 실패"}

# 도구 선택은 재현성을 위해 낮은 temperature 사용
SELECT_TEMPERATURE = 0.1
EVAL_TEMPERATURE = 0.7


# ------------------------------------------------------------------
# LangGraph State
# ------------------------------------------------------------------
class EvalState(TypedDict, total=False):
    candidate: dict          # 평가할 후보 1개
    details: dict            # 후보의 TMDB 상세정보 (credits, keywords, reviews 포함)
    query: str               # 사용자 검색어
    weights: dict | None     # 최종 점수 가중치 (None이면 기본값)
    technical: dict          # {"score", "reason"}
    narrative: dict
    public: dict
    technical_tools: dict    # 도구 선택/실행 기록
    narrative_tools: dict
    public_tools: dict
    final_score: float


# ------------------------------------------------------------------
# 도구 선택 (LLM 호출 + 검증)
# ------------------------------------------------------------------
async def select_tools(
    client: httpx.AsyncClient,
    candidate: dict,
    query: str,
    agent_type: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    prompt = build_select_prompt(candidate, query, agent_type)

    try:
        async with semaphore:
            raw = await call_luxia(
                client, "너는 데이터 조회 계획을 세우는 AI야.", prompt,
                temperature=SELECT_TEMPERATURE, max_tokens=200,
            )
    except Exception as e:
        print(f"[agent_service] {agent_type} 도구 선택 오류 ({candidate.get('title')}): {e}")
        raw = None

    plan = validate_tools(agent_type, raw)
    if plan["status"] != "ok":
        print(f"[agent_service] {agent_type} 도구 선택 {plan['status']} "
              f"({candidate.get('title')}) rejected={plan['rejected']} -> {plan['tools']}")
    return plan


# ------------------------------------------------------------------
# 응답 파싱
# ------------------------------------------------------------------
def parse_agent_response(raw: str) -> dict:
    """LLM 응답에서 score / reason을 꺼낸다. 실패하면 FAIL_RESULT."""
    result = extract_json(raw)
    if result is None:
        return dict(FAIL_RESULT)

    # 중첩 구조인 경우 score가 있는 dict를 꺼낸다
    if "score" not in result:
        for v in result.values():
            if isinstance(v, dict) and "score" in v:
                result = v
                break

    if "score" not in result:
        return dict(FAIL_RESULT)

    try:
        score = float(result["score"])
    except (TypeError, ValueError):
        return dict(FAIL_RESULT)

    return {
        "score": max(0.0, min(10.0, score)),  # 0~10 범위로 보정
        "reason": str(result.get("reason", "-")),
    }


async def score_candidate(
    client: httpx.AsyncClient,
    candidate: dict,
    query: str,
    agent_type: str,
    tool_data: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    prompt = build_prompt(candidate, query, agent_type, tool_data)

    try:
        async with semaphore:
            raw = await call_luxia(client, "너는 영화 평가 AI야.", prompt, temperature=EVAL_TEMPERATURE)
        return parse_agent_response(raw)
    except Exception as e:
        print(f"[agent_service] {agent_type} 평가 오류 ({candidate.get('title')}): {e}")
        return dict(FAIL_RESULT)


# ------------------------------------------------------------------
# 최종 점수 계산
# ------------------------------------------------------------------
def calculate_final_score(scores: dict, weights: dict | None = None) -> float:
    """
    scores  : {"technical": 8.5, "narrative": 7.0, "public": 6.0}
    weights : {"technical": 0.4, "narrative": 0.4, "public": 0.2}
              None이면 기본 가중치(35:35:30) 사용
    """
    w = weights or DEFAULT_WEIGHTS

    total_w = sum(w.get(a, 0) for a in AGENT_TYPES)
    if total_w <= 0:
        w, total_w = DEFAULT_WEIGHTS, 1.0

    # 가중치 합이 1.0이 아니어도 정규화해서 사용
    final = sum(scores[a] * w.get(a, 0) for a in AGENT_TYPES) / total_w
    return round(final, 2)


# ------------------------------------------------------------------
# LangGraph 구성
# ------------------------------------------------------------------
def build_graph(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, person_cache: dict):
    """에이전트 3개를 병렬 노드로, 점수 계산을 마지막 노드로 하는 그래프를 만든다."""

    def make_agent_node(agent_type: str):
        async def agent_node(state: EvalState) -> dict:
            candidate = state["candidate"]
            query = state["query"]

            # 1) 도구 선택 + 2) 검증
            plan = await select_tools(client, candidate, query, agent_type, semaphore)

            # 3) 도구 실행
            ctx = ToolContext(client, candidate, state.get("details", {}), person_cache)
            tool_data, failed = await run_tools(ctx, agent_type, plan["tools"])

            # 4) 평가
            result = await score_candidate(client, candidate, query, agent_type, tool_data, semaphore)

            # 자기 키만 State에 쓴다
            return {
                agent_type: result,
                f"{agent_type}_tools": {**plan, "failed": failed, "data": tool_data},
            }

        return agent_node

    def aggregate_node(state: EvalState) -> dict:
        scores = {a: state[a]["score"] for a in AGENT_TYPES}
        return {"final_score": calculate_final_score(scores, state.get("weights"))}

    builder = StateGraph(EvalState)

    # 주의: 노드 이름은 State 키(technical 등)와 같으면 에러가 나서 _agent를 붙인다
    for agent_type in AGENT_TYPES:
        builder.add_node(f"{agent_type}_agent", make_agent_node(agent_type))
        builder.add_edge(START, f"{agent_type}_agent")  # START에서 세 노드로 동시에 분기

    builder.add_node("aggregate", aggregate_node)
    builder.add_edge([f"{a}_agent" for a in AGENT_TYPES], "aggregate")  # 세 노드가 모두 끝나면 실행
    builder.add_edge("aggregate", END)

    return builder.compile()


# ------------------------------------------------------------------
# 후보 전체 평가 + 정렬
# ------------------------------------------------------------------
async def evaluate_candidates(
    client: httpx.AsyncClient,
    candidates: list[dict],
    query: str,
    weights: dict | None = None,
) -> list[dict]:
    """
    candidates : candidate_service.select_candidates() 결과
    weights    : 최종 점수 가중치 (None이면 35:35:30)
    return     : final_score 내림차순으로 정렬된 후보 리스트
    """
    if not candidates:
        return []

    # 1) TMDB 상세정보 한 번에 받아오기 (도구들이 공유)
    all_details = await attach_details(client, candidates)

    # 2) 후보마다 그래프를 한 번씩 실행 (후보끼리도 병렬)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)
    person_cache: dict = {}
    graph = build_graph(client, semaphore, person_cache)

    final_states = await asyncio.gather(*(
        graph.ainvoke({"candidate": c, "details": d, "query": query, "weights": weights})
        for c, d in zip(candidates, all_details)
    ))

    # 3) 그래프 결과를 후보 dict에 붙인다
    for c, state in zip(candidates, final_states):
        c["technical"] = state["technical"]
        c["narrative"] = state["narrative"]
        c["public"] = state["public"]
        c["tool_usage"] = {a: state.get(f"{a}_tools", {}) for a in AGENT_TYPES}
        c["final_score"] = state["final_score"]

    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)