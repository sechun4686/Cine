import asyncio
import json
import sys
import types

import httpx
import pytest

sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from services import log_service  # noqa: E402


def _mock_supabase(status=201):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, text="" if status < 400 else '{"message":"column \\"x\\" does not exist"}')

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(log_service, "LOG_DIR", tmp_path)
    return tmp_path


async def test_saves_to_supabase_with_new_secret_key(monkeypatch, log_dir):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co/")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_xxx")
    client, seen = _mock_supabase()
    async with client:
        assert await log_service.save("feedback", [{"feedback": "accepted"}], client) == "supabase"
    req = seen[0]
    assert str(req.url) == "https://abc.supabase.co/rest/v1/feedback"
    assert req.headers["apikey"] == "sb_secret_xxx"
    assert "authorization" not in req.headers          # 새 형식 키는 Authorization 헤더 없이
    assert json.loads(req.content) == [{"feedback": "accepted"}]
    assert not list(log_dir.iterdir())


async def test_legacy_jwt_key_also_sends_authorization(monkeypatch, log_dir):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOi.test")
    client, seen = _mock_supabase()
    async with client:
        await log_service.save("feedback", [{"a": 1}], client)
    assert seen[0].headers["authorization"] == "Bearer eyJhbGciOi.test"


async def test_falls_back_to_file_when_supabase_fails(monkeypatch, log_dir, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_xxx")
    client, _ = _mock_supabase(status=400)
    async with client:
        assert await log_service.save("candidates", [{"title": "a"}], client) == "file"
    lines = (log_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["title"] == "a"
    assert "does not exist" in capsys.readouterr().out   # Supabase 오류 내용이 로그에 남는다


async def test_without_config_writes_file(log_dir):
    assert await log_service.save("feedback", [{"title": "한글"}]) == "file"
    assert "한글" in (log_dir / "feedback.jsonl").read_text(encoding="utf-8")


def test_candidate_rows_keep_failed_scores_as_null():
    ranked = [{"id": 7, "media_type": "tv", "title": "t", "label": "최신작", "similarity": 0.2,
               "technical": {"score": 7.0, "reason": "r1"}, "narrative": {"score": None, "reason": "평가 실패"},
               "public": {"score": 8.0, "reason": "r3"}, "final_score": 7.4, "failed_agents": ["narrative"],
               "tool_usage": {"technical": {"tools": ["director"]}}}]
    row = log_service.candidate_rows("rid", ranked)[0]
    assert row["item_key"] == "tv:7" and row["rank"] == 1
    assert row["technical"] == 7.0 and row["narrative"] is None
    assert row["tools"] == {"technical": ["director"], "narrative": [], "public": []}


# ------------------------------------------------------------------
# API가 올바른 로그를 남기는지
# ------------------------------------------------------------------
@pytest.fixture
def captured(monkeypatch):
    rows = []
    pending = []

    async def fake_save(table, items, client=None):
        rows.extend((table, r) for r in items)
        return "file"

    monkeypatch.setattr(log_service, "save", fake_save)
    monkeypatch.setattr(log_service, "fire_and_forget", pending.append)

    def flush():
        async def run():
            for coro in pending:
                await coro
        asyncio.run(run())
        pending.clear()
        return rows

    return flush


def _patch_pipeline(monkeypatch, pool):
    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")

    async def fake_pool(client, request):
        return pool

    async def fake_weights(client, query):
        return {"weights": {"technical": 0.3, "narrative": 0.4, "public": 0.3},
                "importance": {"technical": 3, "narrative": 4, "public": 3}, "reason": "이유", "status": "ok"}

    async def fake_eval(client, candidates, query, weights):
        return [{**c, "final_score": 8.0 - i, "failed_agents": [],
                 **{a: {"score": 8.0 - i, "reason": "-"} for a in ("technical", "narrative", "public")}}
                for i, c in enumerate(candidates)]

    async def fake_call(*a, **k):
        return "총평"

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(main, "infer_weights", fake_weights)
    monkeypatch.setattr(main, "rank_candidates_by_similarity", lambda q, p: p)
    monkeypatch.setattr(main, "evaluate_candidates", fake_eval)
    monkeypatch.setattr(main, "call_luxia", fake_call)


def test_recommend_logs_request_and_candidates(monkeypatch, captured):
    _patch_pipeline(monkeypatch, [{"id": i, "media_type": "movie", "title": f"m{i}", "overview": "x"}
                                  for i in range(3)])
    res = TestClient(main.app).post("/api/recommend", json={"query": "힐링", "participant_id": "P01",
                                                            "ott": ["넷플릭스"]}).json()
    rows = captured()

    rec = [r for t, r in rows if t == "recommendations"]
    cand = [r for t, r in rows if t == "candidates"]
    assert len(rec) == 1 and rec[0]["request_id"] == res["request_id"]
    assert rec[0]["status"] == "ok" and rec[0]["participant_id"] == "P01"
    assert rec[0]["best_title"] == res["best"]["title"]
    assert rec[0]["filters"]["ott"] == ["넷플릭스"] and rec[0]["weights"]["narrative"] == 0.4
    assert [c["rank"] for c in cand] == [1, 2, 3]
    assert all(c["request_id"] == res["request_id"] for c in cand)


def test_recommend_logs_errors_too(monkeypatch, captured):
    _patch_pipeline(monkeypatch, [])
    res = TestClient(main.app).post("/api/recommend", json={"query": "q"})
    assert res.status_code == 404
    rec = captured()
    assert rec[0][0] == "recommendations"
    assert rec[0][1]["status"] == "error" and rec[0][1]["error"].startswith("404")


def test_feedback_endpoints_log(monkeypatch, captured):
    monkeypatch.setenv("LUXIA_API_KEY", "l")

    async def fake_call(*a, **k):
        return "총평"

    monkeypatch.setattr(main, "call_luxia", fake_call)
    tc = TestClient(main.app)
    best = {"id": 1, "media_type": "movie", "title": "A", "final_score": 8.0,
            "scores": {"technical": 8, "narrative": 7, "public": 7}}
    other = {"id": 2, "media_type": "movie", "title": "B", "final_score": 7.0,
             "scores": {"technical": 6, "narrative": 8, "public": 6}}

    tc.post("/api/feedback", json={"feedback": "accepted", "item": best, "request_id": "r1",
                                   "participant_id": "P02", "shown_order": 1})
    tc.post("/api/rerecommend", json={"feedback": "not_interested", "rejected": best, "candidates": [other],
                                      "request_id": "r1", "participant_id": "P02", "shown_order": 1})
    rows = [r for t, r in captured() if t == "feedback"]

    assert rows[0]["feedback"] == "accepted" and rows[0]["item_key"] == "movie:1" and rows[0]["shown_order"] == 1
    assert rows[1]["feedback"] == "not_interested" and rows[1]["next_title"] == "B"
    assert rows[1]["need_new_search"] is False and rows[1]["participant_id"] == "P02"


# ------------------------------------------------------------------
# 코드가 만드는 행의 컬럼 이름이 db/schema.sql과 일치하는지 (오타가 있으면 Supabase 저장이 실패한다)
# ------------------------------------------------------------------
def _schema_columns() -> dict[str, set[str]]:
    import re
    from pathlib import Path

    sql = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")
    tables = {}
    for name, body in re.findall(r"create table if not exists (\w+) \((.*?)\n\);", sql, re.S):
        tables[name] = {line.split()[0] for line in body.splitlines()
                        if line.strip() and not line.strip().startswith("--")}
    return tables


def test_rows_match_schema():
    schema = _schema_columns()
    rec = log_service.recommendation_row("rid", "P1", "q", {}, [], 1.0, weight_result={}, best={"id": 1})
    cand = log_service.candidate_rows("rid", [{"id": 1, "technical": {}, "narrative": {}, "public": {}}])[0]
    fb = log_service.feedback_row("rid", "P1", "accepted", {"id": 1})

    for table, row in (("recommendations", rec), ("candidates", cand), ("feedback", fb)):
        assert set(row) <= schema[table], f"{table}: 스키마에 없는 컬럼 {set(row) - schema[table]}"


async def test_url_copied_with_rest_v1_suffix_still_works(monkeypatch, log_dir):
    # Data API 화면에서 복사한 주소에는 /rest/v1이 붙어 있다 -> 예전에는 /rest/v1/rest/v1/... 로 요청해 404(PGRST125)
    monkeypatch.setenv("SUPABASE_URL", " https://abc.supabase.co/rest/v1/ ")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_xxx")
    client, seen = _mock_supabase()
    async with client:
        assert await log_service.save("feedback", [{"a": 1}], client) == "supabase"
    assert str(seen[0].url) == "https://abc.supabase.co/rest/v1/feedback"
