from services.candidate_service import RELEVANT_MIN, relevant_pool, select_candidates, _enrich


def _movie(i, sim, vote=7.0, votes=500, pop=50.0, year="2015", genres=(18,), overview="줄거리"):
    return {"id": i, "title": f"m{i}", "similarity": sim, "vote_average": vote, "vote_count": votes,
            "popularity": pop, "year": year, "genre_ids": list(genres), "overview": overview}


def _pool():
    # 0~29: 검색어와 관련 높음, 30~59: 관련 낮음 (그러나 평점·인기도는 훨씬 높음)
    relevant = [_movie(i, 0.6 - i * 0.005) for i in range(30)]
    irrelevant = [_movie(100 + i, 0.05, vote=9.5, pop=900.0) for i in range(30)]
    return relevant + irrelevant


def test_all_candidates_come_from_relevant_pool():
    picks = select_candidates(_pool(), "따뜻한 힐링 영화")
    assert len(picks) == 5
    assert all(p["id"] < 100 for p in picks), [p["id"] for p in picks]


def test_no_query_uses_whole_pool():
    picks = select_candidates(_pool(), "")
    assert any(p["id"] >= 100 for p in picks)


def test_low_vote_count_does_not_win_hidden_gem():
    pool = [_movie(i, 0.5, vote=7.0, pop=20.0) for i in range(12)]
    pool.append(_movie(999, 0.55, vote=10.0, votes=2, pop=1.0))
    picks = {p["label"]: p["id"] for p in select_candidates(pool, "q")}
    assert picks["숨은 명작"] != 999
    assert picks["평점·인기도 균형"] != 999


def test_empty_overview_excluded_from_relevant_pool():
    pool = [_enrich(_movie(i, 0.5), 2026) for i in range(20)]
    pool.append(_enrich(_movie(77, 0.9, overview=""), 2026))
    ids = {x["id"] for x in relevant_pool(pool, "q")}
    assert 77 not in ids and len(ids) >= RELEVANT_MIN


def test_unexpected_pick_prefers_different_genre():
    pool = [_movie(i, 0.6 - i * 0.01, genres=(18,)) for i in range(12)]
    pool.append(_movie(50, 0.555, vote=6.0, genres=(35,)))   # 유일한 코미디, 관련 후보군(상위 10개) 안
    picks = {p["label"]: p["id"] for p in select_candidates(pool, "q")}
    assert picks["의외의 추천"] == 50
