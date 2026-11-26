"""
compare_single_multi.py
실험 4. Single-agent vs Multi-agent 비교

같은 쿼리, 같은 후보, 같은 가중치로 두 방식을 번갈아 실행해
점수·순위·일관성·비용을 비교하고, 사용자 블라인드 설문용 A/B 결과를 만든다.

실행 (backend 폴더에서)
    python scripts/compare_single_multi.py                       # 기본: full 모드, 쿼리 5개 x 3회
    python scripts/compare_single_multi.py --runs 1 --queries 1  # 동작 확인용
    python scripts/compare_single_multi.py --query-set new       # 추가 쿼리 5개 (설문 B형)
    python scripts/compare_single_multi.py --single-mode score   # 채점만 단일화한 비교 (이전 방식)

single 모드 (--single-mode)
    full  (기본) : single 에이전트 1개가 세 관점의 도구 14개 중에서 직접 고르고 한 번에 평가한다
                   도구 선택 제약(관점마다 2~4개, 필수 도구)은 multi와 같다
                   -> 완전한 multi-agent vs single-agent 비교 (도구 선택 + 채점)
    score        : multi의 세 에이전트가 고른 도구 데이터를 그대로 single에 넘긴다
                   -> 두 방식은 같은 데이터를 보며, 채점을 나눈 효과만 비교한다

측정 방식
    - 쿼리마다 후보 5편과 가중치는 처음 한 번만 정해 고정한다 (두 방식 모두 같은 값)
    - 평가 기준 문구·점수 기준·가중치 계산·temperature는 두 방식이 같다
    - Supabase에는 기록하지 않는다 (서비스 함수를 직접 호출)

결과 (scripts/results/)
    compare_<label>_<시각>_multi_raw.csv    multi 원자료 (measure_reproducibility와 같은 열 + reason)
    compare_<label>_<시각>_single_raw.csv   single 원자료 (같은 형식)
        -> 두 파일 모두 weight_sensitivity.py에 그대로 넣을 수 있다
    compare_<label>_<시각>_runs.csv         회차별 LLM 호출 수, LLM 응답 시간 합, 전체 소요 시간
    compare_<label>_<시각>.md               요약표 (보고서용)
    compare_<label>_<시각>_survey.md        설문용 A/B 결과 (참가자에게 보여줄 내용, 방식 이름 없음)
    compare_<label>_<시각>_survey_key.csv   설문 정답표 (A/B가 어느 방식인지. 참가자에게 보여주지 말 것)

LLM 호출 수: 쿼리 수 x (검색어 구조화 1 + 가중치 1 + 회차 x (multi 30 + single 10(full) 또는 5(score)))
"""

import argparse
import asyncio
import copy
import csv
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_reproducibility import (  # noqa: E402  (실험 1과 같은 쿼리·통계 사용)
    AGENT_TYPES,
    DEFAULT_QUERIES,
    _std,
    kendall_tau,
    summarize,
)

# 쿼리 세트
#   base : 실험 1과 같은 쿼리 5개 (measure_reproducibility.DEFAULT_QUERIES, 설문 A형)
#   new  : 추가 쿼리 5개 (설문 B형). 결과를 보기 전에 정한 목록이다 (상황·대상·제외 조건·매체를 넓힘)
#   all  : 둘을 합친 10개
NEW_QUERIES = [
    {"query": "비 오는 날 혼자 보기 좋은 잔잔한 로맨스", "media": ["movie"], "ott": ["넷플릭스"]},
    {"query": "친구들이랑 웃으면서 가볍게 볼 코미디", "media": ["movie"], "ott": []},
    {"query": "주말에 정주행하기 좋은 몰입감 있는 범죄 수사 드라마", "media": ["tv"], "ott": ["넷플릭스"]},
    {"query": "무섭지만 너무 잔인하지는 않은 공포 영화", "media": ["movie"], "ott": []},
    {"query": "생각할 거리를 주는 SF 영화", "media": ["movie"], "ott": []},
]
QUERY_SETS = {"base": DEFAULT_QUERIES, "new": NEW_QUERIES, "all": DEFAULT_QUERIES + NEW_QUERIES}

MODES = ("multi", "single")
SINGLE_MODES = ("full", "score")
SINGLE_MODE_NOTES = {
    "full": "- single(full): 에이전트 1개가 세 관점의 도구를 직접 고르고 한 번에 평가 "
            "(도구 선택 제약은 multi와 같음. 완전한 multi vs single 비교)",
    "score": "- single(score): 같은 회차 multi의 세 에이전트가 고른 데이터를 그대로 받아 평가 "
             "(다른 점은 채점을 나눴는지뿐)",
}
MODE_LABELS = {"multi": "Multi-agent", "single": "Single-agent"}
AGENT_LABELS = {"technical": "기술적 완성도", "narrative": "서사", "public": "대중성"}
RAW_FIELDS = ["query", "run", "rank", "item_key", "title", "agent", "score", "final_score", "tools",
              "importance", "weights", "reason"]


# ------------------------------------------------------------------
# 통계 (LLM 없이 테스트 가능한 순수 함수)
# ------------------------------------------------------------------
def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _ranking(rows: list[dict], query: str, run: int) -> list[str]:
    """한 회차의 순위 (item_key 목록, 1위부터)"""
    picked = {r["item_key"]: r for r in rows if r["query"] == query and r["run"] == run}
    return [r["item_key"] for r in sorted(picked.values(), key=lambda r: r["rank"])]


def _scores(rows: list[dict], query: str, run: int) -> dict:
    """(item_key, agent) -> score"""
    return {(r["item_key"], r["agent"]): r["score"]
            for r in rows if r["query"] == query and r["run"] == run}


def compare_modes(multi_rows: list[dict], single_rows: list[dict]) -> dict:
    """
    같은 회차의 multi / single 결과를 짝지어 비교한다.
    return:
        per_query : 쿼리별 {pairs, top1_match, rank_tau}
        per_agent : 관점별 {mean_diff (single - multi), mean_abs_diff, spread (방식별 후보 간 표준편차)}
        overall   : {top1_match, rank_tau}
    """
    queries = list(dict.fromkeys(r["query"] for r in multi_rows))
    per_query, all_match, all_tau = {}, [], []
    diffs = {a: [] for a in AGENT_TYPES}
    spread = {m: {a: [] for a in AGENT_TYPES} for m in MODES}

    for q in queries:
        runs = sorted({r["run"] for r in multi_rows if r["query"] == q}
                      & {r["run"] for r in single_rows if r["query"] == q})
        matches, taus = [], []
        for run in runs:
            order = {"multi": _ranking(multi_rows, q, run), "single": _ranking(single_rows, q, run)}
            if order["multi"] and order["single"]:
                matches.append(order["multi"][0] == order["single"][0])
            tau = kendall_tau(order["multi"], order["single"])
            if tau is not None:
                taus.append(tau)

            scores = {"multi": _scores(multi_rows, q, run), "single": _scores(single_rows, q, run)}
            for (item, agent), m_score in scores["multi"].items():
                s_score = scores["single"].get((item, agent))
                if m_score is not None and s_score is not None:
                    diffs[agent].append(s_score - m_score)
            for mode in MODES:
                for agent in AGENT_TYPES:
                    values = [s for (_, a), s in scores[mode].items() if a == agent and s is not None]
                    if len(values) > 1:
                        spread[mode][agent].append(_std(values))

        per_query[q] = {
            "pairs": len(runs),
            "top1_match": _mean([float(m) for m in matches]),
            "rank_tau": _mean(taus),
        }
        all_match += matches
        all_tau += taus

    per_agent = {
        a: {
            "mean_diff": _mean(diffs[a]),
            "mean_abs_diff": _mean([abs(d) for d in diffs[a]]),
            "spread": {m: _mean(spread[m][a]) for m in MODES},
        }
        for a in AGENT_TYPES
    }
    overall = {"top1_match": _mean([float(m) for m in all_match]), "rank_tau": _mean(all_tau)}
    return {"per_query": per_query, "per_agent": per_agent, "overall": overall}


def summarize_cost(run_rows: list[dict]) -> dict:
    """방식별 회차 평균 {llm_calls, llm_sec, wall_sec}"""
    result = {}
    for mode in MODES:
        rows = [r for r in run_rows if r["mode"] == mode]
        result[mode] = {k: _mean([r[k] for r in rows]) for k in ("llm_calls", "llm_sec", "wall_sec")}
    return result


def build_survey(items: list[dict], seed: int = 0, show_scores: bool = False) -> tuple[str, list[dict]]:
    """
    items: 쿼리마다 {"query", "multi": 1위 후보 dict, "single": 1위 후보 dict}
           후보 dict = {"title", "overview", "technical": {"score","reason"}, "narrative", "public"}
    return: (참가자용 마크다운, 정답표 행 목록)

    A/B 배치: multi가 A인 문항 수를 절반(홀수면 ±1)으로 맞춘 뒤 seed로 순서를 섞는다
              (한쪽이 A에 몰리면 위치 효과가 섞인다)
    show_scores: 기본 False. single이 점수를 평균 1점가량 후하게 주므로, 숫자를 보여주면
                 참가자가 근거가 아니라 점수 크기로 고를 수 있다.
    """
    rng = random.Random(seed)
    multi_first = [True] * ((len(items) + 1) // 2) + [False] * (len(items) // 2)
    rng.shuffle(multi_first)
    lines = [
        "# 추천 결과 비교 설문 자료",
        "",
        "각 문항에서 같은 요청에 대한 추천 결과 A와 B를 읽고 비교해 주세요.",
        "",
    ]
    key_rows = []

    for no, (item, first) in enumerate(zip(items, multi_first), 1):
        order = ["multi", "single"] if first else ["single", "multi"]
        same = item["multi"]["title"] == item["single"]["title"]
        key_rows.append({"question": no, "query": item["query"], "A": order[0], "B": order[1],
                         "same_title": same, "A_title": item[order[0]]["title"],
                         "B_title": item[order[1]]["title"]})

        lines += [f"## 문항 {no}", "", f"**요청:** {item['query']}", ""]
        for letter, mode in zip("AB", order):
            c = item[mode]
            lines += [f"### 결과 {letter}: {c['title']}", ""]
            if c.get("overview"):
                lines += [f"> {c['overview']}", ""]
            for agent in AGENT_TYPES:
                ev = c.get(agent) or {}
                label = AGENT_LABELS[agent]
                if show_scores:
                    label += " (-)" if ev.get("score") is None else f" ({ev['score']:.1f}점)"
                lines.append(f"- **{label}**: {ev.get('reason', '-')}")
            lines.append("")
    return "\n".join(lines), key_rows


def to_markdown(comparison: dict, mode_summaries: dict, cost: dict, meta: dict) -> str:
    fmt = lambda v, d=2: "-" if v is None else f"{v:.{d}f}"
    pct = lambda v: "-" if v is None else f"{v * 100:.0f}%"
    ov = comparison["overall"]
    lines = [
        f"# Single vs Multi agent 비교 결과 ({meta['label']})",
        "",
        f"- 측정 시각: {meta['started']}",
        f"- 쿼리 {meta['queries']}개 (세트: {meta.get('query_set', 'base')}) x {meta['runs']}회, 쿼리마다 후보 5편과 가중치 고정",
        SINGLE_MODE_NOTES[meta.get("single_mode", "score")],
        f"- 평가 temperature: {meta['eval_temperature']}, 소요 시간: {meta['elapsed_min']:.1f}분",
        "",
        "## 1. 두 방식의 추천이 같은가",
        "",
        f"- 1위 일치율: **{pct(ov['top1_match'])}**",
        f"- 순위 상관(Kendall τ, 1 = 같은 순위): **{fmt(ov['rank_tau'])}**",
        "",
        "| 쿼리 | 비교 회차 | 1위 일치율 | 순위 상관(τ) |",
        "|---|---|---|---|",
    ]
    for q, s in comparison["per_query"].items():
        lines.append(f"| {q} | {s['pairs']} | {pct(s['top1_match'])} | {fmt(s['rank_tau'])} |")

    lines += [
        "",
        "## 2. 같은 후보에 대한 점수 차이",
        "",
        "평균 차이 = single − multi (양수면 single이 더 후하게 줌). "
        "후보 간 표준편차가 클수록 작품끼리 점수를 뚜렷하게 구분한다는 뜻.",
        "",
        "| 관점 | 평균 차이 | 평균 절대 차이 | 후보 간 표준편차 (multi) | 후보 간 표준편차 (single) |",
        "|---|---|---|---|---|",
    ]
    for a, s in comparison["per_agent"].items():
        lines.append(f"| {AGENT_LABELS[a]} | {fmt(s['mean_diff'])} | {fmt(s['mean_abs_diff'])} | "
                     f"{fmt(s['spread']['multi'])} | {fmt(s['spread']['single'])} |")

    lines += [
        "",
        "## 3. 방식별 일관성 (같은 후보를 회차마다 다시 평가)",
        "",
        "| 관점 | 표준편차 (multi) | 표준편차 (single) | 실패율 (multi) | 실패율 (single) |",
        "|---|---|---|---|---|",
    ]
    for a in AGENT_TYPES:
        m = mode_summaries["multi"]["per_agent"].get(a, {})
        s = mode_summaries["single"]["per_agent"].get(a, {})
        lines.append(f"| {AGENT_LABELS[a]} | {fmt(m.get('mean_std'))} | {fmt(s.get('mean_std'))} | "
                     f"{pct(m.get('fail_rate'))} | {pct(s.get('fail_rate'))} |")

    lines += [
        "",
        "| 쿼리 | 1위 일치율 (multi) | 1위 일치율 (single) | 순위 상관 τ (multi) | 순위 상관 τ (single) |",
        "|---|---|---|---|---|",
    ]
    for q in comparison["per_query"]:
        m = mode_summaries["multi"]["per_query"].get(q, {})
        s = mode_summaries["single"]["per_query"].get(q, {})
        lines.append(f"| {q} | {pct(m.get('top1_agreement'))} | {pct(s.get('top1_agreement'))} | "
                     f"{fmt(m.get('rank_tau'))} | {fmt(s.get('rank_tau'))} |")
    if meta["runs"] < 2:
        lines += ["", "회차가 1번이라 일관성은 측정되지 않았습니다 (--runs 2 이상)."]

    lines += [
        "",
        "## 4. 비용 (회차 평균, 후보 5편 평가 기준)",
        "",
        "| 방식 | LLM 호출 수 | LLM 응답 시간 합(초) | 전체 소요 시간(초) |",
        "|---|---|---|---|",
    ]
    for mode in MODES:
        c = cost[mode]
        lines.append(f"| {MODE_LABELS[mode]} | {fmt(c['llm_calls'], 1)} | {fmt(c['llm_sec'], 1)} | "
                     f"{fmt(c['wall_sec'], 1)} |")
    if meta.get("single_mode", "score") == "score":
        note = ("전체 소요 시간: multi는 TMDB 데이터 수집을 포함하고, single은 multi가 모은 데이터를 재사용하므로 "
                "평가 시간만 포함한다. 두 방식의 계산 비용은 LLM 호출 수와 LLM 응답 시간 합으로 비교한다.")
    else:
        note = "전체 소요 시간: 두 방식 모두 TMDB 상세정보 조회, 도구 실행, 평가를 포함한다."
    lines += ["", note, ""]
    return "\n".join(lines)


# ------------------------------------------------------------------
# 실행
# ------------------------------------------------------------------
def _raw_rows(ranked: list[dict], query: str, run_no: int, weight_result: dict) -> list[dict]:
    rows = []
    for rank, c in enumerate(ranked, 1):
        for agent in AGENT_TYPES:
            rows.append({
                "query": query, "run": run_no, "rank": rank,
                "item_key": f"{c.get('media_type', 'movie')}:{c.get('id')}",
                "title": c.get("title"), "agent": agent,
                "score": c[agent].get("score"),
                "final_score": c.get("final_score"),
                "tools": (c.get("tool_usage", {}).get(agent) or {}).get("tools", []),
                "importance": weight_result.get("importance"),
                "weights": weight_result.get("weights"),
                "reason": c[agent].get("reason", ""),
            })
    return rows


def _csv_row(row: dict) -> dict:
    return {**row, "tools": "|".join(row["tools"]),
            "importance": json.dumps(row["importance"]), "weights": json.dumps(row["weights"])}


def short_overview(text: str, limit: int = 160) -> str:
    """줄거리를 limit자 안에서 문장 끝으로 자른다. 문장 끝이 없으면 자르고 '…'를 붙인다."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("."))
    if end >= limit // 2:
        return cut[: end + 1].strip()
    return cut.rstrip() + "…"


def _survey_candidate(c: dict) -> dict:
    return {"title": (c.get("title") or "").strip(), "overview": short_overview(c.get("overview")),
            **{a: {"score": c[a].get("score"), "reason": c[a].get("reason", "")} for a in AGENT_TYPES}}


async def run(args) -> None:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env", override=True)

    import httpx
    import main
    from services import agent_service, single_agent_service
    from services.agent_service import evaluate_candidates
    from services.candidate_service import select_candidates
    from services.embedding_service import rank_candidates_by_similarity
    from services.single_agent_service import evaluate_candidates_single, tool_data_from_multi
    from services.weight_service import infer_weights

    for key in ("TMDB_API_KEY", "LUXIA_API_KEY"):
        if not os.getenv(key):
            sys.exit(f"{key}가 backend/.env에 없습니다.")

    # 평가 단계 LLM 호출 수와 응답 시간을 센다 (이 프로세스 안에서만 감싼다)
    meter = {"calls": 0, "sec": 0.0}

    def metered(fn):
        async def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                return await fn(*a, **kw)
            finally:
                meter["calls"] += 1
                meter["sec"] += time.perf_counter() - t0
        return wrapper

    agent_service.call_luxia = metered(agent_service.call_luxia)
    single_agent_service.call_luxia = metered(single_agent_service.call_luxia)

    single_mode = getattr(args, "single_mode", "full")
    query_set = QUERY_SETS[getattr(args, "query_set", "base")]
    queries = query_set[: args.queries] if args.queries else query_set
    out_dir = Path(args.out_dir) if args.out_dir else BACKEND_DIR / "scripts" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"compare_{args.label}_{datetime.now().strftime('%Y%m%d_%H%M')}"

    rows = {m: [] for m in MODES}
    run_rows: list[dict] = []
    survey_items: list[dict] = []
    started = time.perf_counter()
    total, done = len(queries) * args.runs, 0

    files = {m: open(f"{base}_{m}_raw.csv", "w", newline="", encoding="utf-8-sig") for m in MODES}
    writers = {m: csv.DictWriter(files[m], fieldnames=RAW_FIELDS) for m in MODES}
    for w in writers.values():
        w.writeheader()

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for spec in queries:
                query = spec["query"]
                request = main.RecommendRequest(query=query, media=spec["media"], ott=spec["ott"])

                # 후보 5편과 가중치는 한 번만 정해 두 방식이 같이 쓴다
                pool = await main._fetch_pool(client, request)
                pool = await asyncio.to_thread(rank_candidates_by_similarity, query, pool)
                fixed = select_candidates(pool, query)
                weight_result = await infer_weights(client, query)
                weights = weight_result["weights"]
                print(f"\n[쿼리] {query}\n  고정 후보: {', '.join(c['title'] for c in fixed)}\n  가중치: {weights}")

                for run_no in range(1, args.runs + 1):
                    ranked = {}

                    # 1) multi
                    meter.update(calls=0, sec=0.0)
                    t0 = time.perf_counter()
                    multi_cands = copy.deepcopy(fixed)
                    ranked["multi"] = await evaluate_candidates(client, multi_cands, query, weights)
                    run_rows.append({"mode": "multi", "query": query, "run": run_no,
                                     "llm_calls": meter["calls"], "llm_sec": round(meter["sec"], 2),
                                     "wall_sec": round(time.perf_counter() - t0, 2)})

                    # 2) single
                    meter.update(calls=0, sec=0.0)
                    t0 = time.perf_counter()
                    if single_mode == "full":
                        # 에이전트 1개가 도구를 직접 골라 평가 (TMDB 조회부터 독립 실행)
                        ranked["single"] = await evaluate_candidates_single(
                            client, copy.deepcopy(fixed), query, weights, select_tools=True)
                    else:
                        # multi가 고른 데이터를 그대로 사용
                        # (evaluate_candidates가 입력 dict에 결과를 붙이므로 multi_cands는 원래 순서 그대로다)
                        data = [tool_data_from_multi(c) for c in multi_cands]
                        ranked["single"] = await evaluate_candidates_single(
                            client, copy.deepcopy(fixed), query, weights, tool_data_list=data)
                    run_rows.append({"mode": "single", "query": query, "run": run_no,
                                     "llm_calls": meter["calls"], "llm_sec": round(meter["sec"], 2),
                                     "wall_sec": round(time.perf_counter() - t0, 2)})

                    for mode in MODES:
                        new = _raw_rows(ranked[mode], query, run_no, weight_result)
                        rows[mode] += new
                        writers[mode].writerows(_csv_row(r) for r in new)
                        files[mode].flush()   # 중간에 멈춰도 그때까지의 원자료는 남는다

                    if run_no == args.survey_run and ranked["multi"] and ranked["single"]:
                        survey_items.append({"query": query,
                                             "multi": _survey_candidate(ranked["multi"][0]),
                                             "single": _survey_candidate(ranked["single"][0])})

                    done += 1
                    top = {m: ranked[m][0]["title"] if ranked[m] else "-" for m in MODES}
                    print(f"  {run_no}회차 1위  multi: {top['multi']} / single: {top['single']}"
                          f"  ({done}/{total}, {(time.perf_counter() - started) / 60:.1f}분 경과)")
    finally:
        for f in files.values():
            f.close()

    with open(f"{base}_runs.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["mode", "query", "run", "llm_calls", "llm_sec", "wall_sec"])
        writer.writeheader()
        writer.writerows(run_rows)

    meta = {
        "label": args.label, "started": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "queries": len(queries), "runs": args.runs, "eval_temperature": agent_service.EVAL_TEMPERATURE,
        "elapsed_min": (time.perf_counter() - started) / 60, "single_mode": single_mode,
        "query_set": getattr(args, "query_set", "base"),
    }
    report = to_markdown(compare_modes(rows["multi"], rows["single"]),
                         {m: summarize(rows[m]) for m in MODES}, summarize_cost(run_rows), meta)
    Path(f"{base}.md").write_text(report, encoding="utf-8")

    survey_md, key_rows = build_survey(survey_items, seed=args.seed, show_scores=getattr(args, "show_scores", False))
    Path(f"{base}_survey.md").write_text(survey_md, encoding="utf-8")
    with open(f"{base}_survey_key.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["question", "query", "A", "B", "same_title", "A_title", "B_title"])
        writer.writeheader()
        writer.writerows(key_rows)

    print("\n" + report)
    print(f"결과 파일: {base}*")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Single vs Multi agent 비교")
    parser.add_argument("--runs", type=int, default=3, help="쿼리마다 반복 횟수 (기본 3, 일관성 비교는 2 이상)")
    parser.add_argument("--query-set", choices=list(QUERY_SETS), default="base",
                        help="base: 실험 쿼리 5개 (기본) / new: 추가 쿼리 5개 / all: 10개")
    parser.add_argument("--queries", type=int, default=0, help="세트 앞에서부터 사용할 쿼리 수 (0 = 전부)")
    parser.add_argument("--label", default="main", help="결과 파일 이름에 붙일 이름")
    parser.add_argument("--out-dir", default="", help="결과 저장 폴더 (기본 scripts/results)")
    parser.add_argument("--survey-run", type=int, default=1, help="설문 자료로 쓸 회차 (기본 1)")
    parser.add_argument("--seed", type=int, default=0, help="설문 A/B 배치 무작위 seed")
    parser.add_argument("--single-mode", choices=SINGLE_MODES, default="full",
                        help="full: single이 도구를 직접 선택 (기본) / score: multi가 고른 데이터로 채점만")
    parser.add_argument("--show-scores", action="store_true",
                        help="설문 자료에 관점별 점수도 표시 (기본: 근거만 표시)")
    args = parser.parse_args()
    if not 1 <= args.survey_run <= args.runs:
        parser.error("--survey-run은 1 이상 --runs 이하여야 합니다.")

    n = len(QUERY_SETS[args.query_set][: args.queries] if args.queries else QUERY_SETS[args.query_set])
    calls = n * (2 + args.runs * (40 if args.single_mode == "full" else 35))
    print(f"쿼리 {n}개 ({args.query_set}) x {args.runs}회, LLM 호출 약 {calls}회")
    asyncio.run(run(args))


if __name__ == "__main__":
    main_cli()
