"""
genre_service.py
검색어에서 장르를 추론해 후보군 수집에 쓴다 (사용자가 장르를 고르지 않았을 때만)

왜 필요한가
    후보군은 TMDB에서 '조건에 맞는 인기작 60편'으로 모은다. 장르를 고르지 않으면 검색어와 무관한
    인기작이 후보군이 되어, '잔잔한 힐링 영화' 요청에도 범죄·액션 인기작이 후보로 올라왔다.
    검색어로 장르를 먼저 좁혀 두면, 그 뒤의 유사도 계산과 후보 선정이 관련 있는 작품 안에서 이뤄진다.

흐름
    1) LLM이 장르 목록(tmdb_service.MOVIE_GENRES의 이름) 안에서 0~3개를 고른다 (restrict 프롬프트)
    2) 코드에서 목록에 없는 이름, 중복, 개수 초과를 걸러낸다
    3) 실패하거나 판단할 수 없으면 빈 목록 -> 지금처럼 장르 조건 없이 수집한다

반환 형식
    {"genres": ["드라마", "가족"], "reason": "...", "status": "ok | filtered | empty | fallback"}
"""

import httpx

from .llm_service import call_luxia, extract_json
from .tmdb_service import MOVIE_GENRES

GENRE_NAMES = list(MOVIE_GENRES)
MAX_GENRES = 3
LLM_TEMPERATURE = 0.1   # 같은 검색어에 같은 장르가 나오도록 낮게 설정


def build_genre_prompt(query: str) -> str:
    return f"""사용자의 영화·드라마 검색 요청을 보고, 후보 작품을 모을 때 쓸 장르를 골라줘.

[사용자 요청]
{query}

[고를 수 있는 장르]
{", ".join(GENRE_NAMES)}

[규칙]
1. 반드시 위 목록에 있는 이름만 그대로 써.
2. 요청에 맞는 작품이 가장 많이 속해 있을 장르를 0~{MAX_GENRES}개 골라. 고른 장르 중 하나라도 해당하는 작품을 모은다.
3. 분위기나 감정 표현도 장르로 바꿔서 판단해. (예: 힐링·따뜻한 -> 드라마, 가족, 코미디 / 반전·긴장감 -> 스릴러, 미스터리)
4. 요청과 반대되는 분위기의 장르는 고르지 마. (예: 힐링 요청에 공포, 범죄)
5. 평점·인기·최신작처럼 장르와 관계없는 요청이면 빈 목록으로 둬.
6. 아래 JSON 형식 외에 다른 말은 절대 하지 마.

출력 형식:
{{"genres": ["장르1", "장르2"], "reason": "고른 이유 한 문장"}}
"""


def validate_genres(raw: str | None) -> dict:
    parsed = extract_json(raw) if raw else None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("genres"), list):
        return {"genres": [], "reason": "장르 추론 결과를 해석하지 못함", "status": "fallback"}

    picked, rejected = [], []
    for name in parsed["genres"]:
        name = str(name).strip()
        if name not in MOVIE_GENRES:
            rejected.append(name)
        elif name not in picked:
            picked.append(name)

    over = len(picked) > MAX_GENRES
    picked = picked[:MAX_GENRES]
    reason = str(parsed.get("reason", "")).strip() or "-"

    if not picked:
        return {"genres": [], "reason": reason, "status": "empty"}
    return {"genres": picked, "reason": reason,
            "status": "filtered" if rejected or over else "ok"}


async def infer_genres(client: httpx.AsyncClient, query: str) -> dict:
    """검색어로 장르를 추론한다. 검색어가 없거나 실패하면 빈 목록."""
    if not (query or "").strip():
        return {"genres": [], "reason": "검색어 없음", "status": "empty"}

    try:
        raw = await call_luxia(
            client, "너는 검색 의도를 분석하는 AI야.", build_genre_prompt(query),
            temperature=LLM_TEMPERATURE, max_tokens=150,
        )
    except Exception as e:
        print(f"[genre_service] 장르 추론 호출 실패: {e}")
        raw = None

    result = validate_genres(raw)
    print(f"[genre_service] {query!r} -> {result['genres']} ({result['status']})")
    return result
