
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

# 화면에서 고르는 장르 이름 -> TMDB 장르 번호
# 영화와 TV는 장르 번호 체계가 다르다 (예: 액션은 영화 28, TV는 10759 'Action & Adventure').
# TV에 없는 장르(공포·로맨스·스릴러)는 None -> 그 장르 조건만 빼고 검색한다.
MOVIE_GENRES = {
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

TV_GENRES = {
    "액션": 10759,
    "모험": 10759,
    "애니메이션": 16,
    "코미디": 35,
    "범죄": 80,
    "다큐멘터리": 99,
    "드라마": 18,
    "가족": 10751,
    "판타지": 10765,
    "공포": None,
    "미스터리": 9648,
    "로맨스": None,
    "SF": 10765,
    "스릴러": None,
    "전쟁": 10768,
    "서부": 37,
}

GENRE_MAP = MOVIE_GENRES   # 예전 이름 호환용 (화면의 장르 목록 = 이 키 목록)

# TV 후보에서 항상 빼는 장르: 리얼리티(예능), 토크쇼, 뉴스
TV_EXCLUDED_GENRES = [10764, 10767, 10763]


def genre_ids(names: list[str], media_type: str) -> list[int]:
    """장르 이름 -> 매체에 맞는 TMDB 장르 번호 (없는 장르는 빼고, 중복 제거)"""
    table = TV_GENRES if media_type == "tv" else MOVIE_GENRES
    ids = [table.get(n) for n in names]
    return list(dict.fromkeys(i for i in ids if i))


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
    required_genres: list[str] | None = None,
    exclude_genres: list[str] | None = None,
) -> list[dict]:
    """
    사용자 조건으로 TMDB 후보를 필터링해 수집한다.

    genres          : 이 중 하나라도 해당하면 된다 (OR). 화면에서 여러 장르를 고른 경우
    required_genres : 반드시 해당해야 한다 (AND). 예: 애니메이션 매체를 고른 경우 '애니메이션'
    exclude_genres  : 하나라도 해당하면 뺀다. 예: 힐링 요청이면 범죄·공포
    TMDB는 AND와 OR을 한 조건에 섞을 수 없어서, 둘 다 있으면 required는 API로 걸고
    genres는 받아온 결과에서 거른다.
    """
    any_ids = genre_ids(genres, media_type)
    all_ids = genre_ids(required_genres or [], media_type)
    # 포함·필수 장르와 겹치는 제외 장르는 쓰지 않는다 (TV는 액션·모험처럼 한 번호를 나눠 쓰기도 한다)
    none_ids = [i for i in genre_ids(exclude_genres or [], media_type) if i not in any_ids + all_ids]

    provider_ids = [
        OTT_MAP[p]
        for p in ott
        if p in OTT_MAP
    ]

    params = {
        "language": "ko-KR",   # 제목·줄거리를 한국어로 받기 (임베딩 모델이 한국어 전용)
        "sort_by": "popularity.desc",
        "page": 1,
    }

    if all_ids:
        params["with_genres"] = ",".join(map(str, all_ids))      # 쉼표 = 모두 포함 (AND)
        post_filter = set(any_ids)
    else:
        if any_ids:
            params["with_genres"] = "|".join(map(str, any_ids))  # 파이프 = 하나라도 포함 (OR)
        post_filter = set()

    if media_type == "tv":
        none_ids = list(dict.fromkeys([*TV_EXCLUDED_GENRES, *none_ids]))
    if none_ids:
        params["without_genres"] = ",".join(map(str, none_ids))

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

            if post_filter and not post_filter & set(item.get("genre_ids") or []):
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
                "genre_ids": item.get("genre_ids", []),
            })

            if len(candidates) >= target_count:
                return candidates[:target_count]

    return candidates
