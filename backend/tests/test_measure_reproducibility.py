import asyncio
import csv
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import measure_reproducibility as mr  # noqa: E402


def _row(run, item, agent, score, tools, rank, title=None, query="q"):
    return {"query": query, "run": run, "item_key": item, "title": title or item, "agent": agent,
            "score": score, "tools": tools, "rank": rank, "final_score": 0}


def test_summarize_std_top1_and_tool_effect():
    rows = [
        # A/narrative: 도구가 바뀌고 점수도 크게 흔들림 (3.0, 7.0)
        _row(1, "A", "narrative", 3.0, ["overview_full", "keywords"], 1),
        _row(2, "A", "narrative", 7.0, ["overview_full", "genres"], 2),
        # A/technical: 도구 같고 점수 같음
        _row(1, "A", "technical", 8.0, ["director", "cast"], 1),
        _row(2, "A", "technical", 8.0, ["cast", "director"], 2),     # 순서만 다르면 같은 조합
        # B/public: 한 번 실패
        _row(1, "B", "public", 6.0, ["vote_stats"], 2),
        _row(2, "B", "public", None, ["vote_stats"], 1),
    ]
    s = mr.summarize(rows)

    assert s["per_agent"]["narrative"]["mean_std"] == pytest.approx(2.0)
    assert s["per_agent"]["narrative"]["mean_range"] == pytest.approx(4.0)
    assert s["per_agent"]["technical"]["mean_std"] == 0
    assert s["per_agent"]["public"]["fail_rate"] == pytest.approx(0.5)

    assert s["per_query"]["q"]["top1_titles"] == ["A", "B"]
    assert s["per_query"]["q"]["top1_agreement"] == pytest.approx(0.5)

    assert s["tool_effect"]["varied_tools"] == {"cells": 1, "mean_std": pytest.approx(2.0)}
    assert s["tool_effect"]["same_tools"]["cells"] == 2

    md = mr.to_markdown(s, {"label": "t", "started": "-", "queries": 1, "runs": 2,
                            "eval_temperature": 0.2, "elapsed_min": 0.1})
    assert "| Narrative | 2.00 | 4.00 | 0.0% |" in md and "A → B" in md


def test_full_run_with_fake_pipeline(monkeypatch, tmp_path):
    import main
    from services import agent_service, candidate_service, embedding_service, weight_service

    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    calls = {"pool": 0, "eval": 0}

    async def fake_pool(client, request):
        calls["pool"] += 1
        return [{"id": i, "media_type": "movie", "title": f"m{i}"} for i in range(5)]

    async def fake_weights(client, query):
        return {"weights": {"technical": .3, "narrative": .4, "public": .3}, "importance": {}, "status": "ok"}

    async def fake_eval(client, candidates, query, weights):
        calls["eval"] += 1
        bump = calls["eval"] % 2            # 회차마다 점수가 달라지게
        return [{**c, "final_score": 9 - i,
                 **{a: {"score": 7.0 + bump} for a in mr.AGENT_TYPES},
                 "tool_usage": {a: {"tools": ["x"]} for a in mr.AGENT_TYPES}} for i, c in enumerate(candidates)]

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(weight_service, "infer_weights", fake_weights)
    monkeypatch.setattr(agent_service, "evaluate_candidates", fake_eval)
    monkeypatch.setattr(embedding_service, "rank_candidates_by_similarity", lambda q, p: p)
    monkeypatch.setattr(candidate_service, "select_candidates", lambda pool, q: pool)

    asyncio.run(mr.run(Namespace(runs=3, queries=2, label="test", out_dir=str(tmp_path))))

    assert calls == {"pool": 2, "eval": 6}               # 후보는 쿼리마다 한 번만 수집
    raw = next(tmp_path.glob("*_raw.csv"))
    with open(raw, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2 * 3 * 5 * 3                    # 쿼리 x 회차 x 후보 x 에이전트
    md = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "| Technical | 0.47 |" in md                  # 7,8,7 -> 표준편차 0.47
