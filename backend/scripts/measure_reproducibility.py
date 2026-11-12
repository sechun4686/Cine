"""
measure_reproducibility.py
같은 쿼리·같은 후보를 여러 번 평가해 에이전트 점수와 최종 추천이 얼마나 흔들리는지 측정한다.

실행 (backend 폴더에서)
    python scripts/measure_reproducibility.py                 # 기본: 쿼리 5개 x 5회
    python scripts/measure_reproducibility.py --runs 3 --queries 3 --label after-fix

측정 방식
    - 쿼리마다 후보 5개는 처음 한 번만 뽑아 고정한다 (후보가 바뀐 영향과 에이전트 편차를 분리)
    - 매 회차마다 가중치 추론 + 세 에이전트 평가(도구 선택 포함)를 다시 실행한다
    - Supabase에는 기록하지 않는다 (서비스 함수를 직접 호출)

결과 (scripts/results/)
    reproducibility_<label>_<시각>_raw.csv   회차 x 후보 x 에이전트별 점수와 선택 도구 (엑셀로 열기 가능)
    reproducibility_<label>_<시각>.md        요약표 (보고서용)

LLM 호출 수: 쿼리 수 x 회차 x 31회 (가중치 1 + 후보 5 x 에이전트 3 x 2)
"""

import argparse
import asyncio
import copy
import csv
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

AGENT_TYPES = ("technical", "narrative", "public")

# 성격이 다른 쿼리 5개 (분위기 / 장르 / 대상 / 실화 / 작품성)
DEFAULT_QUERIES = [
    {"query": "지친 하루 끝에 보기 좋은 잔잔하고 따뜻한 힐링 영화", "media": ["movie"], "ott": ["넷플릭스"]},
    {"query": "마지막에 반전이 있는 긴장감 넘치는 스릴러", "media": ["movie"], "ott": ["넷플릭스"]},
    {"query": "가족이랑 같이 웃으면서 볼 수 있는 애니메이션", "media": ["animation"], "ott": []},
    {"query": "실화를 바탕으로 한 감동적인 이야기", "media": ["movie"], "ott": []},
    {"query": "연출과 영상미가 뛰어난 작품성 있는 드라마", "media": ["tv"], "ott": []},
]


# ------------------------------------------------------------------
# 통계 (LLM 없이 테스트 가능한 순수 함수)
# ------------------------------------------------------------------
def _std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def summarize(rows: list[dict]) -> dict:
    """
    rows: 회차 x 후보 x 에이전트 1행씩
        {"query", "run", "item_key", "title", "agent", "score"(None=실패), "tools", "final_score", "rank"}
    return:
        per_query  : 쿼리별 {top1_agreement, top1_titles, mean_std, max_range}
        per_agent  : 에이전트별 {mean_std, mean_range, fail_rate}
        tool_effect: 도구 조합이 회차마다 같았을 때 / 달랐을 때의 평균 표준편차
    """
    queries = list(dict.fromkeys(r["query"] for r in rows))

    # (쿼리, 후보, 에이전트) 별 점수 묶기
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["query"], r["item_key"], r["agent"]), []).append(r)

    cells = []
    for (query, item_key, agent), items in groups.items():
        scores = [i["score"] for i in items if i["score"] is not None]
        tool_sets = {tuple(sorted(i["tools"])) for i in items}
        cells.append({
            "query": query, "item_key": item_key, "agent": agent,
            "std": _std(scores), "range": (max(scores) - min(scores)) if scores else 0.0,
            "fails": len(items) - len(scores), "n": len(items),
            "tools_varied": len(tool_sets) > 1,
        })

    per_query = {}
    for q in queries:
        q_rows = [r for r in rows if r["query"] == q]
        runs = sorted({r["run"] for r in q_rows})
        top1 = [next((r["title"] for r in q_rows if r["run"] == run and r["rank"] == 1), None) for run in runs]
        most = max(set(top1), key=top1.count) if top1 else None
        q_cells = [c for c in cells if c["query"] == q]
        per_query[q] = {
            "runs": len(runs),
            "top1_agreement": top1.count(most) / len(top1) if top1 else 0.0,
            "top1_titles": top1,
            "mean_std": statistics.mean(c["std"] for c in q_cells) if q_cells else 0.0,
            "max_range": max((c["range"] for c in q_cells), default=0.0),
        }

    per_agent = {}
    for a in AGENT_TYPES:
        a_cells = [c for c in cells if c["agent"] == a]
        if not a_cells:
            continue
        per_agent[a] = {
            "mean_std": statistics.mean(c["std"] for c in a_cells),
            "mean_range": statistics.mean(c["range"] for c in a_cells),
            "fail_rate": sum(c["fails"] for c in a_cells) / sum(c["n"] for c in a_cells),
        }

    same = [c["std"] for c in cells if not c["tools_varied"]]
    varied = [c["std"] for c in cells if c["tools_varied"]]
    tool_effect = {
        "same_tools": {"cells": len(same), "mean_std": statistics.mean(same) if same else None},
        "varied_tools": {"cells": len(varied), "mean_std": statistics.mean(varied) if varied else None},
    }

    return {"per_query": per_query, "per_agent": per_agent, "tool_effect": tool_effect}


def to_markdown(summary: dict, meta: dict) -> str:
    labels = {"technical": "Technical", "narrative": "Narrative", "public": "Public"}
    fmt = lambda v: "-" if v is None else f"{v:.2f}"
    lines = [
        f"# 재현성 측정 결과 ({meta['label']})",
        "",
        f"- 측정 시각: {meta['started']}",
        f"- 쿼리 {meta['queries']}개 x {meta['runs']}회, 쿼리마다 후보 5개 고정",
        f"- 평가 temperature: {meta['eval_temperature']}, 소요 시간: {meta['elapsed_min']:.1f}분",
        "",
        "## 에이전트별 점수 편차 (0~10점 기준)",
        "",
        "| 에이전트 | 평균 표준편차 | 평균 범위(최대-최소) | 평가 실패율 |",
        "|---|---|---|---|",
    ]
    for a, s in summary["per_agent"].items():
        lines.append(f"| {labels[a]} | {s['mean_std']:.2f} | {s['mean_range']:.2f} | {s['fail_rate'] * 100:.1f}% |")

    lines += [
        "",
        "## 쿼리별 최종 추천(1위) 일치율",
        "",
        "| 쿼리 | 1위 일치율 | 평균 표준편차 | 최대 범위 | 회차별 1위 |",
        "|---|---|---|---|---|",
    ]
    for q, s in summary["per_query"].items():
        titles = " → ".join(t or "-" for t in s["top1_titles"])
        lines.append(f"| {q} | {s['top1_agreement'] * 100:.0f}% | {s['mean_std']:.2f} | {s['max_range']:.1f} | {titles} |")

    te = summary["tool_effect"]
    lines += [
        "",
        "## 도구 선택이 점수 편차에 주는 영향",
        "",
        "같은 후보·같은 에이전트를 여러 번 평가했을 때, 회차마다 고른 도구 조합이 같았는지/달랐는지에 따른 평균 표준편차",
        "",
        "| 도구 조합 | 칸 수 | 평균 표준편차 |",
        "|---|---|---|",
        f"| 매번 같음 | {te['same_tools']['cells']} | {fmt(te['same_tools']['mean_std'])} |",
        f"| 회차마다 다름 | {te['varied_tools']['cells']} | {fmt(te['varied_tools']['mean_std'])} |",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------
# 실행
# ------------------------------------------------------------------
async def run(args) -> None:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env", override=True)

    import httpx
    import main
    from services.agent_service import EVAL_TEMPERATURE, evaluate_candidates
    from services.candidate_service import select_candidates
    from services.embedding_service import rank_candidates_by_similarity
    from services.weight_service import infer_weights

    for key in ("TMDB_API_KEY", "LUXIA_API_KEY"):
        if not os.getenv(key):
            sys.exit(f"{key}가 backend/.env에 없습니다.")

    queries = DEFAULT_QUERIES[: args.queries]
    out_dir = Path(args.out_dir) if args.out_dir else BACKEND_DIR / "scripts" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    raw_path = out_dir / f"reproducibility_{args.label}_{stamp}_raw.csv"
    md_path = out_dir / f"reproducibility_{args.label}_{stamp}.md"

    fields = ["query", "run", "rank", "item_key", "title", "agent", "score", "final_score", "tools",
              "importance", "weights"]
    rows: list[dict] = []
    started = time.perf_counter()
    total = len(queries) * args.runs
    done = 0

    # utf-8-sig: 엑셀에서 한글이 깨지지 않게
    with open(raw_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        async with httpx.AsyncClient(timeout=60) as client:
            for spec in queries:
                query = spec["query"]
                request = main.RecommendRequest(query=query, media=spec["media"], ott=spec["ott"])

                # 후보 5개는 한 번만 뽑아 고정
                pool = await main._fetch_pool(client, request)
                pool = await asyncio.to_thread(rank_candidates_by_similarity, query, pool)
                fixed = select_candidates(pool, query)
                print(f"\n[쿼리] {query}\n  고정 후보: {', '.join(c['title'] for c in fixed)}")

                for run_no in range(1, args.runs + 1):
                    weight_result = await infer_weights(client, query)
                    ranked = await evaluate_candidates(client, copy.deepcopy(fixed), query, weight_result["weights"])
                    done += 1
                    elapsed = (time.perf_counter() - started) / 60
                    print(f"  {run_no}회차 1위: {ranked[0]['title'] if ranked else '-'}"
                          f"  ({done}/{total}, {elapsed:.1f}분 경과)")

                    for rank, c in enumerate(ranked, 1):
                        for agent in AGENT_TYPES:
                            row = {
                                "query": query, "run": run_no, "rank": rank,
                                "item_key": f"{c.get('media_type', 'movie')}:{c.get('id')}",
                                "title": c.get("title"), "agent": agent,
                                "score": c[agent].get("score"),
                                "final_score": c.get("final_score"),
                                "tools": (c.get("tool_usage", {}).get(agent) or {}).get("tools", []),
                                "importance": weight_result.get("importance"),
                                "weights": weight_result.get("weights"),
                            }
                            rows.append(row)
                            writer.writerow({**row, "tools": "|".join(row["tools"])})
                    f.flush()   # 중간에 멈춰도 그때까지의 원자료는 남는다

    meta = {
        "label": args.label, "started": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "queries": len(queries), "runs": args.runs, "eval_temperature": EVAL_TEMPERATURE,
        "elapsed_min": (time.perf_counter() - started) / 60,
    }
    report = to_markdown(summarize(rows), meta)
    md_path.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"원자료: {raw_path}\n요약:   {md_path}")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="에이전트 평가 재현성 측정")
    parser.add_argument("--runs", type=int, default=5, help="쿼리마다 반복 횟수 (기본 5)")
    parser.add_argument("--queries", type=int, default=len(DEFAULT_QUERIES), help="사용할 쿼리 수 (최대 5)")
    parser.add_argument("--label", default="baseline", help="결과 파일 이름에 붙일 이름 (예: baseline, after-fix)")
    parser.add_argument("--out-dir", default="", help="결과 저장 폴더 (기본 scripts/results)")
    args = parser.parse_args()

    calls = args.queries * args.runs * 31
    print(f"쿼리 {args.queries}개 x {args.runs}회 = 추천 평가 {args.queries * args.runs}번, LLM 호출 약 {calls}회")
    asyncio.run(run(args))


if __name__ == "__main__":
    main_cli()
