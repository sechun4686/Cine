import sys
import types

import pytest

# main.py가 불러오는 sentence-transformers(무거움) 대신 가짜 모듈
sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from services.rerecommend_service import dominant_agent, pick_alternative  # noqa: E402


def _c(i, final, t, n, p, media="movie"):
    return {"id": i, "title": f"m{i}", "media_type": media, "final_score": final,
            "scores": {"technical": t, "narrative": n, "public": p},
            "reasons": {"technical": "기술", "narrative": "서사", "public": "대중"}}


BEST = _c(1, 7.8, 6.0, 8.5, 8.5)   # 서사 강점 (동점이면 narrative가 public보다 앞)
OTHERS = [
    _c(2, 7.4, 8.5, 6.5, 8.5),      # 기술 강점
    _c(3, 7.6, 5.0, 8.5, 7.5),      # 서사 강점, 점수 더 높음
    _c(4, 3.9, 6.5, 2.0, 6.5),      # 5점 미만 -> 재추천 대상 아님
]


def test_dominant_agent_skips_failed_scores():
    assert dominant_agent(_c(9, 7, None, 6.0, 9.0)) == "public"
    assert dominant_agent(BEST) == "narrative"


def test_watched_picks_next_best_score():
    pick, _ = pick_alternative(BEST, OTHERS, "watched")
    assert pick["id"] == 3


def test_not_interested_prefers_different_strength():
    pick, note = pick_alternative(BEST, OTHERS, "not_interested")
    assert pick["id"] == 2
    assert "서사·감성이 강점" in note and "기술적 완성도가 돋보이는" in note


def test_low_score_candidates_never_recommended():
    pick, _ = pick_alternative(BEST, [OTHERS[2]], "watched")
    assert pick is None


def test_same_id_different_media_is_not_the_rejected_one():
    pick, _ = pick_alternative(BEST, [_c(1, 7.0, 7, 7, 7, media="tv")], "watched")
    assert pick is not None and pick["media_type"] == "tv"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LUXIA_API_KEY", "test")
    calls = []

    async def fake_call(client, system, prompt, **kwargs):
        calls.append(prompt)
        return "새 총평"

    monkeypatch.setattr(main, "call_luxia", fake_call)
    return TestClient(main.app), calls


def test_rerecommend_endpoint_returns_new_best(client):
    tc, calls = client
    res = tc.post("/api/rerecommend", json={
        "query": "힐링", "feedback": "not_interested", "rejected": BEST, "candidates": OTHERS,
    }).json()
    assert res["need_new_search"] is False
    assert res["best"]["id"] == 2 and res["best"]["editor_comment"] == "새 총평"
    assert [c["id"] for c in res["others"]] == [3, 4]
    assert len(calls) == 1 and "m2" in calls[0] and "8.5" in calls[0]


def test_rerecommend_endpoint_asks_new_search_without_llm(client):
    tc, calls = client
    res = tc.post("/api/rerecommend", json={
        "query": "힐링", "feedback": "watched", "rejected": BEST, "candidates": [OTHERS[2]],
    }).json()
    assert res["need_new_search"] is True
    assert calls == []


def test_recommend_excludes_ids(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")
    seen = {}

    async def fake_pool(client, request):
        return [{"id": 1, "media_type": "movie", "title": "a"}, {"id": 2, "media_type": "movie", "title": "b"}]

    async def fake_weights(client, query):
        return {"weights": {"technical": 0.3, "narrative": 0.4, "public": 0.3}, "importance": None,
                "reason": "-", "status": "fallback"}

    async def fake_eval(client, candidates, query, weights):
        seen["ids"] = [c["id"] for c in candidates]
        return [{**c, "final_score": 7.0, **{a: {"score": 7.0, "reason": "-"} for a in
                ("technical", "narrative", "public")}} for c in candidates]

    async def fake_call(*a, **k):
        return "총평"

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(main, "infer_weights", fake_weights)
    monkeypatch.setattr(main, "rank_candidates_by_similarity", lambda q, pool: pool)
    monkeypatch.setattr(main, "evaluate_candidates", fake_eval)
    monkeypatch.setattr(main, "call_luxia", fake_call)

    res = TestClient(main.app).post("/api/recommend", json={"query": "q", "exclude_ids": ["movie:1"]})
    assert res.status_code == 200
    assert seen["ids"] == [2]

    res = TestClient(main.app).post("/api/recommend", json={"query": "q", "exclude_ids": ["movie:1", "movie:2"]})
    assert res.status_code == 404


def test_log_ranking_handles_failed_agent(capsys):
    item = {"title": "a", "label": "x", "final_score": 7.0,
            "technical": {"score": 7.0}, "narrative": {"score": None}, "public": {"score": 7.0}}
    main._log_ranking([item])
    assert "N 실패" in capsys.readouterr().out
