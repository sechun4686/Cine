import asyncio

from services import single_agent_service as single
from services.agent_prompts import build_single_prompt
from services.agent_tools import TOOL_REGISTRY

GOOD = ('{"technical": {"reason": "기술 근거", "score": 8}, '
        '"narrative": {"reason": "서사 근거", "score": 6}, '
        '"public": {"reason": "대중 근거", "score": 12}}')


def test_parse_all_perspectives_and_clamp():
    r = single.parse_single_response(GOOD)
    assert r["technical"] == {"score": 8.0, "reason": "기술 근거", "failed": False}
    assert r["narrative"]["score"] == 6.0
    assert r["public"]["score"] == 10.0          # 0~10 보정


def test_parse_missing_perspective_fails_only_that_one():
    r = single.parse_single_response('{"technical": {"reason": "a", "score": 7}, "narrative": "망가짐"}')
    assert r["technical"]["failed"] is False
    assert r["narrative"]["failed"] is True and r["narrative"]["score"] is None
    assert r["public"]["failed"] is True


def test_parse_garbage_fails_all():
    r = single.parse_single_response("점수는 8점입니다")
    assert all(v["failed"] for v in r.values())


def test_prompt_contains_all_criteria_and_data():
    data = {"technical": {"director": "봉준호"}, "narrative": {"overview_full": "줄거리"},
            "public": {"vote_stats": "8.5 (1000표)"}}
    prompt = build_single_prompt({"title": "A작품", "overview": "요약"}, "스릴러", data)
    for text in ("기술적 완성도 평가 전문가", "서사 구조 평가 전문가", "대중성 평가 전문가",
                 "봉준호", "줄거리", "8.5 (1000표)", "[점수 기준]", "[데이터 사용 규칙]"):
        assert text in prompt


async def test_score_retries_when_all_fail(monkeypatch):
    replies = iter(["형식이 틀린 답변", GOOD])

    async def fake_call(*args, **kwargs):
        return next(replies)

    monkeypatch.setattr(single, "call_luxia", fake_call)
    r = await single.score_candidate_single(None, {"title": "t"}, "q", {}, asyncio.Semaphore(1))
    assert r["technical"]["score"] == 8.0


async def test_evaluate_single_ranks_uses_one_call_per_candidate_and_all_tools(monkeypatch):
    calls = []

    async def fake_details(client, candidates):
        return [{} for _ in candidates]

    async def fake_run_tools(ctx, agent_type, names):
        return {n: "값" for n in names}, []

    async def fake_call(client, system, prompt, **kwargs):
        calls.append(prompt)
        if "B작품" in prompt:
            return "망가진 응답"
        if "A작품" in prompt:
            return '{"technical": {"reason": "r", "score": 9}, "narrative": {"reason": "r", "score": 9}}'
        return GOOD

    monkeypatch.setattr(single, "attach_details", fake_details)
    monkeypatch.setattr(single, "run_tools", fake_run_tools)
    monkeypatch.setattr(single, "call_luxia", fake_call)

    events = []
    cands = [{"title": "A작품"}, {"title": "B작품"}, {"title": "C작품"}]
    ranked = await single.evaluate_candidates_single(None, cands, "q", None, events.append)

    # B는 세 관점 모두 실패 -> 제외 (재시도 포함 2회 호출), A·C는 1회씩
    assert [c["title"] for c in ranked] == ["A작품", "C작품"]
    assert len(calls) == 4
    a = ranked[0]
    assert a["failed_agents"] == ["public"] and a["final_score"] == 9.0
    assert a["tool_usage"]["technical"]["tools"] == list(TOOL_REGISTRY["technical"])
    assert a["tool_usage"]["public"]["status"] == "all"

    # 로딩 화면 호환: 관점마다 tools_selected / data_ready / scored 이벤트
    phases = {(e["agent"], e["phase"]) for e in events if e.get("type") == "agent" and e["index"] == 0}
    assert len(phases) == 9


async def test_single_uses_exactly_the_data_multi_selected(monkeypatch):
    """비교 실험(A안): multi가 고른 데이터만 프롬프트에 들어가고, TMDB 조회·도구 실행은 하지 않는다"""
    prompts = []

    async def must_not_call(*args, **kwargs):
        raise AssertionError("데이터가 주어지면 TMDB 조회나 도구 실행을 하면 안 된다")

    async def fake_call(client, system, prompt, **kwargs):
        prompts.append(prompt)
        return GOOD

    monkeypatch.setattr(single, "attach_details", must_not_call)
    monkeypatch.setattr(single, "run_tools", must_not_call)
    monkeypatch.setattr(single, "call_luxia", fake_call)

    multi_result = {"tool_usage": {
        "technical": {"data": {"director": "감독X", "director_filmography": "필모Y"}},
        "narrative": {"data": {"overview_full": "줄거리Z"}},
        "public": {"data": {"vote_stats": "평점W"}},
    }}
    data = single.tool_data_from_multi(multi_result)

    ranked = await single.evaluate_candidates_single(
        None, [{"title": "A작품"}], "q", None, tool_data_list=[data])

    data_block = prompts[0].split("[수집된 데이터]")[1].split("[출력 규칙]")[0]
    for value in ("감독X", "필모Y", "줄거리Z", "평점W"):
        assert value in data_block
    for unselected in ("주요 출연진", "제작사", "키워드", "관객 리뷰"):   # multi가 고르지 않은 도구
        assert unselected not in data_block
    usage = ranked[0]["tool_usage"]
    assert usage["technical"]["tools"] == ["director", "director_filmography"]
    assert usage["public"]["status"] == "from_multi"


async def test_tool_data_list_length_must_match():
    import pytest
    with pytest.raises(ValueError):
        await single.evaluate_candidates_single(None, [{"title": "a"}], "q", tool_data_list=[])
