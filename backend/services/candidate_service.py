"""
candidate_service.py
후보군에서 서로 다른 기준으로 작품 5개를 고른다.

흐름
    1) 관련 후보군 만들기: 검색어가 있으면 유사도 상위 작품만 남긴다
       (예전에는 C2만 유사도를 봐서 검색어와 무관한 작품이 후보에 들어왔다)
    2) 관련 후보군 안에서 5가지 기준으로 1개씩 고른다

ko-sroberta-multitask는 유사도 값이 전반적으로 낮게 나와 고정 임계값이 불안정하므로,
절댓값 대신 상위 비율(RELEVANT_RATIO)로 자른다.
"""

import math
from datetime import datetime

CANDIDATE_LABELS = [
    "평점·인기도 균형",
    "검색어 유사도",
    "최신작",
    "숨은 명작",
    "의외의 추천",
]

RELEVANT_RATIO = 0.35      # 유사도 상위 몇 %를 관련 후보로 볼지 (60개 기준 21개)
RELEVANT_MIN = 10          # 관련 후보군 최소 크기
MIN_VOTES_FOR_RATING = 30  # 모든 기준에 적용하는 최소 투표 수 (투표 2~3개짜리 작품 방지)
RECENT_YEARS = 2

# 의외의 추천 점수 = 유사도(관련 후보군 안에서 0~1 정규화) * W_SIM + 장르 참신성 * W_NOVELTY
# 예전에는 장르 참신성만 봐서, 힐링 쿼리에 불륜·성인 로맨스물이 '의외의 추천'으로 뽑혔다
UNEXPECTED_W_SIM = 0.6
UNEXPECTED_W_NOVELTY = 0.4


def _enrich(movie: dict, now_year: int) -> dict:
    item = dict(movie)

    release = item.get("release_date") or item.get("first_air_date") or ""
    # TMDB 서비스에서 year만 제공한 경우도 처리
    if not release:
        release = str(item.get("year") or "")

    try:
        item["years_ago"] = now_year - int(release[:4])
    except (ValueError, TypeError):
        item["years_ago"] = 99

    item["vote_average"] = float(item.get("vote_average") or 0)
    item["vote_count"] = int(item.get("vote_count") or 0)
    item["popularity"] = float(item.get("popularity") or 0)
    item["similarity"] = float(item.get("similarity") or 0)
    item["genre_ids"] = list(item.get("genre_ids") or [])
    return item


def relevant_pool(enriched: list[dict], query: str) -> list[dict]:
    """검색어가 있으면 줄거리가 있는 작품 중 유사도 상위만 남긴다."""
    if not (query or "").strip() or not any(x["similarity"] > 0 for x in enriched):
        return enriched

    with_text = [x for x in enriched if (x.get("overview") or "").strip()]
    ranked = sorted(with_text, key=lambda x: x["similarity"], reverse=True)
    size = max(RELEVANT_MIN, math.ceil(len(enriched) * RELEVANT_RATIO))
    return ranked[:size] or enriched


def _trusted(items: list[dict]) -> list[dict]:
    """투표 수가 충분한 작품만. 하나도 없으면 원래 목록."""
    trusted = [x for x in items if x["vote_count"] >= MIN_VOTES_FOR_RATING]
    return trusted or items


def select_candidates(
    pool: list[dict],
    query: str = "",
) -> list[dict]:
    """후보군에서 서로 다른 기준으로 작품 5개를 선정한다."""

    if not pool:
        return []

    now_year = datetime.now().year
    enriched = [_enrich(m, now_year) for m in pool]
    relevant = relevant_pool(enriched, query)

    used_ids = set()
    picked_genres: set[int] = set()

    def pick(ranked: list[dict]):
        for movie in ranked:
            movie_id = movie.get("id")

            if movie_id is not None and movie_id not in used_ids:
                used_ids.add(movie_id)
                picked_genres.update(movie["genre_ids"])
                return dict(movie)

        return None

    # C1: 평점과 인기도의 균형
    c1 = pick(sorted(
        _trusted(relevant),
        key=lambda x: (
            x["vote_average"] * 0.6
            + min(x["popularity"] / 100, 10) * 0.4
        ),
        reverse=True,
    ))

    # C2: 검색어와 줄거리의 의미 유사도
    c2 = pick(sorted(
        _trusted(relevant),
        key=lambda x: x["similarity"],
        reverse=True,
    ))

    # C3: 최근 작품을 우선 선정 (없으면 관련 후보 중 가장 최신)
    recent = [x for x in _trusted(relevant) if x["years_ago"] <= RECENT_YEARS]
    c3 = pick(sorted(
        recent,
        key=lambda x: (
            x["vote_average"] * 0.5
            + min(x["popularity"] / 100, 10) * 0.5
        ),
        reverse=True,
    ))

    if c3 is None:
        c3 = pick(sorted(_trusted(relevant), key=lambda x: x["years_ago"]))

    # C4: 평점은 높고 인기도는 상대적으로 낮은 작품 (투표 수가 충분한 작품만)
    c4 = pick(sorted(
        _trusted(relevant),
        key=lambda x: (
            x["vote_average"] * 0.7
            - min(x["popularity"] / 100, 10) * 0.3
        ),
        reverse=True,
    ))

    # C5: 의외의 추천. 검색어와의 유사도를 유지하면서, 앞서 고른 작품들과
    #     장르가 덜 겹치는 작품을 고른다 (유사도와 장르 참신성을 함께 점수화).
    c5_pool = _trusted(relevant)
    sims = [x["similarity"] for x in c5_pool]
    sim_min, sim_max = min(sims), max(sims)

    def sim_norm(x: dict) -> float:
        if sim_max <= sim_min:
            return 1.0
        return (x["similarity"] - sim_min) / (sim_max - sim_min)

    def genre_novelty(x: dict) -> float:
        if not x["genre_ids"]:
            return 0.0
        return 1 - len(set(x["genre_ids"]) & picked_genres) / len(x["genre_ids"])

    c5 = pick(sorted(
        c5_pool,
        key=lambda x: (
            sim_norm(x) * UNEXPECTED_W_SIM
            + genre_novelty(x) * UNEXPECTED_W_NOVELTY,
            x["vote_average"],
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
