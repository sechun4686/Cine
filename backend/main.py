from pydantic import BaseModel, Field
import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


TMDB_BASE_URL = "https://api.themoviedb.org/3"

LUXIA_CHAT_URL = "https://bridge.luxiacloud.com/luxia/v1/chat"
LUXIA_MODEL = "luxia3-llm-8b-0731"
class RecommendRequest(BaseModel):
    ott: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)
    runtime: int = 120
    query: str = ""

async def call_luxia(prompt: str) -> str:
    api_key = os.getenv("LUXIA_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="LUXIA_API_KEY 환경변수가 설정되지 않았습니다.",
        )

    payload = {
        "model": LUXIA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
        "temperature": 0,
        "max_completion_tokens": 2048,
        "top_p": 1,
        "frequency_penalty": 0.1,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                LUXIA_CHAT_URL,
                headers={
                    "apikey": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Luxia API 오류: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Luxia 서버에 연결하지 못했습니다.",
        ) from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Luxia 응답 형식이 예상과 다릅니다.",
        ) from exc


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

@app.post("/api/recommend")
async def recommend(request: RecommendRequest):
    token = os.getenv("TMDB_API_KEY")

    if not token:
        raise HTTPException(
            status_code=500,
            detail="TMDB_API_KEY 환경변수가 설정되지 않았습니다.",
        )

    media_types = request.media or ["movie", "tv"]
    media_types = [
        media for media in media_types
        if media in ("movie", "tv", "animation")
    ]

    if not media_types:
        media_types = ["movie", "tv"]

    candidates = []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for media in media_types:
                # 애니메이션은 우선 영화 목록에서 후보를 찾음
                endpoint_media = "movie" if media == "animation" else media

                response = await client.get(
                    f"{TMDB_BASE_URL}/discover/{endpoint_media}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "accept": "application/json",
                    },
                    params={
                        "language": "ko-KR",
                        "region": "KR",
                        "sort_by": "popularity.desc",
                        "page": 1,
                    },
                )
                response.raise_for_status()

                for item in response.json().get("results", []):
                    candidates.append({
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
                        "media_type": endpoint_media,
                    })

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

    # 중복 제거 후 후보가 없으면 오류 반환
    unique_candidates = {
        (item["media_type"], item["id"]): item
        for item in candidates
    }
    candidates = list(unique_candidates.values())[:30]

    if not candidates:
        raise HTTPException(
            status_code=502,
            detail="TMDB에서 추천 후보를 찾지 못했습니다.",
        )

    prompt = f"""
너는 영화와 TV 작품을 추천하는 AI 에이전트다.
사용자 조건을 참고하고 아래 후보 중 가장 적합한 작품 하나를 골라라.

사용자 조건:
- OTT: {request.ott}
- 장르: {request.genres}
- 매체: {request.media}
- 희망 러닝타임(분): {request.runtime}
- 원하는 분위기나 키워드: {request.query}

작품 후보(JSON):
{__import__("json").dumps(candidates, ensure_ascii=False)}

반드시 JSON 객체만 반환해라. 마크다운 코드블록은 쓰지 마라.
형식:
{{
  "best_id": 후보 작품의 id 숫자,
  "technical": 0부터 100 사이 점수,
  "narrative": 0부터 100 사이 점수,
  "public": 0부터 100 사이 점수,
  "reason": "한국어 추천 이유",
  "others": [다른 후보 id 숫자 최대 3개]
}}
"""

    import json
    import re

    luxia_text = await call_luxia(prompt)

    try:
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            luxia_text.strip(),
            flags=re.IGNORECASE,
        )
        result = json.loads(cleaned)
        best_id = int(result["best_id"])
        best_candidate = next(
            item for item in candidates
            if item["id"] == best_id
        )
    except (ValueError, TypeError, KeyError, StopIteration, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Luxia 추천 결과를 해석하지 못했습니다. 다시 시도해 주세요.",
        ) from exc

    technical = max(0, min(100, int(result.get("technical", 70))))
    narrative = max(0, min(100, int(result.get("narrative", 70))))
    public = max(0, min(100, int(result.get("public", 70))))

    def format_candidate(item, is_best=False):
        return {
            **item,
            "director": "정보 없음",
            "cast": [],
            "final_score": round(
                (technical + narrative + public) / 3
            ) if is_best else round(
                (item.get("vote_average", 0) or 0) * 10
            ),
            "scores": {
                "technical": technical if is_best else 70,
                "narrative": narrative if is_best else 70,
                "public": public if is_best else round(
                    (item.get("vote_average", 0) or 0) * 10
                ),
            },
            "reasons": {
                "technical": "작품의 전반적인 완성도를 고려했습니다.",
                "narrative": result.get("reason", "사용자 조건을 고려했습니다.")
                if is_best else "추천 후보 작품입니다.",
                "public": "평점과 대중성을 참고했습니다.",
            },
            "reason": result.get("reason", "") if is_best else "",
            "weights": {
                "technical": 0.33,
                "narrative": 0.34,
                "public": 0.33,
            },
            "editor_comment": result.get("reason", "") if is_best else "",
        }

    others_ids = result.get("others", [])
    others = [
        format_candidate(item)
        for item in candidates
        if item["id"] in others_ids and item["id"] != best_id
    ][:3]

    return {
        "best": format_candidate(best_candidate, is_best=True),
        "others": others,
        "weights": {
            "technical": 0.33,
            "narrative": 0.34,
            "public": 0.33,
        },
    }

