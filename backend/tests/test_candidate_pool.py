"""후보군 수집 개선: TV 장르 번호, 장르 OR 검색, 예능 제외, Query Structuring(포함/제외 장르), 유사도 반영 후보 선정"""
import asyncio
import sys
import types

import httpx
import pytest

sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))

import main  # noqa: E402
from services import query_service, tmdb_service  # noqa: E402
from services.candidate_service import rank_with_relevance, select_candidates  # noqa: E402


# ------------------------------------------------------------------
# tmdb_service
# ------------------------------------------------------------------
def test_genre_ids_use_tv_numbers_and_drop_missing():
    assert tmdb_service.genre_ids(["액션", "SF", "드라마"], "movie") == [28, 878, 18]
    assert tmdb_service.genre_ids(["액션", "모험", "SF", "판타지", "스릴러"], "tv") == [10759, 10765]
    assert tmdb_service.genre_ids(["없는장르"], "movie") == []


def _discover(monkeypatch, results):
    """discover 호출 파라미터를 기록하는 가짜 TMDB"""
    monkeypatch.setenv("TMDB_API_KEY", "t")
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"results": results if page == 1 else []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _item(i, genres):
    return {"id": i, "title": f"t{i}", "overview": "o", "genre_ids": genres}


async def test_multiple_genres_are_or_and_tv_excludes_variety(monkeypatch):
    client, seen = _discover(monkeypatch, [_item(1, [18])])
    async with client:
        await tmdb_service.fetch_filtered_pool(client, "tv", ["드라마", "코미디"], [], 120, target_count=5)
    assert seen[0]["with_genres"] == "18|35"                  # OR
    assert seen[0]["without_genres"] == "10764,10767,10763"   # 예능·토크·뉴스 제외

    client, seen = _discover(monkeypatch, [_item(1, [18])])
    async with client:
        await tmdb_service.fetch_filtered_pool(client, "movie", ["드라마"], [], 120, target_count=5)
    assert "without_genres" not in seen[0]


async def test_required_genre_is_and_and_other_genres_filter_results(monkeypatch):
    results = [_item(1, [16, 35]), _item(2, [16, 28]), _item(3, [16, 10751])]
    client, seen = _discover(monkeypatch, results)
    async with client:
        pool = await tmdb_service.fetch_filtered_pool(
            client, "movie", ["코미디", "가족"], [], 120, target_count=10, required_genres=["애니메이션"])
    assert seen[0]["with_genres"] == "16"                      # 필수 장르만 API로 (AND)
    assert [p["id"] for p in pool] == [1, 3]                    # 코미디 또는 가족만 남김


async def test_exclude_genres_become_without_genres(monkeypatch):
    client, seen = _discover(monkeypatch, [_item(1, [18])])
    async with client:
        await tmdb_service.fetch_filtered_pool(
            client, "movie", ["드라마", "범죄"], [], 120, target_count=5, exclude_genres=["범죄", "공포", "없는장르"])
    assert seen[0]["without_genres"] == "27"          # 포함 장르와 겹치는 범죄는 빼지 않는다

    client, seen = _discover(monkeypatch, [_item(1, [18])])
    async with client:
        await tmdb_service.fetch_filtered_pool(
            client, "tv", [], [], 120, target_count=5, exclude_genres=["범죄", "스릴러"])
    assert seen[0]["without_genres"] == "10764,10767,10763,80"   # TV 기본 제외 + 범죄 (스릴러는 TV에 없음)


# ------------------------------------------------------------------
# query_service (Query Structuring)
# ------------------------------------------------------------------
def test_validate_structure():
    ok = query_service.validate_structure(
        '{"include_genres": ["드라마", "가족"], "exclude_genres": ["공포", "범죄"], "reason": "r"}')
    assert ok == {"include_genres": ["드라마", "가족"], "exclude_genres": ["공포", "범죄"],
                  "reason": "r", "status": "ok"}

    f = query_service.validate_structure(
        '{"include_genres": ["드라마", "힐링", "드라마", "가족", "코미디", "SF"],'
        ' "exclude_genres": ["드라마", "공포", "범죄", "스릴러", "전쟁", "서부"]}')
    assert f["include_genres"] == ["드라마", "가족", "코미디"]
    assert f["exclude_genres"] == ["공포", "범죄", "스릴러"]      # 4개로 자른 뒤 포함과 겹치는 드라마 제거
    assert f["status"] == "filtered"

    only_exclude = query_service.validate_structure('{"include_genres": [], "exclude_genres": ["공포"]}')
    assert only_exclude["exclude_genres"] == ["공포"] and only_exclude["status"] == "ok"

    assert query_service.validate_structure('{"include_genres": [], "exclude_genres": []}')["status"] == "empty"
    assert query_service.validate_structure("장르는 드라마")["status"] == "fallback"


async def test_structure_query_skips_llm_without_query(monkeypatch):
    async def must_not_call(*a, **k):
        raise AssertionError
    monkeypatch.setattr(query_service, "call_luxia", must_not_call)
    r = await query_service.structure_query(None, "  ")
    assert r["include_genres"] == [] and r["exclude_genres"] == []


async def test_structure_query_survives_llm_error(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(query_service, "call_luxia", broken)
    assert (await query_service.structure_query(None, "힐링"))["status"] == "fallback"


# ------------------------------------------------------------------
# main._fetch_pool
# ------------------------------------------------------------------
def _capture_fetch(monkeypatch, empty_when=None):
    calls = []

    async def fake_fetch(client, media_type, genres, ott, runtime, target_count=60,
                         required_genres=None, exclude_genres=None):
        call = (media_type, list(genres), list(required_genres or []), list(exclude_genres or []))
        calls.append(call)
        if empty_when and empty_when(call):
            return []
        return [{"id": len(calls), "media_type": media_type, "title": "x"}]

    monkeypatch.setattr(main, "fetch_filtered_pool", fake_fetch)
    return calls


def _structure(monkeypatch, include, exclude):
    queries = []

    async def fake_structure(client, query):
        queries.append(query)
        return {"include_genres": include, "exclude_genres": exclude, "reason": "-", "status": "ok"}

    monkeypatch.setattr(main, "structure_query", fake_structure)
    return queries


async def test_fetch_pool_uses_structured_genres_when_user_chose_none(monkeypatch):
    calls = _capture_fetch(monkeypatch)
    queries = _structure(monkeypatch, ["드라마", "가족"], ["공포", "애니메이션"])
    info = {}
    req = main.RecommendRequest(query="힐링 영화", refinements=["최근 작품"], media=["movie", "animation"])
    await main._fetch_pool(None, req, info)

    assert queries == [main.combine_query("힐링 영화", ["최근 작품"])]
    assert calls == [
        ("movie", ["드라마", "가족"], [], ["공포", "애니메이션"]),
        ("movie", ["드라마", "가족"], ["애니메이션"], ["공포"]),   # 애니메이션 매체는 애니메이션을 빼지 않는다
    ]
    assert info["query_structure"]["exclude_genres"] == ["공포", "애니메이션"]
    assert main._structure_log(info["query_structure"]) == {
        "include_genres": ["드라마", "가족"], "exclude_genres": ["공포", "애니메이션"], "status": "ok"}


async def test_user_genres_win_but_exclusions_still_apply(monkeypatch):
    calls = _capture_fetch(monkeypatch)
    _structure(monkeypatch, ["드라마"], ["범죄", "공포"])
    await main._fetch_pool(None, main.RecommendRequest(query="q", genres=["범죄", "액션"], media=["tv"]), {})
    # 사용자가 고른 장르를 쓰고, 사용자가 고른 범죄는 제외하지 않는다
    assert calls == [("tv", ["범죄", "액션"], [], ["공포"])]


async def test_fetch_pool_relaxes_include_then_exclude(monkeypatch):
    calls = _capture_fetch(monkeypatch, empty_when=lambda c: bool(c[1]) or bool(c[3]))
    _structure(monkeypatch, ["드라마"], ["공포"])
    pool = await main._fetch_pool(None, main.RecommendRequest(query="q", media=["movie"]), {})
    assert [c[1:] for c in calls] == [(["드라마"], [], ["공포"]), ([], [], ["공포"]), ([], [], [])]
    assert len(pool) == 1


# ------------------------------------------------------------------
# candidate_service
# ------------------------------------------------------------------
def test_rank_with_relevance_prefers_relevant_over_slightly_better_rated():
    items = [{"id": 1, "rating": 8.0, "similarity": 0.10},   # 평점 조금 높지만 검색어와 무관
             {"id": 2, "rating": 7.5, "similarity": 0.60},
             {"id": 3, "rating": 5.0, "similarity": 0.62}]   # 관련은 높지만 평점이 크게 낮음
    ranked = rank_with_relevance(items, lambda x: x["rating"])
    assert ranked[0]["id"] == 2                               # 관련 높고 평점도 괜찮은 작품이 1위

    same_sim = [{**x, "similarity": 0.3} for x in items]      # 검색어가 없으면 기준 점수 순서
    assert [x["id"] for x in rank_with_relevance(same_sim, lambda x: x["rating"])] == [1, 2, 3]


def test_balance_pick_is_not_an_unrelated_hit():
    # 인기·평점은 최고지만 검색어와 무관한 작품(조커 같은)이 C1을 차지하지 않는다
    pool = [{"id": 1, "title": "인기 무관작", "overview": "o", "vote_average": 8.5, "vote_count": 5000,
             "popularity": 900, "similarity": 0.05, "release_date": "2019-01-01"}]
    pool += [{"id": 10 + i, "title": f"관련작{i}", "overview": "o", "vote_average": 7.6, "vote_count": 800,
              "popularity": 150, "similarity": 0.55 - i * 0.01, "release_date": "2015-01-01"} for i in range(12)]
    picks = {p["label"]: p["id"] for p in select_candidates(pool, "따뜻한 힐링 영화")}
    assert picks["평점·인기도 균형"] != 1
