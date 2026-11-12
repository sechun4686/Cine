import httpx
import pytest

from services import llm_service


def _client(statuses: list[int]):
    """statuses 순서대로 응답하는 가짜 Luxia 서버. 마지막 값은 계속 반복."""
    calls = {"n": 0}

    def handler(request):
        status = statuses[min(calls["n"], len(statuses) - 1)]
        calls["n"] += 1
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(status, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LUXIA_API_KEY", "test-key")
    monkeypatch.setattr(llm_service, "RETRY_BASE_DELAY", 0)


async def test_retries_on_5xx_then_succeeds():
    client, calls = _client([503, 429, 200])
    async with client:
        assert await llm_service.call_luxia(client, "s", "u") == "ok"
    assert calls["n"] == 3


async def test_gives_up_after_max_retries():
    client, calls = _client([500])
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            await llm_service.call_luxia(client, "s", "u")
    assert calls["n"] == llm_service.MAX_RETRIES + 1


async def test_does_not_retry_auth_error():
    client, calls = _client([401])
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            await llm_service.call_luxia(client, "s", "u")
    assert calls["n"] == 1


def test_extract_json_with_surrounding_text():
    assert llm_service.extract_json('결과: {"score": 7.5} 입니다') == {"score": 7.5}
    assert llm_service.extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert llm_service.extract_json("not json") is None
