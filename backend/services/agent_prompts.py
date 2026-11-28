"""
agent_prompts.py
에이전트 프롬프트 모음

    build_select_prompt : 도구 선택용 restrict 프롬프트
    build_prompt        : 수집된 데이터만 근거로 평가하는 프롬프트
"""

from .agent_tools import MAX_TOOLS, NO_DATA, REQUIRED_TOOLS, TOOL_REGISTRY


# ------------------------------------------------------------------
# 1) 도구 선택 (restrict 프롬프트)
# ------------------------------------------------------------------
_ROLE_FOCUS = {
    "technical": "기술적 완성도(감독 역량과 이력, 제작사, 제작 규모, 출연진 구성)",
    "narrative": "서사(줄거리, 캐릭터 발전, 감성, 주제 의식)",
    "public": "대중성(관객 반응, 평점 신뢰도, 인기도, 트렌드)",
}


def _required_line(agent_type: str) -> str:
    return ", ".join(REQUIRED_TOOLS[agent_type])


def build_select_prompt(candidate: dict, query: str, agent_type: str) -> str:
    tools = TOOL_REGISTRY[agent_type]
    tool_lines = "\n".join(f"- {name}: {spec['description']}" for name, spec in tools.items())
    media = "드라마" if candidate.get("media_type") == "tv" else "영화"
    extra_max = MAX_TOOLS - len(REQUIRED_TOOLS[agent_type])

    return f"""너는 작품의 {_ROLE_FOCUS[agent_type]}을 평가하기 전에, 어떤 데이터를 조회할지 정하는 역할이야.

[작품]
제목: {candidate.get('title', '')}
유형: {media}
사용자 요청: {query}

[사용 가능한 도구]
{tool_lines}

[규칙]
1. 반드시 위 목록에 있는 도구 이름만 그대로 사용해. 목록에 없는 이름을 만들거나 바꾸지 마.
2. 핵심 도구({_required_line(agent_type)})는 항상 포함돼. 이 작품과 사용자 요청을 판단하는 데
   추가로 필요한 도구가 있으면 나머지 중에서 0~{extra_max}개를 더 골라.
3. 평가 관점과 사용자 요청에 더 필요한 도구부터 순서대로 나열해.
4. reason에는 왜 그 추가 도구를 골랐는지(또는 고르지 않았는지) 1~2문장으로 써.
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
- 촬영, 색감, 카메라 움직임, 음악처럼 데이터에 없는 시각·청각 요소는 묘사하지 마
[이 관점의 점수 기준]
점수는 '제작 역량이 데이터로 얼마나 검증되었는가'를 나타낸다.
- 사용자 요청이 연출, 영상미, 감독, 배우, 제작 규모를 직접 언급할 때만 그 조건과의 일치를 점수에 반영해.
  그 외에는 줄거리·분위기·장르가 요청에 맞는지는 점수에 반영하지 마 (서사 관점이 판단한다).
- 감독 필모그래피 평균 평점이 7.0 이상이고 연출작이 여러 편이면 높은 점수(8.0 이상)의 근거가 된다.
- 필모그래피 평균 평점이 6.0 미만이거나 연출작이 1편 이하면 6.5점 이하로 매겨.
- 감독 이력 정보가 없으면 6.5점을 넘기지 마.""",
    "narrative": """너는 영화·드라마 서사 구조 평가 전문가야.
줄거리, 캐릭터, 감정선, 주제 의식 관점에서 이 작품이 사용자 요청에 맞는지 평가해.
reason에는 다음 내용을 포함해:
- 줄거리에 드러난 서사 구조와 전개 방식
- 주요 인물의 관계와 감정선
- 사용자 요청 키워드와 어떻게 맞닿는지, 또는 어긋나는지
- 키워드는 한국어로 풀어서 설명할 것
[이 관점의 점수 기준]
점수는 '줄거리·인물·감정선·주제가 사용자 요청의 분위기와 내용에 얼마나 맞는가'를 나타낸다.
요청에 대한 내용 적합도는 이 관점이 판단한다.
- 줄거리와 키워드가 요청의 분위기·소재와 직접 일치하면 8.0 이상, 일부만 맞으면 5.0~6.9.
- 요청과 반대되는 분위기나 소재(예: 힐링 요청에 잔혹한 범죄)가 중심이면 4.9 이하.
- 평점, 인기도, 감독의 명성은 점수에 반영하지 마 (다른 관점이 판단한다).""",
    "public": """너는 대중성 평가 전문가야.
평점, 투표 수, 인기도, 개봉 연도, 관객 리뷰를 근거로 이 작품이 대중적으로 검증된 선택인지 평가해.
reason에는 다음 내용을 포함해:
- 평점과 투표 수를 함께 본 평점 신뢰도 (투표 수 100 미만은 신뢰도가 낮고, 1,000 이상은 높은 편으로 판단)
- 인기도와 개봉 연도가 보여주는 화제성
- 리뷰가 있다면 관객 반응의 경향 (영어 리뷰는 한국어로 요약해서 설명할 것)
- 데이터에 없는 트렌드, 흥행 성적, 수상 이력은 언급하지 마
[이 관점의 점수 기준]
점수는 '관객에게 얼마나 검증된 작품인가'를 나타낸다.
- 사용자 요청이 평점, 인기, 화제작, 숨은 명작, 최신작을 직접 언급할 때만 그 조건과의 일치를 점수에 반영해.
  그 외에는 줄거리·분위기·장르가 요청에 맞는지는 점수에 반영하지 마 (서사 관점이 판단한다).
- 투표 수 1,000 이상이고 평점 7.5 이상: 8.0 이상
- 투표 수 1,000 이상이고 평점 6.5~7.4: 6.0~7.9
- 투표 수 100~999: 평점이 높아도 7.5 이하
- 투표 수 100 미만: 6.0 이하
- 리뷰 반응이 뚜렷하게 긍정적이거나 부정적이면 위 범위 안에서 1점까지 조정해.""",
}

_DATA_RULES = """[데이터 사용 규칙]
- 평가 근거는 [기본 정보]와 [수집된 데이터]에 있는 내용만 사용해. 작품에 대한 사전 지식으로 내용을 보충하지 마.
- '정보 없음'으로 표시된 항목은 추측하지 말고, 필요하면 정보가 없다고만 언급해.
- 인물 이름, 지명, 배경, 회사 이름은 데이터에 적힌 표기 그대로 써. 바꾸거나 다른 이름으로 대체하지 마.
- 데이터로 확인할 수 없는 특징을 단정하지 마. 확인할 수 없는 부분은 판단 근거에서 제외해.
"""

_SCORE_RULES = """[점수 기준]
점수는 관점마다 정해진 [이 관점의 점수 기준]에 따라 0.0~10.0 사이 소수 첫째 자리로 매겨.
- 9.0~10.0: 이 관점에서 데이터상 근거가 매우 강함
- 7.0~8.9: 이 관점에서 근거가 뚜렷하고 강점이 있음
- 5.0~6.9: 이 관점에서 평범하거나, 판단 근거가 부족함
- 3.0~4.9: 이 관점에서 약점이 뚜렷함
- 0.0~2.9: 이 관점에서 매우 부족함
- 이 관점의 점수 기준에 없는 요소(다른 관점이 판단하는 요소)는 점수에 반영하지 마.
  세 관점의 점수를 합치는 일은 사용자 요청에 따라 정한 가중치가 맡는다.
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

# ------------------------------------------------------------------
# 5) Single-agent 비교 실험용 평가 프롬프트
#    multi-agent와 같은 평가 기준(_ROLE_PROMPTS), 데이터 규칙, 점수 기준을 쓰되
#    LLM 한 번 호출로 세 관점을 모두 평가한다.
# ------------------------------------------------------------------
_SINGLE_SECTION_TITLES = {
    "technical": "기술적 완성도",
    "narrative": "서사",
    "public": "대중성",
}


def _format_all_tool_data(tool_data: dict) -> str:
    """tool_data: {관점: {도구명: 값}} -> 관점별로 묶은 데이터 블록"""
    blocks = []
    for agent_type, data in tool_data.items():
        registry = TOOL_REGISTRY[agent_type]
        lines = [f"- {registry[name]['label']}: {value}" for name, value in data.items()]
        blocks.append(f"({_SINGLE_SECTION_TITLES[agent_type]} 관련)\n" + "\n".join(lines))
    return "[수집된 데이터]\n" + "\n\n".join(blocks) + "\n"


def build_single_prompt(candidate: dict, query: str, tool_data: dict) -> str:
    criteria = "\n\n".join(
        f"[{key}: {_SINGLE_SECTION_TITLES[key]} 평가 기준]\n{_ROLE_PROMPTS[key]}"
        for key in _SINGLE_SECTION_TITLES
    )
    return (
        "너는 영화·드라마를 기술적 완성도, 서사, 대중성 세 관점에서 평가하는 전문가야.\n"
        "아래 세 관점의 평가 기준에 따라 이 작품이 사용자 요청에 맞는지 관점마다 따로 평가해.\n\n"
        f"{criteria}\n\n"
        f"{_DATA_RULES}\n"
        f"{_SCORE_RULES}\n"
        f"{_base_info(candidate, query)}\n"
        f"{_format_all_tool_data(tool_data)}\n"
        "[출력 규칙]\n"
        "- 관점마다 reason은 3~4문장으로 써.\n"
        "- 최상위 키는 technical, narrative, public 세 개만 사용하고, 각각 reason과 score를 가져.\n"
        "- JSON 외에 다른 말은 절대 하지 마.\n\n"
        "출력 형식:\n"
        '{"technical": {"reason": "평가 근거 3~4문장", "score": 0.0~10.0 사이 숫자}, '
        '"narrative": {"reason": "...", "score": 숫자}, '
        '"public": {"reason": "...", "score": 숫자}}\n'
    )


def build_single_select_prompt(candidate: dict, query: str) -> str:
    """
    Single-agent(full) 도구 선택 프롬프트.
    세 관점의 도구 14개를 한 목록으로 주고, multi-agent와 같은 제약(관점마다 MIN~MAX개)으로 고르게 한다.
    """
    sections = []
    for agent_type in ("technical", "narrative", "public"):
        tools = TOOL_REGISTRY[agent_type]
        lines = "\n".join(f"- {name}: {spec['description']}" for name, spec in tools.items())
        sections.append(f"({_SINGLE_SECTION_TITLES[agent_type]} 평가용)\n{lines}")
    media = "드라마" if candidate.get("media_type") == "tv" else "영화"
    required = "; ".join(
        f"{_SINGLE_SECTION_TITLES[a]}: {_required_line(a)}" for a in ("technical", "narrative", "public")
    )
    extra_max = MAX_TOOLS - max(len(t) for t in REQUIRED_TOOLS.values())

    return f"""너는 작품을 기술적 완성도, 서사, 대중성 세 관점에서 평가하기 전에, 어떤 데이터를 조회할지 정하는 역할이야.

[작품]
제목: {candidate.get('title', '')}
유형: {media}
사용자 요청: {query}

[사용 가능한 도구]
{chr(10).join(sections)}

[규칙]
1. 반드시 위 목록에 있는 도구 이름만 그대로 사용해. 목록에 없는 이름을 만들거나 바꾸지 마.
2. 관점마다 핵심 도구는 항상 포함돼 ({required}).
   이 작품과 사용자 요청을 판단하는 데 추가로 필요한 도구가 있으면 관점마다 나머지 중에서 0~{extra_max}개를 더 골라.
3. 평가와 사용자 요청에 더 필요한 도구부터 순서대로 나열해.
4. reason에는 왜 그 추가 도구를 골랐는지(또는 고르지 않았는지) 1~2문장으로 써.
5. 아래 JSON 형식 외에 다른 말은 절대 하지 마.

출력 형식:
{{"tools": ["도구이름1", "도구이름2", "..."], "reason": "선택 이유"}}
"""
