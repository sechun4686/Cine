import csv
import json
import math
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import measure_reproducibility as mr  # noqa: E402
import weight_sensitivity as ws  # noqa: E402
from services.agent_service import calculate_final_score  # noqa: E402
from services.weight_service import calculate_weights  # noqa: E402


# ------------------------------------------------------------------
# 서비스 계산식과 일치하는지
# ------------------------------------------------------------------
@pytest.mark.parametrize("scores", [
    {"technical": 8, "narrative": 6, "public": 4},
    {"technical": 8, "narrative": 6, "public": None},
    {"technical": None, "narrative": None, "public": None},
])
def test_final_score_matches_service(scores):
    w = {"technical": 0.5, "narrative": 0.3, "public": 0.2}
    expected = calculate_final_score(scores, w)
    got = ws.final_score(scores, w)
    assert (got is None and expected is None) or round(got, 2) == expected


def test_softmax_matches_service():
    imp = {"technical": 2, "narrative": 5, "public": 1}
    assert ws.softmax_weights(imp, 1.5) == pytest.approx(calculate_weights(imp, 1.5))
    assert ws.softmax_weights(imp, math.inf) == pytest.approx({a: 1 / 3 for a in mr.AGENT_TYPES})


# ------------------------------------------------------------------
# 순위 상관
# ------------------------------------------------------------------
def test_kendall_tau():
    assert mr.kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1
    assert mr.kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1
    assert mr.kendall_tau(["a", "b", "c"], ["b", "a", "c"]) == pytest.approx(1 / 3)
    assert mr.kendall_tau(["a"], ["a"]) is None
    assert mr.mean_pairwise_tau([["a", "b"], ["a", "b"], ["b", "a"]]) == pytest.approx(-1 / 3)


def test_summarize_reports_rank_tau():
    rows = []
    for run, order in [(1, ["A", "B"]), (2, ["A", "B"]), (3, ["B", "A"])]:
        for rank, item in enumerate(order, 1):
            rows.append({"query": "q", "run": run, "item_key": item, "title": item, "agent": "technical",
                         "score": 5.0, "tools": [], "rank": rank, "final_score": 0})
    assert mr.summarize(rows)["per_query"]["q"]["rank_tau"] == pytest.approx(-1 / 3)


# ------------------------------------------------------------------
# 분석
# ------------------------------------------------------------------
def test_weight_grid_size_and_sum():
    grid = ws.weight_grid()
    assert len(grid) == 66
    assert all(sum(g.values()) == pytest.approx(1) for g in grid)


def test_perturbations_respect_bounds():
    names = [n for n, _ in ws.perturbations({"technical": 0, "narrative": 5, "public": 3})]
    assert names == ["T+1", "N-1", "P+1", "P-1"]


def _set(items, importance, weights=None):
    return {"source": "f", "query": "q", "run": 1, "importance": importance,
            "weights": weights or ws.softmax_weights(importance, 1.5),
            "items": {k: {"title": k, "scores": dict(zip(mr.AGENT_TYPES, v))} for k, v in items.items()}}


def test_analyze_detects_sensitivity():
    # A는 기술 점수, B는 서사 점수가 높다 -> 기준선(서사 중시)에서 B가 1위, 기술만 보면 A가 1위
    s = _set({"A": (9, 5, 6), "B": (5, 9, 6)}, {"technical": 1, "narrative": 5, "public": 2})
    r = ws.analyze([s])

    tech_only = next(g for g in r["grid"] if g["technical"] == 1)
    narr_only = next(g for g in r["grid"] if g["narrative"] == 1)
    assert tech_only["top1_kept"] == 0 and narr_only["top1_kept"] == 1
    assert next(t for t in r["temperatures"] if t["temperature"] == 1.5)["top1_kept"] == 1
    assert r["margin"]["mean"] > 0


def test_correlation_of_identical_perspectives_is_one():
    s = _set({"A": (9, 9, 9), "B": (5, 5, 5), "C": (7, 7, 7)}, {"technical": 3, "narrative": 3, "public": 3})
    r = ws.analyze([s])
    assert r["correlation"]["T-N"] == pytest.approx(1.0)
    assert r["grid_mean_kept"] == 1     # 관점이 같으면 가중치를 바꿔도 1위가 그대로


def test_end_to_end_from_reproducibility_csv(tmp_path):
    raw = tmp_path / "reproducibility_test_raw.csv"
    fields = ["query", "run", "rank", "item_key", "title", "agent", "score", "final_score", "tools",
              "importance", "weights"]
    imp = {"technical": 2, "narrative": 4, "public": 3}
    w = ws.softmax_weights(imp, 1.5)
    scores = {"m:1": (8, 6, 7), "m:2": (6, 8, 7), "m:3": (7, 7, None)}
    with open(raw, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for run in (1, 2):
            for rank, (key, triple) in enumerate(scores.items(), 1):
                for agent, score in zip(mr.AGENT_TYPES, triple):
                    writer.writerow({"query": "q", "run": run, "rank": rank, "item_key": key, "title": key,
                                     "agent": agent, "score": score, "final_score": 0, "tools": "",
                                     "importance": json.dumps(imp), "weights": json.dumps(w)})

    md_path = ws.run(Namespace(inputs=[str(raw)], label="t", out_dir=str(tmp_path)))
    md = md_path.read_text(encoding="utf-8")
    assert "평가 세트 2개" in md and "## 3. 중요도 ±1" in md
    grid_csv = next(tmp_path.glob("*_grid.csv"))
    with open(grid_csv, encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 66


def test_loads_legacy_python_dict_strings(tmp_path):
    raw = tmp_path / "old_raw.csv"
    raw.write_text(
        "query,run,rank,item_key,title,agent,score,final_score,tools,importance,weights\n"
        "q,1,1,m:1,A,technical,8,0,,\"{'technical': 2, 'narrative': 4, 'public': 3}\","
        "\"{'technical': 0.2, 'narrative': 0.5, 'public': 0.3}\"\n",
        encoding="utf-8-sig",
    )
    s = ws.load_sets([raw])[0]
    assert s["importance"]["narrative"] == 4 and s["weights"]["technical"] == 0.2
