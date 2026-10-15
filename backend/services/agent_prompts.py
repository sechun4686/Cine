"""
agent_prompts.py
에이전트 프롬프트 모음

    build_select_prompt : 도구 선택용 restrict 프롬프트
    build_prompt        : 수집된 데이터만 근거로 평가하는 프롬프트
"""

from .agent_tools import MAX_TOOLS, MIN_TOOLS, NO_DATA, TOOL_REGISTRY


# ------------------------------------------------------------------
# 1) 도구 선택 (restrict 프롬프트)
# ------------------------------------------------------------------
_ROLE_FOCUS = {
    "technical": "기술적 완성도(감독 역량과 이력, 제작사, 제작 규모, 출연진 구성)",
    "narrative": "서사(줄거리, 캐릭터 발전, 감성, 주제 의식)",
    "public": "대중성(관객 반응, 평점 신뢰도, 인기도, 트렌드)",
}


def build_select_prompt(candidate: dict, query: str, agent_type: str) -> str:
    tools = TOOL_REGISTRY[agent_type]
    tool_lines = "\n".join(f"- {name}: {spec['description']}" for name, spec in tools.items())
    media = "드라마" if candidate.get("media_type") == "tv" else "영화"

    return f"""너는 작품의 {_ROLE_FOCUS[agent_type]}을 평가하기 전에, 어떤 데이터를 조회할지 정하는 역할이야.

[작품]
제목: {candidate.get('title', '')}
유형: {media}
사용자 요청: {query}

[사용 가능한 도구]
{tool_lines}

[규칙]
1. 반드시 위 목록에 있는 도구 이름만 그대로 사용해. 목록에 없는 이름을 만들거나 바꾸지 마.
2. 도구는 {MIN_TOOLS}개 이상 {MAX_TOOLS}개 이하로 골라.
3. 평가 관점과 사용자 요청에 더 필요한 도구부터 순서대로 나열해.
4. reason에는 왜 이 도구들을 골랐는지 1~2문장으로 써.
5. 아래 JSON 형식 외에 다른 말은 절대 하지 마.

출력 형식:
{{"tools": ["도구이름1", "도구이름2"], "reason": "선택 이유"}}
"""


# ------------------------------------------------------------------
# 4) 평가 프롬프트
# ------------------------------------------------------------------
def _base_info(candidate: dict, query: str) -> str:
    """세 에이전트 공통 입력 (평가 관점별 데이터는 도구로 따로 받는다)"""
    overview = (candidate.get("overview") or "")[:200]
    media = "드라마" if candidate.get("media_type") == "tv" else "영화"
    return f"""[기본 정보]
작품 제목: {candidate.get('title', '')}
유형: {media}
줄거리 요약: {overview or NO_DATA}
사용자 요청: {query}
"""


def _format_tool_data(agent_type: str, tool_data: dict) -> str:
    registry = TOOL_REGISTRY[agent_type]
    lines = [f"- {registry[name]['label']}: {value}" for name, value in tool_data.items()]
    return "[수집된 데이터]\n" + "\n".join(lines) + "\n"


_ROLE_PROMPTS = {
    "technical": """너는 영화·드라마 기술적 완성도 평가 전문가야.
수집된 데이터로 확인할 수 있는 제작 역량(감독 이력과 필모그래피 평점, 제작사, 제작 규모, 상영 시간, 출연진 구성)을 근거로
이 작품이 사용자 요청에 맞는 완성도를 갖췄는지 평가해.
reason에는 다음 내용을 포함해:
- 감독의 이력과 필모그래피가 보여주는 연출 역량
- 제작사·제작 규모·출연진 등 제작 수준에 대한 근거
- 이 제작 역량이 사용자 요청과 어떻게 연결되는지
- 촬영, 색감, 카메라 움직임, 음악처럼 데이터에 없는 시각·청각 요소는 묘사하지 마""",
    "narrative": """너는 영화·드라마 서사 구조 평가 전문가야.
줄거리, 캐릭터, 감정선, 주제 의식 관점에서 이 작품이 사용자 요청에 맞는지 평가해.
reason에는 다음 내용을 포함해:
- 줄거리에 드러난 서사 구조와 전개 방식
- 주요 인물의 관계와 감정선
- 사용자 요청 키워드와 어떻게 맞닿는지, 또는 어긋나는지
- 키워드는 한국어로 풀어서 설명할 것""",
    "public": """너는 대중성 평가 전문가야.
평점, 투표 수, 인기도, 개봉 연도, 관객 리뷰를 근거로 이 작품이 대중적으로 검증된 선택인지 평가해.
reason에는 다음 내용을 포함해:
- 평점과 투표 수를 함께 본 평점 신뢰도 (투표 수 100 미만은 신뢰도가 낮고, 1,000 이상은 높은 편으로 판단)
- 인기도와 개봉 연도가 보여주는 화제성
- 리뷰가 있다면 관객 반응의 경향
- 데이터에 없는 트렌드, 흥행 성적, 수상 이력은 언급하지 마""",
}

_DATA_RULES = """[데이터 사용 규칙]
- 평가 근거는 [기본 정보]와 [수집된 데이터]에 있는 내용만 사용해. 작품에 대한 사전 지식으로 내용을 보충하지 마.
- '정보 없음'으로 표시된 항목은 추측하지 말고, 필요하면 정보가 없다고만 언급해.
- 인물 이름, 지명, 배경, 회사 이름은 데이터에 적힌 표기 그대로 써. 바꾸거나 다른 이름으로 대체하지 마.
- 데이터로 확인할 수 없는 특징을 단정하지 마. 확인할 수 없는 부분은 판단 근거에서 제외해.
"""

_SCORE_RULES = """[점수 기준]
점수는 '이 관점에서 이 작품이 사용자 요청을 얼마나 잘 충족하는가'를 0.0~10.0 사이 소수 첫째 자리로 매겨.
- 9.0~10.0: 데이터상 근거가 매우 강하고 사용자 요청과 직접적으로 일치함
- 7.0~8.9: 근거가 뚜렷하고 사용자 요청과 대체로 맞음
- 5.0~6.9: 평범하거나, 요청과 부분적으로만 맞거나, 판단 근거가 부족함
- 3.0~4.9: 요청과 잘 맞지 않거나 이 관점에서 약점이 뚜렷함
- 0.0~2.9: 요청과 반대되거나 이 관점에서 매우 부족함
- 데이터가 부족해 판단 근거가 약하면 6.5점을 넘기지 마.
- 모든 작품에 비슷한 점수를 주지 말고, 위 기준에 따라 근거의 강약을 점수 차이로 드러내.
- reason을 먼저 작성하고, reason에 쓴 근거에 맞춰 score를 정해.
"""


def build_prompt(candidate: dict, query: str, agent_type: str, tool_data: dict) -> str:
    return (
        f"{_ROLE_PROMPTS[agent_type]}\n\n"
        f"{_DATA_RULES}\n"
        f"{_SCORE_RULES}\n"
        f"{_base_info(candidate, query)}\n"
        f"{_format_tool_data(agent_type, tool_data)}\n"
        "[출력 규칙]\n"
        "- reason은 3~4문장으로 써.\n"
        "- 키는 reason과 score 두 개만 사용하고, JSON 외에 다른 말은 절대 하지 마.\n\n"
        "출력 형식:\n"
        '{"reason": "평가 근거 3~4문장", "score": 0.0~10.0 사이 숫자}\n'
    )