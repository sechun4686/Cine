"""
weight_sensitivity.py
가중치 민감도 분석: 에이전트 점수(T/N/P)는 그대로 두고 가중치만 바꿔 최종 순위가 얼마나 바뀌는지 본다.
LLM을 호출하지 않는다 (몇 초면 끝난다).

입력
    measure_reproducibility.py가 만든 원자료 CSV (*_raw.csv). 여러 개를 넣으면 합쳐서 분석한다.
    한 '평가 세트' = (조건 파일, 쿼리, 회차) 하나 = 후보 5편의 T/N/P 점수 + 그 회차에 LLM이 추론한 중요도·가중치

실행 (backend 폴더에서)
    python scripts/weight_sensitivity.py scripts/results/reproducibility_t02-free_..._raw.csv
    python scripts/weight_sensitivity.py scripts/results/*_raw.csv --label all

분석
    0) 기준선     : 회차마다 LLM이 추론한 가중치로 계산한 순위 (= 서비스가 실제로 낸 순위)
    1) 가중치 그리드: T+N+P=1 위에서 0.1 간격 66가지 가중치로 순위를 다시 계산
                    -> 1위 유지율, 기준선과의 순위 상관(Kendall tau)
    2) Softmax 온도 : 같은 중요도로 온도만 바꿔 가중치를 만든다 (0.5 / 1 / 1.5(서비스) / 2 / 3 / 동일 가중치)
    3) 중요도 ±1    : LLM이 매긴 중요도(0~5)를 에이전트 하나씩 1점 올리고/내려 본다 (최대 6가지)
                    -> LLM이 중요도를 조금 틀려도 추천이 유지되는지 (강건성)
    4) 에이전트 점수 상관: T/N/P 점수끼리의 피어슨 상관. 상관이 높으면 가중치를 바꿔도 순위가 잘 안 바뀐다
    5) 1·2위 점수 차: 기준선에서 1위와 2위의 최종 점수 차이 (작을수록 가중치에 민감)

결과 (scripts/results/)
    weight_sensitivity_<label>_<시각>.md         보고서용 요약표
    weight_sensitivity_<label>_<시각>_grid.csv   가중치 66가지 각각의 1위 유지율·순위 상관 (그래프용)
    weight_sensitivity_<label>_<시각>_grid.png   위 결과의 삼각 그래프 (matplotlib이 있을 때만)
"""

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_reproducibility import AGENT_TYPES, kendall_tau  # noqa: E402

LABELS = {"technical": "T", "narrative": "N", "public": "P"}
SERVICE_TEMPERATURE = 1.5
TEMPERATURES = [0.5, 1.0, 1.5, 2.0, 3.0, math.inf]   # inf = 동일 가중치
GRID_STEP = 0.1


# ------------------------------------------------------------------
# 서비스와 같은 계산식 (services/agent_service.calculate_final_score, weight_service.calculate_weights)
# 서비스 모듈을 import하면 langgraph 등이 필요해서, 같은 식을 여기 두고 테스트로 일치를 확인한다.
# ------------------------------------------------------------------
def final_score(scores: dict, weights: dict) -> float | None:
    """실패한 에이전트(None)는 빼고 나머지 가중치를 다시 정규화한다."""
    valid = [a for a in AGENT_TYPES if scores.get(a) is not None]
    if not valid:
        return None
    total_w = sum(weights.get(a, 0) for a in valid)
    if total_w <= 0:
        return sum(scores[a] for a in valid) / len(valid)
    return sum(scores[a] * weights.get(a, 0) for a in valid) / total_w


def softmax_weights(importance: dict, temperature: float) -> dict:
    if math.isinf(temperature):
        return {a: 1 / 3 for a in AGENT_TYPES}
    values = [importance[a] / temperature for a in AGENT_TYPES]
    m = max(values)
    exps = [math.exp(v - m) for v in values]
    total = sum(exps)
    return {a: e / total for a, e in zip(AGENT_TYPES, exps)}


def weight_grid(step: float = GRID_STEP) -> list[dict]:
    n = round(1 / step)
    return [
        {"technical": i / n, "narrative": j / n, "public": (n - i - j) / n}
        for i in range(n + 1) for j in range(n + 1 - i)
    ]


# ------------------------------------------------------------------
# 입력 읽기
# ------------------------------------------------------------------
def _parse_score(value: str) -> float | None:
    return None if value in ("", "None", None) else float(value)


def _parse_json(value: str) -> dict | None:
    if not value or value == "None":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # 예전 CSV는 파이썬 dict 문자열로 저장됐다
        import ast
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None


def load_sets(paths: list[Path]) -> list[dict]:
    """
    원자료 CSV -> 평가 세트 목록
        {"source", "query", "run", "importance", "weights",
         "items": {item_key: {"title", "scores": {T/N/P}}}}
    """
    sets: dict[tuple, dict] = {}
    for path in paths:
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                key = (path.name, row["query"], int(row["run"]))
                s = sets.setdefault(key, {
                    "source": path.name, "query": row["query"], "run": int(row["run"]),
                    "importance": _parse_json(row.get("importance")),
                    "weights": _parse_json(row.get("weights")),
                    "items": {},
                })
                item = s["items"].setdefault(row["item_key"], {"title": row["title"], "scores": {}})
                item["scores"][row["agent"]] = _parse_score(row["score"])
    return list(sets.values())


# ------------------------------------------------------------------
# 순위 계산과 비교
# ------------------------------------------------------------------
def ranking(items: dict, weights: dict) -> list[str]:
    """최종 점수 내림차순 item_key 목록. 동점이면 item_key 순 (재현 가능하게). 전부 실패한 작품은 제외."""
    scored = [(final_score(v["scores"], weights), k) for k, v in items.items()]
    scored = [(s, k) for s, k in scored if s is not None]
    return [k for s, k in sorted(scored, key=lambda x: (-x[0], x[1]))]


def compare(sets: list[dict], weights_for) -> dict:
    """
    weights_for(set) -> 그 세트에 쓸 가중치 (None이면 그 세트는 건너뜀)
    return: 기준선(LLM 가중치) 대비 1위 유지율, 평균 Kendall tau, 비교한 세트 수
    """
    same_top1, taus = [], []
    for s in sets:
        base_w = s["weights"]
        new_w = weights_for(s)
        if not base_w or not new_w:
            continue
        base = ranking(s["items"], base_w)
        new = ranking(s["items"], new_w)
        if not base or not new:
            continue
        same_top1.append(base[0] == new[0])
        tau = kendall_tau(base, new)
        if tau is not None:
            taus.append(tau)
    return {
        "n": len(same_top1),
        "top1_kept": statistics.mean(same_top1) if same_top1 else None,
        "tau": statistics.mean(taus) if taus else None,
    }


def perturbations(importance: dict) -> list[tuple[str, dict]]:
    """에이전트 하나씩 중요도를 +1/-1 (0~5 범위 안에서만)"""
    out = []
    for a in AGENT_TYPES:
        for delta in (1, -1):
            v = importance[a] + delta
            if 0 <= v <= 5:
                out.append((f"{LABELS[a]}{'+' if delta > 0 else '-'}1", {**importance, a: v}))
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# ------------------------------------------------------------------
# 전체 분석
# ------------------------------------------------------------------
def analyze(sets: list[dict]) -> dict:
    # 1) 가중치 그리드
    grid = []
    for w in weight_grid():
        r = compare(sets, lambda s, w=w: w)
        grid.append({**w, **r})

    # 2) Softmax 온도
    temps = []
    for t in TEMPERATURES:
        r = compare(sets, lambda s, t=t: softmax_weights(s["importance"], t) if s["importance"] else None)
        max_w = [max(softmax_weights(s["importance"], t).values()) for s in sets if s["importance"]]
        temps.append({"temperature": t, **r, "mean_max_weight": statistics.mean(max_w) if max_w else None})

    # 3) 중요도 ±1
    perturb = {}
    for s in sets:
        if not s["importance"]:
            continue
        for name, imp in perturbations(s["importance"]):
            perturb.setdefault(name, []).append((s, softmax_weights(imp, SERVICE_TEMPERATURE)))
    perturb_rows = []
    all_kept, all_tau = [], []
    for name in sorted(perturb):
        lookup = {id(s): w for s, w in perturb[name]}
        r = compare([s for s, _ in perturb[name]], lambda s: lookup[id(s)])
        perturb_rows.append({"change": name, **r})
        if r["n"]:
            all_kept += [r["top1_kept"]] * r["n"]
            if r["tau"] is not None:
                all_tau += [r["tau"]] * r["n"]

    # 4) 에이전트 점수 상관 (세 점수가 모두 있는 작품만)
    triples = [
        it["scores"] for s in sets for it in s["items"].values()
        if all(it["scores"].get(a) is not None for a in AGENT_TYPES)
    ]
    corr = {}
    for i, a in enumerate(AGENT_TYPES):
        for b in AGENT_TYPES[i + 1:]:
            corr[f"{LABELS[a]}-{LABELS[b]}"] = pearson([t[a] for t in triples], [t[b] for t in triples])

    # 5) 기준선 1·2위 점수 차
    margins = []
    for s in sets:
        if not s["weights"]:
            continue
        finals = sorted(
            (f for v in s["items"].values() if (f := final_score(v["scores"], s["weights"])) is not None),
            reverse=True,
        )
        if len(finals) >= 2:
            margins.append(finals[0] - finals[1])

    kept_values = [g["top1_kept"] for g in grid if g["top1_kept"] is not None]
    return {
        "sets": len(sets),
        "with_weights": sum(1 for s in sets if s["weights"]),
        "grid": grid,
        "grid_share_kept_80": (sum(1 for v in kept_values if v >= 0.8) / len(kept_values)) if kept_values else None,
        "grid_mean_kept": statistics.mean(kept_values) if kept_values else None,
        "temperatures": temps,
        "perturb": perturb_rows,
        "perturb_overall": {
            "top1_kept": statistics.mean(all_kept) if all_kept else None,
            "tau": statistics.mean(all_tau) if all_tau else None,
        },
        "correlation": corr,
        "scored_items": len(triples),
        "margin": {
            "mean": statistics.mean(margins) if margins else None,
            "median": statistics.median(margins) if margins else None,
            "under_0_3": (sum(1 for m in margins if m < 0.3) / len(margins)) if margins else None,
        },
    }


# ------------------------------------------------------------------
# 보고서
# ------------------------------------------------------------------
def _pct(v):
    return "-" if v is None else f"{v * 100:.0f}%"


def _num(v, digits=2):
    return "-" if v is None else f"{v:.{digits}f}"


def _w(w: dict) -> str:
    return " / ".join(f"{LABELS[a]} {w[a]:.1f}" for a in AGENT_TYPES)


def to_markdown(result: dict, meta: dict) -> str:
    grid = result["grid"]
    corners = {
        "Technical만": {"technical": 1.0, "narrative": 0.0, "public": 0.0},
        "Narrative만": {"technical": 0.0, "narrative": 1.0, "public": 0.0},
        "Public만": {"technical": 0.0, "narrative": 0.0, "public": 1.0},
    }

    def find(w):
        return next(g for g in grid if all(abs(g[a] - w[a]) < 1e-9 for a in AGENT_TYPES))

    ranked = sorted((g for g in grid if g["top1_kept"] is not None), key=lambda g: (g["top1_kept"], g["tau"] or 0))

    lines = [
        f"# 가중치 민감도 분석 ({meta['label']})",
        "",
        f"- 분석 시각: {meta['started']}",
        f"- 입력: {', '.join(meta['sources'])}",
        f"- 평가 세트 {result['sets']}개 (쿼리 x 회차, 세트마다 후보 5편), 가중치 기록이 있는 세트 {result['with_weights']}개",
        "- 기준선: 회차마다 LLM이 추론한 가중치로 계산한 순위 (서비스가 실제로 낸 추천)",
        "- 1위 유지율: 가중치를 바꿔도 기준선과 같은 작품이 1위인 비율 / 순위 상관: 5편 순위의 Kendall tau 평균",
        "",
        "## 1. 가중치 그리드 (T+N+P=1, 0.1 간격 66가지)",
        "",
        f"- 66가지 평균 1위 유지율: **{_pct(result['grid_mean_kept'])}**",
        f"- 1위 유지율 80% 이상인 가중치 비율: **{_pct(result['grid_share_kept_80'])}**",
        "",
        "| 가중치 | 1위 유지율 | 순위 상관(τ) |",
        "|---|---|---|",
    ]
    for name, w in corners.items():
        g = find(w)
        lines.append(f"| {name} ({_w(w)}) | {_pct(g['top1_kept'])} | {_num(g['tau'])} |")
    eq = min(grid, key=lambda g: sum(abs(g[a] - 1 / 3) for a in AGENT_TYPES))
    lines.append(f"| 동일에 가장 가까움 ({_w(eq)}) | {_pct(eq['top1_kept'])} | {_num(eq['tau'])} |")
    lines += ["", "1위가 가장 많이 바뀌는 가중치 5개", "", "| 가중치 | 1위 유지율 | 순위 상관(τ) |", "|---|---|---|"]
    for g in ranked[:5]:
        lines.append(f"| {_w(g)} | {_pct(g['top1_kept'])} | {_num(g['tau'])} |")

    lines += [
        "",
        f"## 2. Softmax 온도 (서비스 기본 {SERVICE_TEMPERATURE})",
        "",
        "온도가 낮을수록 중요도 차이가 가중치에 크게 반영된다. 기준선은 온도 1.5로 만든 가중치다.",
        "",
        "| 온도 | 평균 최대 가중치 | 1위 유지율 | 순위 상관(τ) |",
        "|---|---|---|---|",
    ]
    for t in result["temperatures"]:
        name = "동일 가중치(∞)" if math.isinf(t["temperature"]) else f"{t['temperature']:g}"
        lines.append(f"| {name} | {_num(t['mean_max_weight'])} | {_pct(t['top1_kept'])} | {_num(t['tau'])} |")

    po = result["perturb_overall"]
    lines += [
        "",
        "## 3. 중요도 ±1 (LLM이 중요도를 1점 틀렸을 때)",
        "",
        f"- 전체 1위 유지율 **{_pct(po['top1_kept'])}**, 순위 상관 **{_num(po['tau'])}**",
        "",
        "| 변경 | 세트 수 | 1위 유지율 | 순위 상관(τ) |",
        "|---|---|---|---|",
    ]
    for p in result["perturb"]:
        lines.append(f"| {p['change']} | {p['n']} | {_pct(p['top1_kept'])} | {_num(p['tau'])} |")

    lines += [
        "",
        "## 4. 에이전트 점수 상관 (피어슨)",
        "",
        f"세 점수가 모두 있는 작품-회차 {result['scored_items']}개. 상관이 높을수록 관점이 겹쳐 가중치 영향이 작아진다.",
        "",
        "| 쌍 | 상관계수 |",
        "|---|---|",
    ]
    for pair, r in result["correlation"].items():
        lines.append(f"| {pair} | {_num(r)} |")

    m = result["margin"]
    lines += [
        "",
        "## 5. 기준선 1·2위 최종 점수 차",
        "",
        f"- 평균 {_num(m['mean'])}점, 중앙값 {_num(m['median'])}점 (0~10점 기준)",
        f"- 0.3점 미만으로 붙어 있는 세트: {_pct(m['under_0_3'])} (이런 세트는 가중치가 조금만 바뀌어도 1위가 바뀐다)",
        "",
    ]
    return "\n".join(lines)


def save_grid_plot(grid: list[dict], path: Path) -> bool:
    """삼각 좌표 위에 1위 유지율을 색으로 표시. matplotlib이 없으면 건너뛴다."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    pts = [g for g in grid if g["top1_kept"] is not None]
    if not pts:
        return False
    # 삼각 좌표: 꼭짓점 T=(0,0), N=(1,0), P=(0.5, 0.866)
    xs = [g["narrative"] + g["public"] * 0.5 for g in pts]
    ys = [g["public"] * math.sqrt(3) / 2 for g in pts]
    cs = [g["top1_kept"] * 100 for g in pts]

    fig, ax = plt.subplots(figsize=(5.2, 4.6), dpi=150)
    sc = ax.scatter(xs, ys, c=cs, cmap="viridis", vmin=0, vmax=100, s=150, marker="h", edgecolors="none")
    ax.plot([0, 1, 0.5, 0], [0, 0, math.sqrt(3) / 2, 0], color="#999", lw=0.8)
    for label, (x, y), ha, va in [("Technical", (0, 0), "center", "top"), ("Narrative", (1, 0), "center", "top"),
                                   ("Public", (0.5, math.sqrt(3) / 2), "center", "bottom")]:
        ax.annotate(label, (x, y), ha=ha, va=va, fontsize=9, xytext=(0, -4 if va == "top" else 4),
                    textcoords="offset points")
    ax.set_xlim(-0.18, 1.18)
    ax.set_ylim(-0.12, math.sqrt(3) / 2 + 0.1)
    ax.set_aspect("equal")
    ax.axis("off")
    cbar = fig.colorbar(sc, ax=ax, shrink=0.75)
    cbar.set_label("Top-1 kept vs. LLM weights (%)", fontsize=8)
    ax.set_title("Weight sensitivity (T+N+P=1, step 0.1)", fontsize=10, pad=14)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def run(args) -> Path:
    paths = [Path(p) for p in args.inputs]
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit(f"파일이 없습니다: {', '.join(map(str, missing))}")

    sets = load_sets(paths)
    if not sets:
        sys.exit("CSV에 평가 결과가 없습니다.")
    result = analyze(sets)

    out_dir = Path(args.out_dir) if args.out_dir else BACKEND_DIR / "scripts" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base = out_dir / f"weight_sensitivity_{args.label}_{stamp}"

    with open(f"{base}_grid.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["technical", "narrative", "public", "n", "top1_kept", "tau"])
        writer.writeheader()
        for g in result["grid"]:
            writer.writerow({k: g[k] for k in writer.fieldnames})

    meta = {"label": args.label, "started": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sources": [p.name for p in paths]}
    report = to_markdown(result, meta)
    md_path = Path(f"{base}.md")
    md_path.write_text(report, encoding="utf-8")
    plotted = save_grid_plot(result["grid"], Path(f"{base}_grid.png"))

    print(report)
    print(f"요약: {md_path}\n그리드: {base}_grid.csv" + (f"\n그래프: {base}_grid.png" if plotted else ""))
    return md_path


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="가중치 민감도 분석 (LLM 호출 없음)")
    parser.add_argument("inputs", nargs="+", help="measure_reproducibility.py 원자료 CSV (*_raw.csv)")
    parser.add_argument("--label", default="baseline", help="결과 파일 이름에 붙일 이름")
    parser.add_argument("--out-dir", default="", help="결과 저장 폴더 (기본 scripts/results)")
    run(parser.parse_args())


if __name__ == "__main__":
    main_cli()
