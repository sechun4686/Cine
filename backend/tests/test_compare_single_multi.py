import asyncio
import csv
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import compare_single_multi as cmp  # noqa: E402


def _rows(run, ranking, scores, query="q"):
    """ranking: [item...], scores: {item: (t, n, p)}"""
    out = []
    for rank, item in enumerate(ranking, 1):
        for agent, s in zip(cmp.AGENT_TYPES, scores[item]):
            out.append({"query": query, "run": run, "rank": rank, "item_key": item, "title": item,
                        "agent": agent, "score": s, "tools": [], "final_score": 0})
    return out


def test_compare_modes_top1_tau_diff_and_spread():
    multi = _rows(1, ["A", "B", "C"], {"A": (8, 8, 8), "B": (6, 6, 6), "C": (4, 4, 4)}) \
        + _rows(2, ["A", "B", "C"], {"A": (8, 8, 8), "B": (6, 6, 6), "C": (4, 4, 4)})
    single = _rows(1, ["A", "B", "C"], {"A": (9, 8, 8), "B": (7, 6, 6), "C": (5, 4, None)}) \
        + _rows(2, ["B", "A", "C"], {"A": (7, 7, 7), "B": (8, 7, 7), "C": (6, 6, 6)})
    r = cmp.compare_modes(multi, single)

    q = r["per_query"]["q"]
    assert q["pairs"] == 2
    assert q["top1_match"] == pytest.approx(0.5)               # 1회차 같음, 2회차 다름
    assert q["rank_tau"] == pytest.approx((1 + 1 / 3) / 2)     # 1회차 1.0, 2회차 1/3

    tech = r["per_agent"]["technical"]
    # 1회차 +1,+1,+1 / 2회차 -1,+2,+2 -> 평균 1.0, 절대값 평균 4/3
    assert tech["mean_diff"] == pytest.approx(1.0)
    assert tech["mean_abs_diff"] == pytest.approx(4 / 3)
    # 실패한 점수(None)는 차이에서 빠진다: public 5쌍
    assert r["per_agent"]["public"]["mean_diff"] == pytest.approx((0 + 0 - 1 + 1 + 2) / 5)
    assert tech["spread"]["multi"] > tech["spread"]["single"]   # single 2회차 점수가 덜 벌어짐


def test_summarize_cost():
    runs = [{"mode": "multi", "llm_calls": 30, "llm_sec": 60, "wall_sec": 20},
            {"mode": "multi", "llm_calls": 32, "llm_sec": 62, "wall_sec": 22},
            {"mode": "single", "llm_calls": 5, "llm_sec": 25, "wall_sec": 8}]
    c = cmp.summarize_cost(runs)
    assert c["multi"] == {"llm_calls": 31, "llm_sec": 61, "wall_sec": 21}
    assert c["single"]["llm_calls"] == 5


def _cand(title, score):
    return {"title": title, "overview": "줄거리",
            **{a: {"score": score, "reason": f"{title}-{a} 근거"} for a in cmp.AGENT_TYPES}}


def test_build_survey_is_blind_reproducible_and_keyed():
    items = [{"query": f"요청{i}", "multi": _cand(f"M{i}", 8), "single": _cand(f"S{i}", 7)} for i in range(6)]
    md, key = cmp.build_survey(items, seed=1)
    md2, key2 = cmp.build_survey(items, seed=1)
    assert (md, key) == (md2, key2)                            # 같은 seed면 같은 배치

    for word in ("multi", "single", "Multi", "Single", "에이전트"):
        assert word not in md                                   # 참가자 자료에 방식 이름이 없다
    assert sum(k["A"] == "multi" for k in key) == 3            # 6문항이면 정확히 절반씩
    for k in key:
        assert k["A_title"].startswith("M" if k["A"] == "multi" else "S")
        assert f"### 결과 A: {k['A_title']}" in md
    assert "**기술적 완성도**: M0-technical 근거" in md           # 기본은 점수 숨김
    assert "점)" not in md

    md_scores, _ = cmp.build_survey(items, seed=1, show_scores=True)
    assert "**기술적 완성도 (8.0점)**: M0-technical 근거" in md_scores


def test_build_survey_balances_odd_count():
    items = [{"query": f"요청{i}", "multi": _cand(f"M{i}", 8), "single": _cand(f"S{i}", 7)} for i in range(5)]
    for seed in range(20):
        _, key = cmp.build_survey(items, seed=seed)
        assert sum(k["A"] == "multi" for k in key) == 3


def test_short_overview_cuts_at_sentence_end():
    assert cmp.short_overview("짧은 줄거리.") == "짧은 줄거리."
    long_text = "첫 문장입니다. " * 8 + "마지막 문장이 길게 이어지고 있다"
    out = cmp.short_overview(long_text, limit=60)
    assert out.endswith("입니다.") and len(out) <= 60
    assert cmp.short_overview("가" * 200, limit=50) == "가" * 50 + "…"


def test_full_run_single_gets_multi_data(monkeypatch, tmp_path):
    import main
    from services import agent_service, candidate_service, embedding_service, single_agent_service, weight_service

    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    single_prompts = []

    async def fake_pool(client, request):
        return [{"id": i, "media_type": "movie", "title": f"m{i}", "overview": "o"} for i in range(5)]

    async def fake_weights(client, query):
        return {"weights": {"technical": .3, "narrative": .4, "public": .3}, "importance": {}, "status": "ok"}

    async def fake_details(client, candidates):
        return [{} for _ in candidates]

    async def fake_run_tools(ctx, agent_type, names):
        return {n: f"{ctx.candidate['title']}-{n}-값" for n in names}, []

    async def fake_call(client, system, prompt, **kwargs):
        if "데이터 조회 계획" in system:
            return '{"tools": [], "reason": "-"}'
        if "세 관점에서 평가하는 전문가" in prompt:
            single_prompts.append(prompt)
            return ('{"technical": {"reason": "r", "score": 7}, "narrative": {"reason": "r", "score": 6}, '
                    '"public": {"reason": "r", "score": 5}}')
        return '{"reason": "r", "score": 8}'

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(weight_service, "infer_weights", fake_weights)
    monkeypatch.setattr(embedding_service, "rank_candidates_by_similarity", lambda q, p: p)
    monkeypatch.setattr(candidate_service, "select_candidates", lambda pool, q: pool)
    monkeypatch.setattr(agent_service, "attach_details", fake_details)
    monkeypatch.setattr(agent_service, "run_tools", fake_run_tools)
    monkeypatch.setattr(agent_service, "call_luxia", fake_call)
    monkeypatch.setattr(single_agent_service, "call_luxia", fake_call)
    monkeypatch.setattr(single_agent_service, "attach_details", None)   # 호출되면 실패
    monkeypatch.setattr(single_agent_service, "run_tools", None)

    asyncio.run(cmp.run(Namespace(runs=2, queries=1, label="t", out_dir=str(tmp_path), survey_run=1, seed=0,
                                  single_mode="score")))

    # single은 multi가 실행한 도구(기본 도구) 값을 그대로 받는다
    assert len(single_prompts) == 2 * 5
    assert any("m0-director-값" in p and "m0-overview_full-값" in p for p in single_prompts)

    for mode in ("multi", "single"):
        with open(next(tmp_path.glob(f"*_{mode}_raw.csv")), encoding="utf-8-sig") as f:
            assert len(list(csv.DictReader(f))) == 2 * 5 * 3      # 회차 x 후보 x 관점
    with open(next(tmp_path.glob("*_runs.csv")), encoding="utf-8-sig") as f:
        runs = list(csv.DictReader(f))
    assert {(r["mode"], r["llm_calls"]) for r in runs} == {("multi", "30"), ("single", "5")}

    md = next(p for p in tmp_path.glob("*.md") if not p.name.endswith("_survey.md")).read_text(encoding="utf-8")
    assert "## 4. 비용" in md and "| Single-agent | 5.0 |" in md
    assert "### 결과 A:" in next(tmp_path.glob("*_survey.md")).read_text(encoding="utf-8")
    with open(next(tmp_path.glob("*_survey_key.csv")), encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 1


def test_full_run_single_selects_its_own_tools(monkeypatch, tmp_path):
    """full 모드: single은 multi 데이터를 받지 않고, 도구 선택 1회 + 평가 1회로 독립 실행한다"""
    import main
    from services import agent_service, candidate_service, embedding_service, single_agent_service, weight_service

    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    single_selects, single_evals, single_tool_runs, pool_queries = [], [], [], []

    async def fake_pool(client, request, info=None):
        pool_queries.append(request.query)
        return [{"id": i, "media_type": "movie", "title": f"m{i}", "overview": "o"} for i in range(5)]

    async def fake_weights(client, query):
        return {"weights": {"technical": .3, "narrative": .4, "public": .3}, "importance": {}, "status": "ok"}

    async def fake_details(client, candidates):
        return [{} for _ in candidates]

    async def fake_run_tools(ctx, agent_type, names):
        return {n: f"{ctx.candidate['title']}-{n}-값" for n in names}, []

    async def fake_single_run_tools(ctx, agent_type, names):
        single_tool_runs.append((agent_type, list(names)))
        return await fake_run_tools(ctx, agent_type, names)

    async def fake_call(client, system, prompt, **kwargs):
        if "세 관점에서 평가하기 전에" in prompt:
            single_selects.append(prompt)
            return '{"tools": ["director", "director_filmography", "keywords", "reviews"], "reason": "r"}'
        if "데이터 조회 계획" in system:
            return '{"tools": [], "reason": "-"}'
        if "세 관점에서 평가하는 전문가" in prompt:
            single_evals.append(prompt)
            return ('{"technical": {"reason": "r", "score": 7}, "narrative": {"reason": "r", "score": 6}, '
                    '"public": {"reason": "r", "score": 5}}')
        return '{"reason": "r", "score": 8}'

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(weight_service, "infer_weights", fake_weights)
    monkeypatch.setattr(embedding_service, "rank_candidates_by_similarity", lambda q, p: p)
    monkeypatch.setattr(candidate_service, "select_candidates", lambda pool, q: pool)
    monkeypatch.setattr(agent_service, "attach_details", fake_details)
    monkeypatch.setattr(agent_service, "run_tools", fake_run_tools)
    monkeypatch.setattr(agent_service, "call_luxia", fake_call)
    monkeypatch.setattr(single_agent_service, "attach_details", fake_details)
    monkeypatch.setattr(single_agent_service, "run_tools", fake_single_run_tools)
    monkeypatch.setattr(single_agent_service, "call_luxia", fake_call)

    asyncio.run(cmp.run(Namespace(runs=1, queries=1, label="f", out_dir=str(tmp_path), survey_run=1, seed=0,
                                  single_mode="full", query_set="new")))

    assert pool_queries == [cmp.NEW_QUERIES[0]["query"]]          # 추가 쿼리 세트에서 실행

    assert len(single_selects) == 5 and len(single_evals) == 5
    # single이 고른 도구 + 관점별 필수·기본 도구로 검증된 목록만 실행한다
    runs = dict(single_tool_runs[:3])
    assert runs["technical"] == ["director", "director_filmography"]
    assert runs["narrative"] == ["overview_full", "keywords"]
    assert runs["public"] == ["vote_stats", "reviews"]
    assert "m0-keywords-값" in single_evals[0] or any("keywords-값" in p for p in single_evals)

    with open(next(tmp_path.glob("*_runs.csv")), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert {(r["mode"], r["llm_calls"]) for r in rows} == {("multi", "30"), ("single", "10")}
    md = next(p for p in tmp_path.glob("*.md") if not p.name.endswith("_survey.md")).read_text(encoding="utf-8")
    assert "single(full)" in md and "두 방식 모두 TMDB 상세정보 조회" in md
    assert "세트: new" in md


def test_query_sets():
    assert len(cmp.QUERY_SETS["base"]) == 5 and len(cmp.QUERY_SETS["new"]) == 5
    assert cmp.QUERY_SETS["all"] == cmp.QUERY_SETS["base"] + cmp.QUERY_SETS["new"]
    base = {q["query"] for q in cmp.QUERY_SETS["base"]}
    assert not base & {q["query"] for q in cmp.QUERY_SETS["new"]}     # 겹치는 쿼리 없음
    for q in cmp.NEW_QUERIES:
        assert q["media"] and set(q["media"]) <= {"movie", "tv", "animation"}
        main_req = __import__("main").RecommendRequest(query=q["query"], media=q["media"], ott=q["ott"])
        assert main_req.query == q["query"]
