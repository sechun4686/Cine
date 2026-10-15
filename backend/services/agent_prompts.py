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
    "technical": "기술적 완성도(감독 역량, 촬영 기법, 제작 수준, 영상미)",
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
감독 역량, 촬영 기법, 제작 수준, 영상미 관점에서 0~10점으로 평가해줘.
reason은 반드시 3~4문장으로 써줘. 다음 내용을 포함해:
- 감독의 연출 스타일과 역량
- 촬영·영상미의 특징
- 제작 완성도가 사용자 요청과 어떻게 연결되는지""",
    "narrative": """너는 영화·드라마 서사 구조 평가 전문가야.
줄거리, 캐릭터 발전, 감성, 주제 의식 관점에서 0~10점으로 평가해줘.
reason은 반드시 3~4문장으로 써줘. 다음 내용을 포함해:
- 서사 구조와 전개 방식
- 캐릭터의 감성선과 성장
- 사용자 요청 키워드와 어떻게 맞닿는지
- 키워드는 한국어로 풀어서 설명할 것""",
    "public": """너는 대중성 및 트렌드 평가 전문가야.
관객 반응, TMDB 평점 신뢰도, 인기도, 트렌드 부합 여부를 0~10점으로 평가해줘.
reason은 반드시 3~4문장으로 써줘. 다음 내용을 포함해:
- 평점과 관객 반응의 신뢰도
- 인기도 수준과 그 의미
- 현재 트렌드나 사용자 요청과의 부합 여부""",
}

_DATA_RULES = """[데이터 사용 규칙]
- 평가 근거는 [기본 정보]와 [수집된 데이터]에 있는 내용만 사용해.
- '정보 없음'으로 표시된 항목은 추측하지 말고, 필요하면 정보가 없다고만 언급해.
- 감독, 배우, 제작사 같은 고유명사는 데이터에 적힌 그대로만 써. 데이터에 없는 이름을 만들지 마.
"""


def build_prompt(candidate: dict, query: str, agent_type: str, tool_data: dict) -> str:
    return (
        f"{_ROLE_PROMPTS[agent_type]}\n"
        f"{_DATA_RULES}"
        "절대로 키는 score와 reason 두 개만. 다른 말 하지 마.\n\n"
        f"{_base_info(candidate, query)}\n"
        f"{_format_tool_data(agent_type, tool_data)}\n"
        '출력 형식:\n{"score": 8.5, "reason": "상세 설명 3~4문장"}\n'
    )