from pydantic import BaseModel, Field
import asyncio
import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from services.agent_service import evaluate_candidates
from services.candidate_service import select_candidates
from services.editor_service import build_editor_prompt
from services.embedding_service import rank_candidates_by_similarity
from services.llm_service import call_luxia
from services.tmdb_service import fetch_filtered_pool
from services.weight_service import infer_weights

load_dotenv(
    os.path.join(os.path.dirname(__file__), ".env"),
    override=True,
)

app = FastAPI(title="Cine Agent API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "https://cine-agent-eight.vercel.app",
    ],
    allow_origin_regex=r"^https://cine-agent-eight\.vercel\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
)

TMDB_BASE_URL = "https://api.themoviedb.org/3"


class RecommendRequest(BaseModel):
    ott: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)
    runtime: int = 120
    query: str = ""


@app.get("/")
def health_check():
    return {"message": "Cine Agent API is running"}


@app.get("/api/tmdb/search")
async def search_tmdb(
    query: str = Query(min_length=1),
    media: str = "movie",
):
    token = os.getenv("TMDB_API_KEY")

    if not token:
        raise HTTPException(
            status_code=500,
            detail="TMDB_API_KEY 환경변수가 설정되지 않았습니다.",
        )

    if media not in ("movie", "tv"):
        raise HTTPException(
            status_code=400,
            detail="media는 movie 또는 tv여야 합니다.",
        )

    endpoint = f"{TMDB_BASE_URL}/search/{media}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "accept": "application/json",
                },
                params={
                    "query": query,
                    "language": "ko-KR",
                    "region": "KR",
                    "page": 1,
                },
            )
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"TMDB API 오류: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="TMDB 서버에 연결하지 못했습니다.",
        ) from exc

    results = []

    for item in data.get("results", []):
        results.append({
            "id": item.get("id"),
            "title": item.get("title") or item.get("name"),
            "overview": item.get("overview", ""),
            "year": (
                item.get("release_date")
                or item.get("first_air_date")
                or ""
            )[:4],
            "poster_url": (
                f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
                if item.get("poster_path")
                else ""
            ),
            "vote_average": item.get("vote_average", 0),
        })

    return {"results": results}


# ------------------------------------------------------------------
# 추천 파이프라인
#   1) 후보군 수집 (TMDB 하드 필터)      tmdb_service.fetch_filtered_pool
#      + 가중치 추론 (동시에 실행)         weight_service.infer_weights
#   2) 검색어-줄거리 유사도 계산          embedding_service.rank_candidates_by_similarity
#   3) 후보 5개 선별                     candidate_service.select_candidates
#   4) 3개 에이전트 평가 + 최종 점수      agent_service.evaluate_candidates
#   5) Lead Editor 총평                  editor_service.build_editor_prompt
# ------------------------------------------------------------------
POOL_SIZE = 60          # 전체 후보군 크기 (매체가 여러 개면 나눠서 수집)
OTHERS_COUNT = 3        # best 외에 함께 보여줄 후보 수


def _resolve_media(request: RecommendRequest) -> list[tuple[str, list[str]]]:
    """
    프론트의 media 값을 (TMDB 매체, 장르 목록) 쌍으로 바꾼다.
    애니메이션은 영화 목록에서 '애니메이션' 장르로 찾는다.
    """
    media_list = [m for m in (request.media or []) if m in ("movie", "tv", "animation")]
    if not media_list:
        media_list = ["movie", "tv"]

    targets = []
    for media in media_list:
        if media == "animation":
            genres = list(dict.fromkeys([*request.genres, "애니메이션"]))
            targets.append(("movie", genres))
        else:
            targets.append((media, list(request.genres)))
    return targets


async def _fetch_pool(client: httpx.AsyncClient, request: RecommendRequest) -> list[dict]:
    """매체별로 후보를 모아 합친다. 장르 조건으로 비면 장르 조건을 빼고 한 번 더 찾는다."""
    targets = _resolve_media(request)
    per_media = max(POOL_SIZE // len(targets), 20)

    async def fetch_one(media_type: str, genres: list[str]) -> list[dict]:
        pool = await fetch_filtered_pool(
            client, media_type, genres, request.ott, request.runtime, target_count=per_media
        )
        if not pool and genres:
            print(f"[recommend] {media_type} 장르 조건 결과 없음 -> 장르 조건 완화 후 재검색")
            pool = await fetch_filtered_pool(
                client, media_type, [], request.ott, request.runtime, target_count=per_media
            )
        return pool

    results = await asyncio.gather(*(fetch_one(m, g) for m, g in targets))

    # 매체가 겹칠 수 있어 (매체, id) 기준으로 중복 제거
    merged = {}
    for pool in results:
        for item in pool:
            merged.setdefault((item["media_type"], item["id"]), item)
    return list(merged.values())


async def _write_editor_comment(client: httpx.AsyncClient, query: str, best: dict) -> str:
    prompt = build_editor_prompt(query, best, best["technical"], best["narrative"], best["public"])
    try:
        comment = await call_luxia(client, "너는 영화 추천 에디터야.", prompt, max_tokens=600)
        return comment.strip().strip('"')
    except Exception as e:
        print(f"[recommend] Lead Editor 총평 생성 실패: {e}")
        return ""


def _split_names(value) -> list[str]:
    if isinstance(value, list):
        return value
    if not value or value == "정보 없음":
        return []
    return [name.strip() for name in str(value).split(",") if name.strip()]


def _format_candidate(item: dict, weights: dict, editor_comment: str = "") -> dict:
    """프론트(Result.jsx)가 쓰는 응답 형식으로 변환한다. 점수는 0~10 기준."""
    director = item.get("director")
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "overview": item.get("overview", ""),
        "year": item.get("year", ""),
        "poster_url": item.get("poster_url", ""),
        "vote_average": item.get("vote_average", 0),
        "media_type": item.get("media_type"),
        "label": item.get("label", ""),                 # 후보 선별 기준 (평점·인기도 균형 등)
        "similarity": item.get("similarity", 0),
        "director": director if director and director != "정보 없음" else "",
        "cast": _split_names(item.get("cast")),
        "final_score": item.get("final_score", 0),
        "scores": {a: item[a]["score"] for a in ("technical", "narrative", "public")},
        "reasons": {a: item[a]["reason"] for a in ("technical", "narrative", "public")},
        "reason": editor_comment,
        "editor_comment": editor_comment,
        "weights": weights,
        "tool_usage": item.get("tool_usage", {}),       # 에이전트별 선택 도구와 근거 데이터
    }


@app.post("/api/recommend")
async def recommend(request: RecommendRequest):
    if not os.getenv("TMDB_API_KEY"):
        raise HTTPException(status_code=500, detail="TMDB_API_KEY 환경변수가 설정되지 않았습니다.")
    if not os.getenv("LUXIA_API_KEY"):
        raise HTTPException(status_code=500, detail="LUXIA_API_KEY 환경변수가 설정되지 않았습니다.")

    query = (request.query or "").strip()

    async with httpx.AsyncClient(timeout=60) as client:
        # 1) 후보군 수집 + 가중치 추론을 동시에 실행
        try:
            pool, weight_result = await asyncio.gather(
                _fetch_pool(client, request),
                infer_weights(client, query),
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"TMDB API 오류: {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="TMDB 서버에 연결하지 못했습니다.",
            ) from exc

        if not pool:
            raise HTTPException(
                status_code=404,
                detail="선택한 조건에 맞는 작품을 찾지 못했습니다. 조건을 바꿔서 다시 시도해 주세요.",
            )

        weights = weight_result["weights"]

        # 2) 유사도 계산 (임베딩 연산이 무거워 별도 스레드에서 실행)
        pool = await asyncio.to_thread(rank_candidates_by_similarity, query, pool)

        # 3) 후보 5개 선별
        candidates = select_candidates(pool, query)

        # 4) 에이전트 평가 + 가중치 적용 최종 점수
        ranked = await evaluate_candidates(client, candidates, query, weights)

        if not ranked:
            raise HTTPException(status_code=502, detail="에이전트 평가 결과가 없습니다.")

        # 5) Lead Editor 총평 (1위 작품만)
        best = ranked[0]
        editor_comment = await _write_editor_comment(client, query, best)

    return {
        "best": _format_candidate(best, weights, editor_comment),
        "others": [_format_candidate(item, weights) for item in ranked[1:1 + OTHERS_COUNT]],
        "weights": weights,
        "weight_analysis": {
            "importance": weight_result["importance"],   # LLM이 추론한 T/N/P 중요도 (0~5)
            "reason": weight_result["reason"],
            "status": weight_result["status"],
        },
    }