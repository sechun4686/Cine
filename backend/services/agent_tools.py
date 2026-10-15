"""
agent_tools.py
에이전트가 사용할 수 있는 TMDB 도구 정의 / 검증 / 실행

    TOOL_REGISTRY  : 에이전트별 허용 도구 목록 (화이트리스트)
    validate_tools : LLM이 고른 도구를 코드에서 다시 검증
    run_tools      : 선택된 도구만 실행 (실패 시 "정보 없음")

도구 종류
    extract : 이미 받아온 TMDB 상세정보에서 필드만 꺼낸다 (추가 API 호출 없음)
    call    : TMDB 엔드포인트를 따로 호출한다 (timeout / 예외 처리 적용)
"""

import asyncio

import httpx

from .llm_service import extract_json
from .tmdb_details import (
    NO_DATA,
    director_people,
    extract_cast,
    extract_director,
    extract_keywords,
    fetch_person_crew,
)

MIN_TOOLS = 2
MAX_TOOLS = 4
TOOL_TIMEOUT = 10            # 도구 1개 실행 제한 시간(초)
FILMOGRAPHY_MIN_VOTES = 50   # 필모그래피 평균에 포함할 최소 투표 수


# ------------------------------------------------------------------
# 도구 (Tool) 정의
# ------------------------------------------------------------------
class ToolContext:
    """도구 함수가 공통으로 받는 입력"""

    def __init__(self, client: httpx.AsyncClient, candidate: dict, details: dict, person_cache: dict):
        self.client = client
        self.candidate = candidate
        self.details = details or {}
        self.person_cache = person_cache  # person_id -> asyncio.Task (같은 감독 중복 호출 방지)

    @property
    def media_type(self) -> str:
        return self.candidate.get("media_type", "movie")


# ---------- Technical ----------
async def tool_director(ctx: ToolContext) -> str:
    return extract_director(ctx.details, ctx.media_type)


async def tool_cast(ctx: ToolContext) -> str:
    return extract_cast(ctx.details)


async def tool_production_companies(ctx: ToolContext) -> str:
    companies = ctx.details.get("production_companies", [])[:4]
    names = [
        f"{c['name']}({c['origin_country']})" if c.get("origin_country") else c["name"]
        for c in companies if c.get("name")
    ]
    return ", ".join(names) if names else NO_DATA


async def tool_budget_revenue(ctx: ToolContext) -> str:
    if ctx.media_type != "movie":
        return NO_DATA  # TMDB는 드라마의 제작비/수익을 제공하지 않음

    budget = ctx.details.get("budget") or 0
    revenue = ctx.details.get("revenue") or 0
    parts = []
    if budget > 0:
        parts.append(f"제작비 ${budget:,}")
    if revenue > 0:
        parts.append(f"수익 ${revenue:,}")
    return " / ".join(parts) if parts else NO_DATA


async def tool_runtime(ctx: ToolContext) -> str:
    if ctx.media_type == "movie":
        runtime = ctx.details.get("runtime")
        return f"{runtime}분" if runtime else NO_DATA

    episode_times = ctx.details.get("episode_run_time") or []
    seasons = ctx.details.get("number_of_seasons")
    episodes = ctx.details.get("number_of_episodes")
    parts = []
    if episode_times:
        parts.append(f"회당 약 {episode_times[0]}분")
    if seasons:
        parts.append(f"{seasons}개 시즌")
    if episodes:
        parts.append(f"총 {episodes}화")
    return ", ".join(parts) if parts else NO_DATA


async def tool_director_filmography(ctx: ToolContext) -> str:
    """[call] 감독의 다른 연출작 평균 평점 (현재 작품 제외, 투표 수 50 이상만)"""
    people = director_people(ctx.details, ctx.media_type)
    if not people:
        return NO_DATA

    person = people[0]  # 공동 감독이면 첫 번째 감독 기준

    if person["id"] not in ctx.person_cache:
        ctx.person_cache[person["id"]] = asyncio.ensure_future(fetch_person_crew(ctx.client, person["id"]))
    crew = await ctx.person_cache[person["id"]]

    works = {}
    for w in crew:
        if w.get("job") not in ("Director", "Creator"):
            continue
        if w.get("id") == ctx.candidate.get("id") or w.get("id") in works:
            continue
        if (w.get("vote_count") or 0) < FILMOGRAPHY_MIN_VOTES:
            continue
        works[w["id"]] = w

    if not works:
        return f"{person['name']}의 다른 연출작 중 평가 가능한 작품 없음"

    ratings = [w.get("vote_average") or 0 for w in works.values()]
    avg = sum(ratings) / len(ratings)
    top = sorted(works.values(), key=lambda w: w.get("vote_count") or 0, reverse=True)[:3]
    top_str = ", ".join(
        f"{w.get('title') or w.get('name')}({(w.get('vote_average') or 0):.1f})" for w in top
    )
    return f"{person['name']} 연출작 {len(works)}편 평균 평점 {avg:.1f} (대표작: {top_str})"


# ---------- Narrative ----------
async def tool_overview_full(ctx: ToolContext) -> str:
    return ctx.details.get("overview") or ctx.candidate.get("overview") or NO_DATA


async def tool_keywords(ctx: ToolContext) -> str:
    return extract_keywords(ctx.details, ctx.media_type)


async def tool_tagline(ctx: ToolContext) -> str:
    return ctx.details.get("tagline") or NO_DATA


async def tool_genres(ctx: ToolContext) -> str:
    names = [g["name"] for g in ctx.details.get("genres", []) if g.get("name")]
    return ", ".join(names) if names else NO_DATA


# ---------- Public ----------
async def tool_vote_stats(ctx: ToolContext) -> str:
    avg = ctx.details.get("vote_average", ctx.candidate.get("vote_average"))
    count = ctx.details.get("vote_count", ctx.candidate.get("vote_count"))
    if avg is None and count is None:
        return NO_DATA
    return f"평점 {float(avg or 0):.1f} / 투표 수 {int(count or 0):,}"


async def tool_popularity(ctx: ToolContext) -> str:
    pop = ctx.details.get("popularity", ctx.candidate.get("popularity"))
    return f"{float(pop):.1f}" if pop is not None else NO_DATA


async def tool_release_year(ctx: ToolContext) -> str:
    date = ctx.details.get("release_date") or ctx.details.get("first_air_date") or ""
    year = date[:4] or ctx.candidate.get("year")
    return str(year) if year else NO_DATA


async def tool_reviews(ctx: ToolContext) -> str:
    # 참고: 상세정보를 ko-KR로 받기 때문에 한국어 리뷰가 없으면 비어 있을 수 있다
    reviews = ctx.details.get("reviews", {}).get("results", [])[:3]
    lines = []
    for r in reviews:
        content = " ".join((r.get("content") or "").split())[:150]
        rating = (r.get("author_details") or {}).get("rating")
        prefix = f"[{rating}/10] " if rating is not None else ""
        if content:
            lines.append(f"{prefix}{content}")
    return " | ".join(lines) if lines else NO_DATA


# ---------- 도구 목록 (에이전트별 화이트리스트) ----------
TOOL_REGISTRY = {
    "technical": {
        "director": {"label": "감독", "type": "extract", "func": tool_director,
                     "description": "감독 이름 (드라마는 크리에이터)"},
        "cast": {"label": "주요 출연진", "type": "extract", "func": tool_cast,
                 "description": "주요 출연진 상위 5명"},
        "production_companies": {"label": "제작사", "type": "extract", "func": tool_production_companies,
                                 "description": "제작사 이름과 국가"},
        "budget_revenue": {"label": "제작비/수익", "type": "extract", "func": tool_budget_revenue,
                           "description": "제작비와 흥행 수익 (영화만 제공)"},
        "runtime": {"label": "상영 시간", "type": "extract", "func": tool_runtime,
                    "description": "영화 상영 시간 또는 드라마 회당 시간·시즌 수"},
        "director_filmography": {"label": "감독 필모그래피", "type": "call", "func": tool_director_filmography,
                                 "description": "감독의 다른 연출작 평균 평점과 대표작"},
    },
    "narrative": {
        "overview_full": {"label": "전체 줄거리", "type": "extract", "func": tool_overview_full,
                          "description": "잘리지 않은 전체 줄거리"},
        "keywords": {"label": "키워드", "type": "extract", "func": tool_keywords,
                     "description": "TMDB 키워드 (소재·주제·배경)"},
        "tagline": {"label": "태그라인", "type": "extract", "func": tool_tagline,
                    "description": "홍보 문구 한 줄"},
        "genres": {"label": "장르", "type": "extract", "func": tool_genres,
                   "description": "작품의 전체 장르 목록"},
    },
    "public": {
        "vote_stats": {"label": "평점/투표 수", "type": "extract", "func": tool_vote_stats,
                       "description": "TMDB 평균 평점과 투표 수"},
        "popularity": {"label": "인기도", "type": "extract", "func": tool_popularity,
                       "description": "TMDB 인기도 지수"},
        "release_year": {"label": "개봉 연도", "type": "extract", "func": tool_release_year,
                         "description": "개봉 또는 첫 방영 연도"},
        "reviews": {"label": "관객 리뷰", "type": "extract", "func": tool_reviews,
                    "description": "TMDB 관객 리뷰 일부 (없을 수 있음)"},
    },
}

# 항상 포함되는 도구 (평가 관점의 핵심 데이터)
REQUIRED_TOOLS = {
    "technical": ["director"],
    "narrative": ["overview_full"],
    "public": ["vote_stats"],
}

# 선택이 실패했을 때 쓰는 기본 도구 (기존 고정 필드 구성과 동일한 범위)
DEFAULT_TOOLS = {
    "technical": ["director", "cast"],
    "narrative": ["overview_full", "keywords"],
    "public": ["vote_stats", "release_year"],
}


# ------------------------------------------------------------------
# 2) 선택 결과 검증
# ------------------------------------------------------------------
def validate_tools(agent_type: str, raw: str | None) -> dict:
    """
    LLM이 고른 도구를 코드에서 다시 검증한다.
    status
        ok       : LLM 선택을 그대로 사용
        filtered : 목록 외 이름 제거 / 개수 조정 / 필수 도구 추가 등 일부 수정
        fallback : 해석 실패로 기본 도구 사용
    """
    allowed = TOOL_REGISTRY[agent_type]
    parsed = extract_json(raw) if raw else None

    if not parsed or not isinstance(parsed.get("tools"), list):
        return {
            "tools": list(DEFAULT_TOOLS[agent_type]),
            "reason": "도구 선택 결과를 해석하지 못해 기본 도구를 사용함",
            "status": "fallback",
            "rejected": [],
        }

    selected, rejected = [], []
    for name in parsed["tools"]:
        name = str(name).strip()
        if name not in allowed:
            rejected.append(name)
        elif name not in selected:
            selected.append(name)

    original = list(selected)

    # 필수 도구는 맨 앞에 포함
    for req in reversed(REQUIRED_TOOLS[agent_type]):
        if req in selected:
            selected.remove(req)
        selected.insert(0, req)

    # 최대 개수 제한
    selected = selected[:MAX_TOOLS]

    # 최소 개수 미달이면 기본 도구로 채움
    for name in DEFAULT_TOOLS[agent_type]:
        if len(selected) >= MIN_TOOLS:
            break
        if name not in selected:
            selected.append(name)

    changed = bool(rejected) or set(selected) != set(original)

    return {
        "tools": selected,
        "reason": str(parsed.get("reason", "")).strip() or "-",
        "status": "filtered" if changed else "ok",
        "rejected": rejected,
    }


# ------------------------------------------------------------------
# 3) 도구 실행
# ------------------------------------------------------------------
async def run_tools(ctx: ToolContext, agent_type: str, tool_names: list[str]) -> tuple[dict, list[str]]:
    """선택된 도구만 실행한다. return: ({도구명: 값}, 실패한 도구 목록)"""
    registry = TOOL_REGISTRY[agent_type]

    async def run_one(name: str):
        try:
            value = await asyncio.wait_for(registry[name]["func"](ctx), timeout=TOOL_TIMEOUT)
            return name, (value or NO_DATA), True
        except Exception as e:
            print(f"[agent_service] 도구 실행 실패 {agent_type}.{name} ({ctx.candidate.get('title')}): {e!r}")
            return name, NO_DATA, False

    results = await asyncio.gather(*(run_one(n) for n in tool_names))
    data = {name: value for name, value, _ in results}
    failed = [name for name, _, ok in results if not ok]
    return data, failed