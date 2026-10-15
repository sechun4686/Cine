"""
agent_service.py
Technical / Narrative / Public 에이전트 평가 서비스

역할
1. 후보 콘텐츠를 Technical / Narrative / Public 세 관점에서 평가
2. 관점별 점수(score)와 근거(reason) 생성
3. 세 점수를 가중합해 최종 점수(final_score) 계산

사용 예 (main.py)
    ranked = await evaluate_candidates(client, candidates, query, weights)

입력 candidate dict에서 사용하는 키 (candidate_service 출력 기준)
    id, title, overview, year, vote_average, vote_count, popularity, media_type
평가 전에 attach_details()가 아래 키를 추가한다 (TMDB 상세 API)
    director, cast, keywords

출력: 각 candidate에 아래 키가 추가되어 final_score 내림차순으로 반환된다
    technical / narrative / public = {"score": float, "reason": str}
    final_score = float
"""

import asyncio
import json
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()  # main.py에서 이미 불러오고 있다면 이 줄은 지워도 된다

# ------------------------------------------------------------------
# 설정
# ------------------------------------------------------------------
TMDB_BASE_URL = "https://api.themoviedb.org/3"

LUXIA_URL = "https://bridge.luxiacloud.com/luxia/v1/chat"
LUXIA_MODEL = "luxia3-llm-32b-0731"

AGENT_TYPES = ["technical", "narrative", "public"]

# 프로토타입과 동일한 기본 가중치 (35 : 35 : 30)
DEFAULT_WEIGHTS = {"technical": 0.35, "narrative": 0.35, "public": 0.30}

# 동시에 보낼 Luxia 호출 수 제한 (5개 후보 x 3개 에이전트 = 15회)
MAX_CONCURRENT_LLM_CALLS = 6

FAIL_RESULT = {"score": 5.0, "reason": "평가 실패"}


# ------------------------------------------------------------------
# TMDB 상세정보 (감독 / 출연진 / 키워드)
# ------------------------------------------------------------------
async def fetch_details(client: httpx.AsyncClient, candidate: dict) -> dict:
    """TMDB 상세 API에서 credits, keywords를 함께 받아온다. 실패하면 빈 dict."""

    token = os.getenv("TMDB_API_KEY")
    if not token:
        raise ValueError("TMDB_API_KEY 환경변수가 설정되지 않았습니다.")

    media_type = candidate.get("media_type", "movie")

    try:
        response = await client.get(
            f"{TMDB_BASE_URL}/{media_type}/{candidate['id']}",
            headers={
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
            },
            params={
                "language": "ko-KR",
                "append_to_response": "credits,keywords",
            },
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[agent_service] TMDB 상세정보 실패 (id={candidate.get('id')}): {e}")
        return {}


def extract_director(details: dict, media_type: str) -> str:
    crew = details.get("credits", {}).get("crew", [])
    names = [c["name"] for c in crew if c.get("job") == "Director"]

    # 드라마는 crew에 Director가 없는 경우가 많아 제작자(created_by)로 대체
    if not names and media_type == "tv":
        names = [p["name"] for p in details.get("created_by", [])]

    return ", ".join(names) if names else "정보 없음"


def extract_cast(details: dict, top_n: int = 5) -> str:
    cast = details.get("credits", {}).get("cast", [])
    names = [c["name"] for c in cast[:top_n]]
    return ", ".join(names) if names else "정보 없음"


def extract_keywords(details: dict, media_type: str, top_n: int = 8) -> str:
    block = details.get("keywords", {})
    # 영화는 keywords.keywords, 드라마는 keywords.results
    items = block.get("keywords", []) if media_type == "movie" else block.get("results", [])
    names = [k["name"] for k in items[:top_n]]
    return ", ".join(names) if names else "정보 없음"


async def attach_details(client: httpx.AsyncClient, candidates: list[dict]) -> list[dict]:
    """후보 전체에 director / cast / keywords를 붙인다. 이미 있는 값은 덮어쓰지 않는다."""

    all_details = await asyncio.gather(*(fetch_details(client, c) for c in candidates))

    for c, details in zip(candidates, all_details):
        media_type = c.get("media_type", "movie")
        c.setdefault("director", extract_director(details, media_type))
        c.setdefault("cast", extract_cast(details))
        c.setdefault("keywords", extract_keywords(details, media_type))

    return candidates


# ------------------------------------------------------------------
# Luxia LLM 호출
# ------------------------------------------------------------------
async def call_luxia(client: httpx.AsyncClient, system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("LUXIA_API_KEY")  # .env의 키 이름이 다르면 여기만 수정
    if not api_key:
        raise ValueError("LUXIA_API_KEY 환경변수가 설정되지 않았습니다.")

    response = await client.post(
        LUXIA_URL,
        headers={
            "apikey": api_key,
            "Content-Type": "application/json",
        },
        json={
            "model": LUXIA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


# ------------------------------------------------------------------
# 프롬프트 생성
# ------------------------------------------------------------------
def _base_info(candidate: dict, query: str) -> str:
    """세 에이전트 공통 입력"""
    overview = (candidate.get("overview") or "")[:200]
    return f"""
작품 제목: {candidate.get('title', '')}
줄거리: {overview}
TMDB 평점: {candidate.get('vote_average', 0)}
인기도: {float(candidate.get('popularity', 0) or 0):.1f}
사용자 요청: {query}
"""


def _agent_extra(candidate: dict, agent_type: str) -> str:
    """에이전트별로 참조하는 필드를 고정한다. (재현성 / 설명가능성 확보)"""
    if agent_type == "technical":
        return (
            f"감독: {candidate.get('director', '정보 없음')}\n"
            f"주요 출연진: {candidate.get('cast', '정보 없음')}\n"
        )
    if agent_type == "narrative":
        return f"키워드: {candidate.get('keywords', '정보 없음')}\n"
    if agent_type == "public":
        return (
            f"개봉 연도: {candidate.get('year', '정보 없음')}\n"
            f"투표 수: {candidate.get('vote_count', '정보 없음')}\n"
        )
    return ""


_ROLE_PROMPTS = {
    "technical": """너는 영화·드라마 기술적 완성도 평가 전문가야.
감독 역량, 촬영 기법, 제작 수준, 영상미 관점에서 0~10점으로 평가해줘.
reason은 반드시 3~4문장으로 써줘. 다음 내용을 포함해:
- 감독의 연출 스타일과 역량
- 촬영·영상미의 특징
- 제작 완성도가 사용자 요청과 어떻게 연결되는지""",
    "narrative": """너는 영화·드라마 서사 구조 평가 전문가야.
줄거리, 캐릭터 발전, 감성, 주제 의식 관점에서 0~10점으로 평가해줘.
reason은 반드시 3~4문장으로 써줘. 다음 내용을 포함해:
- 서사 구조와 전개 방식
- 캐릭터의 감성선과 성장
- 사용자 요청 키워드와 어떻게 맞닿는지""",
    "public": """너는 대중성 및 트렌드 평가 전문가야.
관객 반응, TMDB 평점 신뢰도, 인기도, 트렌드 부합 여부를 0~10점으로 평가해줘.
reason은 반드시 3~4문장으로 써줘. 다음 내용을 포함해:
- 평점과 관객 반응의 신뢰도
- 인기도 수준과 그 의미
- 현재 트렌드나 사용자 요청과의 부합 여부""",
}


def build_prompt(candidate: dict, query: str, agent_type: str) -> str:
    return (
        f"{_ROLE_PROMPTS[agent_type]}\n"
        "절대로 키는 score와 reason 두 개만. 다른 말 하지 마.\n"
        f"{_base_info(candidate, query)}"
        f"{_agent_extra(candidate, agent_type)}\n"
        '출력 형식:\n{"score": 8.5, "reason": "상세 설명 3~4문장"}\n'
    )


# ------------------------------------------------------------------
# 응답 파싱
# ------------------------------------------------------------------
def parse_agent_response(raw: str) -> dict:
    """LLM 응답에서 score / reason을 꺼낸다. 실패하면 FAIL_RESULT."""
    clean = raw.strip().replace("```json", "").replace("```", "").strip()

    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        # 앞뒤에 다른 말이 섞인 경우 { ... } 부분만 추출
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            return dict(FAIL_RESULT)
        try:
            result = json.loads(match.group())
        except json.JSONDecodeError:
            return dict(FAIL_RESULT)

    # 중첩 구조인 경우 score가 있는 dict를 꺼낸다
    if isinstance(result, dict) and "score" not in result:
        for v in result.values():
            if isinstance(v, dict) and "score" in v:
                result = v
                break

    if not isinstance(result, dict) or "score" not in result:
        return dict(FAIL_RESULT)

    try:
        score = float(result["score"])
    except (TypeError, ValueError):
        return dict(FAIL_RESULT)

    return {
        "score": max(0.0, min(10.0, score)),  # 0~10 범위로 보정
        "reason": str(result.get("reason", "-")),
    }


# ------------------------------------------------------------------
# 에이전트 1명 평가
# ------------------------------------------------------------------
async def score_candidate(
    client: httpx.AsyncClient,
    candidate: dict,
    query: str,
    agent_type: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    prompt = build_prompt(candidate, query, agent_type)

    try:
        async with semaphore:
            raw = await call_luxia(client, "너는 영화 평가 AI야.", prompt)
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

    # 1) 감독 / 출연진 / 키워드 붙이기
    await attach_details(client, candidates)

    # 2) 후보 x 에이전트 전체를 한 번에 병렬 호출 (동시 호출 수는 semaphore로 제한)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)

    tasks = [
        score_candidate(client, c, query, agent, semaphore)
        for c in candidates
        for agent in AGENT_TYPES
    ]
    results = await asyncio.gather(*tasks)

    # 3) 결과를 후보별로 정리하고 최종 점수 계산
    for i, c in enumerate(candidates):
        per_agent = dict(zip(AGENT_TYPES, results[i * 3:(i + 1) * 3]))

        c["technical"] = per_agent["technical"]
        c["narrative"] = per_agent["narrative"]
        c["public"] = per_agent["public"]
        c["final_score"] = calculate_final_score(
            {a: per_agent[a]["score"] for a in AGENT_TYPES},
            weights,
        )

    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)