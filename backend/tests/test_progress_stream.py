import json

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from services import agent_service


# ------------------------------------------------------------------
# evaluate_candidates 진행 이벤트
# ------------------------------------------------------------------
async def test_evaluate_candidates_emits_agent_progress(monkeypatch):
    async def fake_details(client, candidates):
        return [{} for _ in candidates]

    async def fake_call(client, system, prompt, **kwargs):
        if "데이터 조회 계획" in system:
            return '{"tools": [], "reason": "-"}'
        return '{"reason": "근거", "score": 7}'

    monkeypatch.setattr(agent_service, "attach_details", fake_details)
    monkeypatch.setattr(agent_service, "call_luxia", fake_call)

    events = []
    candidates = [{"id": i, "title": f"작품{i}"} for i in range(2)]
    await agent_service.evaluate_candidates(None, candidates, "q", on_progress=events.append)

    assert events[0] == {"type": "stage", "stage": "details", "status": "done"}
    agent_events = [e for e in events if e["type"] == "agent"]
    # 후보 2개 x 에이전트 3개 x 단계 3개
    assert len(agent_events) == 18
    for agent in agent_service.AGENT_TYPES:
        for index in range(2):
            phases = [e["phase"] for e in agent_events if e["agent"] == agent and e["index"] == index]
            assert phases == ["tools_selected", "data_ready", "scored"]
    selected = next(e for e in agent_events if e["phase"] == "tools_selected" and e["agent"] == "technical")
    assert selected["tools"] == ["감독", "감독 필모그래피"]   # 선택 실패 -> 기본(핵심) 도구 라벨


async def test_progress_callback_error_does_not_break_evaluation(monkeypatch):
    async def fake_details(client, candidates):
        return [{} for _ in candidates]

    async def fake_call(client, system, prompt, **kwargs):
        if "데이터 조회 계획" in system:
            return '{"tools": [], "reason": "-"}'
        return '{"reason": "근거", "score": 7}'

    def broken(event):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_service, "attach_details", fake_details)
    monkeypatch.setattr(agent_service, "call_luxia", fake_call)

    ranked = await agent_service.evaluate_candidates(None, [{"id": 1, "title": "A"}], "q", on_progress=broken)
    assert ranked[0]["final_score"] == 7.0


# ------------------------------------------------------------------
# /api/recommend/stream
# ------------------------------------------------------------------
def _read_events(response) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_sends_progress_then_result(monkeypatch):
    async def fake_logged(request, emit=main._noop):
        emit({"type": "stage", "stage": "pool", "status": "running"})
        emit({"type": "agent", "agent": "technical", "index": 0, "phase": "scored"})
        return {"request_id": "r1", "best": {"title": "A"}}

    monkeypatch.setattr(main, "_recommend_logged", fake_logged)

    with TestClient(main.app) as client:
        response = client.post("/api/recommend/stream", json={"query": "q"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _read_events(response)
    assert [e["type"] for e in events] == ["stage", "agent", "result"]
    assert events[-1]["data"]["best"]["title"] == "A"


def test_stream_reports_errors_as_events(monkeypatch):
    async def fake_logged(request, emit=main._noop):
        raise HTTPException(status_code=404, detail="조건에 맞는 작품 없음")

    monkeypatch.setattr(main, "_recommend_logged", fake_logged)

    with TestClient(main.app) as client:
        response = client.post("/api/recommend/stream", json={"query": "q"})

    assert response.status_code == 200
    assert _read_events(response) == [{"type": "error", "status": 404, "detail": "조건에 맞는 작품 없음"}]


def test_stream_hides_unexpected_error_details(monkeypatch):
    async def fake_logged(request, emit=main._noop):
        raise RuntimeError("secret internal path")

    monkeypatch.setattr(main, "_recommend_logged", fake_logged)

    with TestClient(main.app) as client:
        response = client.post("/api/recommend/stream", json={"query": "q"})

    events = _read_events(response)
    assert events[0]["type"] == "error" and events[0]["status"] == 500
    assert "secret" not in events[0]["detail"]
