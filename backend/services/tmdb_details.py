"""
tmdb_details.py
에이전트 평가용 TMDB 상세정보 조회 (credits, keywords, reviews 포함) 와 필드 추출 함수
"""

import asyncio
import os

import httpx

TMDB_BASE_URL = "https://api.themoviedb.org/3"
NO_DATA = "정보 없음"


def tmdb_headers() -> dict:
    token = os.getenv("TMDB_API_KEY")
    if not token:
        raise ValueError("TMDB_API_KEY 환경변수가 설정되지 않았습니다.")
    return {"Authorization": f"Bearer {token}", "accept": "application/json"}


async def fetch_details(client: httpx.AsyncClient, candidate: dict) -> dict:
    """TMDB 상세 API에서 credits, keywords, reviews를 함께 받아온다. 실패하면 빈 dict."""

    media_type = candidate.get("media_type", "movie")

    try:
        response = await client.get(
            f"{TMDB_BASE_URL}/{media_type}/{candidate['id']}",
            headers=tmdb_headers(),
            params={
                "language": "ko-KR",
                "append_to_response": "credits,keywords,reviews",
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

    return ", ".join(names) if names else NO_DATA


def extract_cast(details: dict, top_n: int = 5) -> str:
    cast = details.get("credits", {}).get("cast", [])
    names = [c["name"] for c in cast[:top_n]]
    return ", ".join(names) if names else NO_DATA


def extract_keywords(details: dict, media_type: str, top_n: int = 8) -> str:
    block = details.get("keywords", {})
    # 영화는 keywords.keywords, 드라마는 keywords.results
    items = block.get("keywords", []) if media_type == "movie" else block.get("results", [])
    names = [k["name"] for k in items[:top_n]]
    return ", ".join(names) if names else NO_DATA


def director_people(details: dict, media_type: str) -> list[dict]:
    """감독(드라마는 크리에이터)의 id와 이름 목록"""
    crew = details.get("credits", {}).get("crew", [])
    people = [{"id": c["id"], "name": c["name"]} for c in crew if c.get("job") == "Director" and c.get("id")]
    if not people and media_type == "tv":
        people = [{"id": p["id"], "name": p["name"]} for p in details.get("created_by", []) if p.get("id")]
    return people


async def attach_details(client: httpx.AsyncClient, candidates: list[dict]) -> list[dict]:
    """
    후보 전체의 TMDB 상세정보를 한 번에 받아온다.
    다른 서비스 호환을 위해 director / cast / keywords는 기존처럼 후보에 붙여 둔다.
    return: candidates와 같은 순서의 상세정보 리스트
    """
    all_details = await asyncio.gather(*(fetch_details(client, c) for c in candidates))

    for c, details in zip(candidates, all_details):
        media_type = c.get("media_type", "movie")
        c.setdefault("director", extract_director(details, media_type))
        c.setdefault("cast", extract_cast(details))
        c.setdefault("keywords", extract_keywords(details, media_type))

    return list(all_details)


async def fetch_person_crew(client: httpx.AsyncClient, person_id: int) -> list[dict]:
    response = await client.get(
        f"{TMDB_BASE_URL}/person/{person_id}/combined_credits",
        headers=tmdb_headers(),
        params={"language": "ko-KR"},
    )
    response.raise_for_status()
    return response.json().get("crew", [])