"""
rerecommend_service.py
피드백 기반 재추천: 이미 평가한 후보 중에서 다음 추천작을 고른다 (LLM 평가 재실행 없음)

피드백 종류
    watched        : "이미 봤어요"  -> 취향은 맞았다고 보고, 남은 후보 중 최종 점수가 가장 높은 작품
    not_interested : "관심 없어요"  -> 이전 추천의 강점(가장 높은 관점 점수)과 다른 강점을 가진 작품 우선

안전장치
    최종 점수가 MIN_RERECOMMEND_SCORE 미만인 후보는 재추천하지 않는다.
    고를 후보가 없으면 None -> 프론트가 제외 목록을 붙여 새로 검색한다.

입력 후보는 main._format_candidate 형식 ({"scores": {...}, "final_score": ..., "media_type", "id"})
"""

AGENT_TYPES = ["technical", "narrative", "public"]
AGENT_LABELS = {"technical": "기술적 완성도", "narrative": "서사·감성", "public": "대중성"}
# 조사(이/가)까지 붙인 표기: 완성도 -> 가, 감성/대중성 -> 이
AGENT_SUBJECTS = {"technical": "기술적 완성도가", "narrative": "서사·감성이", "public": "대중성이"}

MIN_RERECOMMEND_SCORE = 5.0
FEEDBACK_TYPES = ("watched", "not_interested")


def candidate_key(item: dict) -> str:
    return f"{item.get('media_type') or 'movie'}:{item.get('id')}"


def dominant_agent(item: dict) -> str | None:
    """가장 높은 점수를 받은 관점 (실패한 관점은 제외). 동점이면 AGENT_TYPES 순서."""
    scores = item.get("scores") or {}
    valid = [(scores[a], -i, a) for i, a in enumerate(AGENT_TYPES) if scores.get(a) is not None]
    return max(valid)[2] if valid else None


def pick_alternative(rejected: dict, candidates: list[dict], feedback: str) -> tuple[dict | None, str]:
    """
    return: (다음 추천 후보 또는 None, 화면에 보여줄 설명)
    """
    rejected_key = candidate_key(rejected)
    pool = [
        c for c in candidates
        if candidate_key(c) != rejected_key
        and c.get("final_score") is not None
        and c["final_score"] >= MIN_RERECOMMEND_SCORE
    ]
    title = rejected.get("title", "이전 작품")

    if not pool:
        return None, f"남은 후보 중 {MIN_RERECOMMEND_SCORE:.0f}점 이상인 작품이 없어 새로 찾아볼게요."

    by_score = lambda c: c["final_score"]

    if feedback == "not_interested":
        prev = dominant_agent(rejected)
        different = [c for c in pool if dominant_agent(c) != prev]
        if different:
            pick = max(different, key=by_score)
            return pick, (
                f"'{title}'은(는) {AGENT_SUBJECTS.get(prev, '다른 관점이')} 강점이었어요. "
                f"이번에는 {AGENT_SUBJECTS[dominant_agent(pick)]} 돋보이는 작품을 골랐어요."
            )
        pick = max(pool, key=by_score)
        return pick, "강점이 다른 후보가 없어, 남은 후보 중 점수가 가장 높은 작품을 골랐어요."

    pick = max(pool, key=by_score)
    return pick, f"'{title}' 다음으로 요청에 잘 맞는 작품이에요."
