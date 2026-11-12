import httpx

from services.agent_tools import ToolContext, tool_reviews


def _client(en_reviews):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": en_reviews})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_uses_korean_reviews_without_extra_call(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "t")
    client, seen = _client([])
    details = {"reviews": {"results": [{"content": "정말 따뜻한 영화", "author_details": {"rating": 9}}]}}
    async with client:
        out = await tool_reviews(ToolContext(client, {"id": 1, "media_type": "movie"}, details, {}))
    assert out == "[9/10] 정말 따뜻한 영화"
    assert seen == []


async def test_falls_back_to_english_reviews(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "t")
    client, seen = _client([{"content": "A  heart-warming\nfilm", "author_details": {"rating": None}}])
    async with client:
        out = await tool_reviews(ToolContext(client, {"id": 42, "media_type": "tv"}, {"reviews": {"results": []}}, {}))
    assert out == "(영어 리뷰) A heart-warming film"
    assert seen[0].url.path == "/3/tv/42/reviews"
    assert seen[0].url.params["language"] == "en-US"
