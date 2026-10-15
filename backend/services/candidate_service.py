
from datetime import datetime


CANDIDATE_LABELS = [
    "평점·인기도 균형",
    "검색어 유사도",
    "최신작",
    "숨은 명작",
    "의외의 추천",
]


def select_candidates(
    pool: list[dict],
    query: str = "",
) -> list[dict]:
    """후보군에서 서로 다른 기준으로 작품 5개를 선정한다."""

    if not pool:
        return []

    now_year = datetime.now().year
    enriched = []

    for movie in pool:
        item = dict(movie)

        release = (
            item.get("release_date")
            or item.get("first_air_date")
            or ""
        )

        # TMDB 서비스에서 year만 제공한 경우도 처리
        if not release:
            release = str(item.get("year") or "")

        try:
            years_ago = now_year - int(release[:4])
        except (ValueError, TypeError):
            years_ago = 99

        item["years_ago"] = years_ago
        item["vote_average"] = float(item.get("vote_average") or 0)
        item["vote_count"] = int(item.get("vote_count") or 0)
        item["popularity"] = float(item.get("popularity") or 0)
        item["similarity"] = float(item.get("similarity") or 0)

        enriched.append(item)

    used_ids = set()

    def pick(ranked: list[dict]):
        for movie in ranked:
            movie_id = movie.get("id")

            if movie_id is not None and movie_id not in used_ids:
                used_ids.add(movie_id)
                return dict(movie)

        return None

    # C1: 평점과 인기도의 균형
    c1 = pick(sorted(
        enriched,
        key=lambda x: (
            x["vote_average"] * 0.6
            + min(x["popularity"] / 100, 10) * 0.4
        ),
        reverse=True,
    ))

    # C2: 검색어와 줄거리의 의미 유사도
    c2 = pick(sorted(
        enriched,
        key=lambda x: x["similarity"],
        reverse=True,
    ))

    # C3: 최근 2년 이내 작품을 우선 선정
    recent = [x for x in enriched if x["years_ago"] <= 2]
    c3 = pick(sorted(
        recent,
        key=lambda x: (
            x["vote_average"] * 0.5
            + min(x["popularity"] / 100, 10) * 0.5
        ),
        reverse=True,
    ))

    if c3 is None:
        c3 = pick(sorted(
            enriched,
            key=lambda x: x["years_ago"],
        ))

    # C4: 평점은 높고 인기도는 상대적으로 낮은 작품
    c4 = pick(sorted(
        enriched,
        key=lambda x: (
            x["vote_average"] * 0.7
            - min(x["popularity"] / 100, 10) * 0.3
        ),
        reverse=True,
    ))

    # C5: 검색어와 다른 매력을 가진 의외의 작품
    c5 = pick(sorted(
        enriched,
        key=lambda x: (
            x["vote_average"] * 0.5
            - x["similarity"] * 5
        ),
        reverse=True,
    ))

    result = []

    for label, movie in zip(
        CANDIDATE_LABELS,
        [c1, c2, c3, c4, c5],
    ):
        if movie:
            movie["label"] = label
            result.append(movie)

    return result
