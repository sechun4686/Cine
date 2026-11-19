"""덧붙인 요청(refinements)이 가중치 추론에 구분되어 들어가는지"""
from fastapi.testclient import TestClient

import main
from services import weight_service
from services.weight_service import build_importance_prompt, combine_query, infer_weights


def test_combine_query():
    assert combine_query("힐링 드라마", ["더 유명한 작품", " "]) == "힐링 드라마, 더 유명한 작품"
    assert combine_query("", ["인기작"]) == "인기작"
    assert combine_query("힐링", None) == "힐링"


def test_prompt_without_refinements_is_unchanged_shape():
    prompt = build_importance_prompt("힐링 드라마")
    assert "추가 요청" not in prompt and "힐링 드라마" in prompt


def test_prompt_lists_refinements_separately_with_priority_rule():
    prompt = build_importance_prompt("힐링 드라마", ["더 유명한 작품으로", "영상미 좋은 걸로"])
    assert "1. 더 유명한 작품으로" in prompt and "2. 영상미 좋은 걸로" in prompt
    assert "원래 검색어보다 추가 요청을 우선" in prompt
    # 원래 검색어와 추가 요청이 섞이지 않고 따로 들어간다
    assert "[사용자 검색어]\n힐링 드라마\n" in prompt


async def test_infer_weights_sends_refinements_to_llm(monkeypatch):
    seen = {}

    async def fake_call(client, system, prompt, **kwargs):
        seen["prompt"] = prompt
        return '{"technical": 2, "narrative": 3, "public": 5, "reason": "인기작 요청 반영"}'

    monkeypatch.setattr(weight_service, "call_luxia", fake_call)
    result = await infer_weights(None, "힐링 드라마", ["좀 더 유명하고 인기있는 작품으로"])
    assert "1. 좀 더 유명하고 인기있는 작품으로" in seen["prompt"]
    assert result["status"] == "ok" and max(result["weights"], key=result["weights"].get) == "public"


async def test_infer_weights_with_only_refinements_does_not_fallback(monkeypatch):
    async def fake_call(*a, **k):
        return '{"technical": 1, "narrative": 2, "public": 4, "reason": "-"}'

    monkeypatch.setattr(weight_service, "call_luxia", fake_call)
    assert (await infer_weights(None, "", ["인기작"]))["status"] == "ok"


def test_recommend_passes_refinements_to_weights_and_combined_query_elsewhere(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")
    seen = {}

    async def fake_pool(client, request):
        return [{"id": i, "media_type": "movie", "title": f"m{i}", "overview": "x"} for i in range(3)]

    async def fake_weights(client, query, refinements=None):
        seen["weights"] = (query, refinements)
        return {"weights": {"technical": 0.2, "narrative": 0.3, "public": 0.5},
                "importance": {"technical": 2, "narrative": 3, "public": 5}, "reason": "-", "status": "ok"}

    def fake_rank(query, pool):
        seen["similarity"] = query
        return pool

    async def fake_eval(client, candidates, query, weights, **kwargs):
        seen["eval"] = query
        return [{**c, "final_score": 8.0 - i, "failed_agents": [],
                 **{a: {"score": 8.0 - i, "reason": "-"} for a in ("technical", "narrative", "public")}}
                for i, c in enumerate(candidates)]

    async def fake_call(*a, **k):
        return "총평"

    async def fake_save(*a, **k):
        return "skipped"

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(main, "infer_weights", fake_weights)
    monkeypatch.setattr(main, "rank_candidates_by_similarity", fake_rank)
    monkeypatch.setattr(main, "evaluate_candidates", fake_eval)
    monkeypatch.setattr(main, "call_luxia", fake_call)
    monkeypatch.setattr(main.log_service, "save", fake_save)

    res = TestClient(main.app).post("/api/recommend", json={"query": "힐링 드라마", "refinements": ["인기작으로"]})
    assert res.status_code == 200
    assert seen["weights"] == ("힐링 드라마", ["인기작으로"])
    assert seen["similarity"] == seen["eval"] == "힐링 드라마, 인기작으로"
