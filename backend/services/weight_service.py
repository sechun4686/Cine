"""
weight_service.py
사용자 쿼리로 Technical / Narrative / Public 가중치를 정하는 서비스

흐름
    1) LLM이 쿼리를 보고 T/N/P 중요도를 0~5 정수로만 추론한다 (restrict 프롬프트)
    2) 코드에서 응답을 검증한다 (키 누락, 숫자 아님, 범위 밖 처리)
    3) Softmax(calculate_weights)가 중요도를 실제 가중치로 변환한다
    4) 어느 단계든 실패하면 동일 가중치(1/3씩)를 사용한다

LLM에게는 가중치 자체가 아니라 중요도 점수만 맡기고,
가중치 계산은 Python Softmax가 하기 때문에 같은 점수면 항상 같은 가중치가 나온다.

사용 예 (main.py)
    weight_result = await infer_weights(client, request.query, request.refinements)
    ranked = await evaluate_candidates(client, candidates, request.query, weight_result["weights"])

반환 형식
    {
        "weights": {"technical": 0.2, "narrative": 0.6, "public": 0.2},
        "importance": {"technical": 2, "narrative": 5, "public": 2},
        "reason": "LLM이 설명한 판단 근거",
        "status": "ok | filtered | fallback",
    }
"""

import math

import httpx

from .llm_service import call_luxia, extract_json

KEYS = ["technical", "narrative", "public"]

DEFAULT_WEIGHTS = {
    "technical": 1 / 3,
    "narrative": 1 / 3,
    "public": 1 / 3,
}

SCORE_MIN = 0
SCORE_MAX = 5
SOFTMAX_TEMPERATURE = 1.5
LLM_TEMPERATURE = 0.1   # 같은 쿼리에 같은 중요도가 나오도록 낮게 설정


# ------------------------------------------------------------------
# Softmax 가중치 계산 (친구 작성 코드)
# ------------------------------------------------------------------
def calculate_weights(
    importance_scores: dict,
    temperature: float = SOFTMAX_TEMPERATURE,
) -> dict:
    """T/N/P 중요도 점수를 Softmax 가중치로 변환한다."""

    keys = ["technical", "narrative", "public"]

    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("temperature must be positive")

    values = []

    for key in keys:
        score = float(importance_scores[key])

        if not math.isfinite(score) or not 0 <= score <= 5:
            raise ValueError(f"Invalid importance score: {key}")

        values.append(score / temperature)

    max_value = max(values)
    exp_values = [math.exp(v - max_value) for v in values]
    total = sum(exp_values)

    return {
        key: value / total
        for key, value in zip(keys, exp_values)
    }


def get_default_weights() -> dict:
    """가중치 분석 실패 시 동일 가중치를 반환한다."""
    return DEFAULT_WEIGHTS.copy()


# ------------------------------------------------------------------
# 1) LLM 중요도 추론 프롬프트 (restrict)
# ------------------------------------------------------------------
def build_importance_prompt(query: str, refinements: list[str] | None = None) -> str:
    refinements = [r.strip() for r in (refinements or []) if r and r.strip()]
    refine_block = ""
    refine_rule = ""
    if refinements:
        lines = "\n".join(f"{i}. {r}" for i, r in enumerate(refinements, 1))
        refine_block = f"""
[결과를 본 사용자가 덧붙인 추가 요청 (번호가 클수록 최근)]
{lines}
"""
        refine_rule = """5. 추가 요청은 앞선 추천이 마음에 들지 않아 방향을 바꾸려는 것이야. 원래 검색어보다 추가 요청을 우선하고, 최근 요청일수록 더 중요하게 봐.
6. 추가 요청이 직접 요구하는 관점(예: '유명한', '인기 있는' -> public / '연출이 좋은', '영상미' -> technical / '이야기가 탄탄한' -> narrative)은 4~5점을 줘.
7. reason에는 추가 요청을 어떻게 반영했는지도 써.
"""
    return f"""너는 사용자의 콘텐츠 검색어를 보고, 세 가지 평가 관점이 각각 얼마나 중요한지 판단하는 역할이야.

[평가 관점]
- technical: 연출, 촬영, 영상미, 음악, 연기, 제작 완성도
- narrative: 줄거리, 캐릭터, 감정선, 분위기, 주제 의식, 장르적 재미
- public: 대중적 인기, 평점, 화제성, 최신 트렌드, 많은 사람이 본 작품인지

[점수 기준]
- 0: 검색어와 전혀 관련 없음
- 1~2: 간접적으로만 관련 있음
- 3: 어느 정도 관련 있음
- 4~5: 검색어에서 직접 요구하는 핵심 요소

[규칙]
1. 세 관점 모두 0~5 사이의 정수로만 점수를 매겨.
2. 키는 technical, narrative, public, reason 네 개만 사용해.
3. reason에는 점수를 그렇게 준 이유를 1~2문장으로 써.
4. 아래 JSON 형식 외에 다른 말은 절대 하지 마.
{refine_rule}
[사용자 검색어]
{query}
{refine_block}
출력 형식:
{{"technical": 3, "narrative": 4, "public": 2, "reason": "판단 이유"}}
"""


# ------------------------------------------------------------------
# 2) LLM 응답 검증
# ------------------------------------------------------------------
def validate_importance(raw: str | None) -> tuple[dict | None, str, str]:
    """
    LLM 응답에서 T/N/P 중요도를 꺼내 검증한다.
    return: (중요도 dict 또는 None, reason, status)
        ok       : 응답을 그대로 사용
        filtered : 범위 밖 값을 0~5로 보정하거나 소수를 반올림함
        fallback : 해석 실패 (None 반환)
    """
    parsed = extract_json(raw) if raw else None
    if not parsed:
        return None, "중요도 응답을 해석하지 못함", "fallback"

    importance = {}
    changed = False

    for key in KEYS:
        if key not in parsed:
            return None, f"{key} 점수가 응답에 없음", "fallback"

        try:
            score = float(parsed[key])
        except (TypeError, ValueError):
            return None, f"{key} 점수가 숫자가 아님", "fallback"

        if not math.isfinite(score):
            return None, f"{key} 점수가 유효하지 않음", "fallback"

        fixed = int(round(min(max(score, SCORE_MIN), SCORE_MAX)))
        if fixed != score:
            changed = True
        importance[key] = fixed

    reason = str(parsed.get("reason", "")).strip() or "-"
    return importance, reason, "filtered" if changed else "ok"


# ------------------------------------------------------------------
# 3) 전체 흐름
# ------------------------------------------------------------------
def _rounded(weights: dict) -> dict:
    """응답/화면 표시용으로 소수 셋째 자리까지 반올림"""
    return {k: round(v, 3) for k, v in weights.items()}


def combine_query(query: str, refinements: list[str] | None = None) -> str:
    """원래 검색어와 추가 요청을 하나의 문장으로 합친다 (유사도 계산, 에이전트 평가, 총평, 로그용)"""
    parts = [(query or "").strip(), *[(r or "").strip() for r in (refinements or [])]]
    return ", ".join(p for p in parts if p)


async def infer_weights(client: httpx.AsyncClient, query: str, refinements: list[str] | None = None) -> dict:
    """
    쿼리로 T/N/P 가중치를 계산한다. 실패하면 동일 가중치를 반환한다.
    refinements: 결과 화면에서 덧붙인 추가 요청. 원래 검색어와 구분해서 프롬프트에 넣어 우선 반영한다.
    """
    refinements = [r.strip() for r in (refinements or []) if r and r.strip()]

    if not (query or "").strip() and not refinements:
        return {
            "weights": _rounded(get_default_weights()),
            "importance": None,
            "reason": "검색어가 없어 동일 가중치를 사용함",
            "status": "fallback",
        }

    try:
        raw = await call_luxia(
            client,
            "너는 검색 의도를 분석하는 AI야.",
            build_importance_prompt(query, refinements),
            temperature=LLM_TEMPERATURE,
            max_tokens=200,
        )
    except Exception as e:
        print(f"[weight_service] 중요도 추론 호출 실패: {e}")
        raw = None

    importance, reason, status = validate_importance(raw)

    if importance is None:
        print(f"[weight_service] 가중치 fallback ({reason}) query={query!r}")
        return {
            "weights": _rounded(get_default_weights()),
            "importance": None,
            "reason": f"{reason}. 동일 가중치를 사용함",
            "status": "fallback",
        }

    weights = calculate_weights(importance)

    return {
        "weights": _rounded(weights),
        "importance": importance,
        "reason": reason,
        "status": status,
    }