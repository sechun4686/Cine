import asyncio

from services import agent_service
from services.agent_service import calculate_final_score, parse_agent_response


def test_parse_valid_and_clamped():
    r = parse_agent_response('{"reason": "좋음", "score": 12}')
    assert r == {"score": 10.0, "reason": "좋음", "failed": False}


def test_parse_failure_has_no_score():
    r = parse_agent_response("점수는 8점입니다")
    assert r["failed"] is True and r["score"] is None


def test_final_score_all_valid():
    w = {"technical": 0.5, "narrative": 0.3, "public": 0.2}
    assert calculate_final_score({"technical": 8, "narrative": 6, "public": 4}, w) == 6.6


def test_final_score_renormalizes_without_failed_agent():
    w = {"technical": 0.5, "narrative": 0.3, "public": 0.2}
    # public 실패 -> technical/narrative 만으로 (8*0.5 + 6*0.3) / 0.8
    assert calculate_final_score({"technical": 8, "narrative": 6, "public": None}, w) == 7.25


def test_final_score_all_failed_is_none():
    assert calculate_final_score({"technical": None, "narrative": None, "public": None}) is None


async def test_score_candidate_retries_unparseable_response(monkeypatch):
    replies = iter(["형식이 틀린 답변", '{"reason": "근거", "score": 7}'])

    async def fake_call(*args, **kwargs):
        return next(replies)

    monkeypatch.setattr(agent_service, "call_luxia", fake_call)
    result = await agent_service.score_candidate(
        None, {"title": "t"}, "q", "narrative", {"overview_full": "줄거리"}, asyncio.Semaphore(1)
    )
    assert result == {"score": 7.0, "reason": "근거", "failed": False}


async def test_evaluate_candidates_drops_fully_failed_and_ranks(monkeypatch):
    async def fake_details(client, candidates):
        return [{} for _ in candidates]

    async def fake_call(client, system, prompt, **kwargs):
        if "데이터 조회 계획" in system:
            return '{"tools": [], "reason": "-"}'
        if "B작품" in prompt:
            return "망가진 응답"           # B는 모든 평가 실패
        if "A작품" in prompt and "대중성 평가" in prompt:
            return "망가진 응답"           # A는 public만 실패
        return '{"reason": "근거", "score": 8}' if "A작품" in prompt else '{"reason": "근거", "score": 6}'

    monkeypatch.setattr(agent_service, "attach_details", fake_details)
    monkeypatch.setattr(agent_service, "call_luxia", fake_call)

    candidates = [{"id": i, "title": t} for i, t in enumerate(["A작품", "B작품", "C작품"])]
    ranked = await agent_service.evaluate_candidates(None, candidates, "q")

    assert [c["title"] for c in ranked] == ["A작품", "C작품"]
    assert ranked[0]["failed_agents"] == ["public"]
    assert ranked[0]["final_score"] == 8.0
    assert ranked[1]["failed_agents"] == []
