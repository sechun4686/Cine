
from typing import Any


def _score_text(evaluation: dict[str, Any]) -> str:
    score = evaluation.get("score")
    return "평가 실패 (근거로 사용하지 말 것)" if score is None else str(score)


def build_editor_prompt(
    query: str,
    best: dict[str, Any],
    technical: dict[str, Any],
    narrative: dict[str, Any],
    public: dict[str, Any],
) -> str:
    """세 에이전트의 평가를 바탕으로 최종 추천 프롬프트를 만든다."""

    return f"""
너는 Cine Agent의 Lead Editor다.

사용자 요청:
{query or "특별한 검색어 없음"}

최종 추천 작품:
{best.get("title", "제목 정보 없음")}

줄거리:
{best.get("overview", "")}

Technical 평가:
점수: {_score_text(technical)}
근거: {technical.get("reason", "")}

Narrative 평가:
점수: {_score_text(narrative)}
근거: {narrative.get("reason", "")}

Public 평가:
점수: {_score_text(public)}
근거: {public.get("reason", "")}

위 평가를 종합해 한국어로 4~5문장의 추천 총평을 작성하라.
최종 추천 작품의 제목과 줄거리에 맞는 내용만 작성하라.
세 분야의 강점과 사용자 요청에 부합하는 이유를 설명하라.
다른 후보 작품을 최종 추천 작품으로 혼동하지 마라.
총평만 출력하라.
""".strip()
