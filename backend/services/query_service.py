"""
query_service.py
Query Structuring (Lead Editor의 계획 단계): 자연어 검색어를 후보 수집 조건으로 바꾼다

왜 필요한가
    후보군은 TMDB에서 '조건에 맞는 인기작 60편'으로 모은다. 예전에는 검색어가 이 단계에 쓰이지 않아
    '잔잔한 힐링 영화' 요청에도 범죄·액션 인기작이 후보군에 들어왔다.
    1차 설계의 Input Parser(Query Structuring)를 장르 조건 추출로 좁혀 복원한다.
    OTT·매체·길이·장르는 입력 화면에서 이미 구조화된 값으로 받으므로 LLM이 다시 뽑지 않는다.

출력
    include_genres : 이 중 하나라도 해당하는 작품을 모은다 (사용자가 장르를 고르지 않았을 때만 사용)
    exclude_genres : 요청과 반대되는 분위기의 장르. 해당하면 후보군에서 뺀다
                     (사용자가 직접 고른 장르는 빼지 않는다)

흐름
    1) LLM이 장르 목록(tmdb_service.MOVIE_GENRES의 이름) 안에서 포함 0~3개, 제외 0~4개를 고른다
    2) 코드에서 목록에 없는 이름, 중복, 개수 초과, 포함·제외 겹침을 걸러낸다
    3) 실패하거나 판단할 수 없으면 빈 목록 -> 장르 조건 없이 수집한다

반환 형식
    {"include_genres": [...], "exclude_genres": [...], "reason": "...",
     "status": "ok | filtered | empty | fallback"}
"""

import httpx

from .llm_service import call_luxia, extract_json
from .tmdb_service import MOVIE_GENRES

GENRE_NAMES = list(MOVIE_GENRES)
MAX_INCLUDE = 3
MAX_EXCLUDE = 4
LLM_TEMPERATURE = 0.1   # 같은 검색어에 같은 조건이 나오도록 낮게 설정


def empty_structure(reason: str, status: str) -> dict:
    return {"include_genres": [], "exclude_genres": [], "reason": reason, "status": status}


def build_structure_prompt(query: str) -> str:
    return f"""사용자의 영화·드라마 검색 요청을 보고, 후보 작품을 모을 때 쓸 장르 조건을 정해줘.

[사용자 요청]
{query}

[고를 수 있는 장르]
{", ".join(GENRE_NAMES)}

[규칙]
1. 반드시 위 목록에 있는 이름만 그대로 써.
2. include_genres: 요청에 맞는 작품이 가장 많이 속해 있을 장르를 0~{MAX_INCLUDE}개 골라. 고른 장르 중 하나라도 해당하는 작품을 모은다.
3. exclude_genres: 요청과 분위기가 반대라서 후보에서 빼야 할 장르를 0~{MAX_EXCLUDE}개 골라.
   요청과 확실히 어긋나는 장르만 고르고, 애매하면 고르지 마. 같은 장르를 include와 exclude에 동시에 넣지 마.
4. 분위기나 감정 표현도 장르로 바꿔서 판단해.
   예) 잔잔한 힐링 영화 -> include: 드라마, 가족, 코미디 / exclude: 공포, 범죄, 스릴러, 전쟁
   예) 반전 있는 스릴러 -> include: 스릴러, 미스터리 / exclude: 가족, 애니메이션
5. 평점·인기·최신작처럼 장르와 관계없는 요청이면 두 목록 모두 비워 둬.
6. 아래 JSON 형식 외에 다른 말은 절대 하지 마.

출력 형식:
{{"include_genres": ["장르1"], "exclude_genres": ["장르2"], "reason": "판단 이유 한 문장"}}
"""


def _pick(values, limit: int) -> tuple[list[str], bool]:
    """목록에 있는 이름만 순서대로, 중복 없이 limit개까지. return: (선택, 걸러낸 것이 있는지)"""
    picked, changed = [], False
    for name in values if isinstance(values, list) else []:
        name = str(name).strip()
        if name not in MOVIE_GENRES or name in picked:
            changed = True
        else:
            picked.append(name)
    if len(picked) > limit:
        picked, changed = picked[:limit], True
    return picked, changed


def validate_structure(raw: str | None) -> dict:
    parsed = extract_json(raw) if raw else None
    if not isinstance(parsed, dict) or not any(
        isinstance(parsed.get(k), list) for k in ("include_genres", "exclude_genres")
    ):
        return empty_structure("검색어 구조화 결과를 해석하지 못함", "fallback")

    include, inc_changed = _pick(parsed.get("include_genres"), MAX_INCLUDE)
    exclude, exc_changed = _pick(parsed.get("exclude_genres"), MAX_EXCLUDE)

    overlap = [g for g in exclude if g in include]   # 겹치면 포함을 우선한다
    exclude = [g for g in exclude if g not in include]

    reason = str(parsed.get("reason", "")).strip() or "-"
    if not include and not exclude:
        return empty_structure(reason, "empty")
    return {"include_genres": include, "exclude_genres": exclude, "reason": reason,
            "status": "filtered" if inc_changed or exc_changed or overlap else "ok"}


async def structure_query(client: httpx.AsyncClient, query: str) -> dict:
    """검색어를 후보 수집 조건으로 구조화한다. 검색어가 없거나 실패하면 빈 조건."""
    if not (query or "").strip():
        return empty_structure("검색어 없음", "empty")

    try:
        raw = await call_luxia(
            client, "너는 검색 의도를 분석하는 AI야.", build_structure_prompt(query),
            temperature=LLM_TEMPERATURE, max_tokens=200,
        )
    except Exception as e:
        print(f"[query_service] 검색어 구조화 호출 실패: {e}")
        raw = None

    result = validate_structure(raw)
    print(f"[query_service] {query!r} -> 포함 {result['include_genres']} / 제외 {result['exclude_genres']}"
          f" ({result['status']})")
    return result
