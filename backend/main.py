
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
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

TMDB_BASE_URL = "https://api.themoviedb.org/3"

LUXIA_CHAT_URL = "https://bridge.luxiacloud.com/luxia/v1/chat"
LUXIA_MODEL = "luxia3-llm-8b-0731"


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

@app.get("/api/luxia/test")
async def test_luxia():
    answer = await call_luxia("대한민국의 수도는 어디인가요?")
    return {"answer": answer}
