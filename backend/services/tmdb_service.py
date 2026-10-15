
import os
import httpx

TMDB_BASE_URL = "https://api.themoviedb.org/3"


async def fetch_tmdb_candidates(
    client: httpx.AsyncClient,
    media_type: str,
    params: dict,
) -> list[dict]:
    """TMDB에서 작품 후보 한 페이지를 가져온다."""

    token = os.getenv("TMDB_API_KEY")

    if not token:
        raise ValueError("TMDB_API_KEY 환경변수가 설정되지 않았습니다.")

    if media_type not in ("movie", "tv"):
        raise ValueError("media_type은 movie 또는 tv여야 합니다.")

    response = await client.get(
        f"{TMDB_BASE_URL}/discover/{media_type}",
        headers={
            "Authorization": f"Bearer {token}",
            "accept": "application/json",
        },
        params={
            "language": "ko-KR",
            "region": "KR",
            **params,
        },
    )

    response.raise_for_status()

    return response.json().get("results", [])

GENRE_MAP = {
    "액션": 28,
    "모험": 12,
    "애니메이션": 16,
    "코미디": 35,
    "범죄": 80,
    "다큐멘터리": 99,
    "드라마": 18,
    "가족": 10751,
    "판타지": 14,
    "공포": 27,
    "미스터리": 9648,
    "로맨스": 10749,
    "SF": 878,
    "스릴러": 53,
    "전쟁": 10752,
    "서부": 37,
}

OTT_MAP = {
    "넷플릭스": 8,
    "웨이브": 356,
    "왓챠": 100,
    "디즈니+": 337,
    "애플TV+": 350,
    "쿠팡플레이": 464,
    "티빙": 1796,
}


async def fetch_filtered_pool(
    client: httpx.AsyncClient,
    media_type: str,
    genres: list[str],
    ott: list[str],
    runtime: int,
    target_count: int = 60,
) -> list[dict]:
    """사용자 조건으로 TMDB 후보를 필터링해 수집한다."""

    genre_ids = [
        GENRE_MAP[g]
        for g in genres
        if g in GENRE_MAP
    ]

    provider_ids = [
        OTT_MAP[p]
        for p in ott
        if p in OTT_MAP
    ]

    params = {
        "sort_by": "popularity.desc",
        "page": 1,
    }

    if genre_ids:
        params["with_genres"] = ",".join(map(str, genre_ids))

    if provider_ids:
        params["with_watch_providers"] = "|".join(
            map(str, provider_ids)
        )
        params["watch_region"] = "KR"

    if media_type == "movie":
        params["with_runtime.lte"] = runtime

    candidates = []
    seen_ids = set()
    token = os.getenv("TMDB_API_KEY")

    if not token:
        raise ValueError("TMDB_API_KEY 환경변수가 설정되지 않았습니다.")

    for page in range(1, 6):
        params["page"] = page

        response = await client.get(
            f"{TMDB_BASE_URL}/discover/{media_type}",
            headers={
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
            },
            params=params,
        )
        response.raise_for_status()

        for item in response.json().get("results", []):
            item_id = item.get("id")

            if not item_id or item_id in seen_ids:
                continue

            # 영화 러닝타임은 TMDB 상세정보에서 확인해야 하므로
            # discover 단계에서는 TMDB의 runtime 조건을 우선 사용한다.
            seen_ids.add(item_id)

            candidates.append({
                "id": item_id,
                "title": item.get("title") or item.get("name"),
                "overview": item.get("overview") or "",
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
                "vote_count": item.get("vote_count", 0),
                "popularity": item.get("popularity", 0),
                "media_type": media_type,
                
            })

            if len(candidates) >= target_count:
                return candidates[:target_count]

    return candidates
