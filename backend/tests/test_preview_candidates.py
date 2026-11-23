import asyncio
import sys
import types
from pathlib import Path

sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import preview_candidates as pc  # noqa: E402


def test_genre_names_by_media():
    assert pc.genre_names([18, 10751], "movie") == "드라마, 가족"
    assert pc.genre_names([10759, 10765], "tv") == "액션, 판타지"
    assert pc.genre_names([], "movie") == "-"
    assert pc.genre_names([18, 36], "movie") == "드라마, 역사"


def test_run_prints_inferred_genres_and_candidates(monkeypatch, capsys):
    import main
    from services import candidate_service, embedding_service

    monkeypatch.setenv("TMDB_API_KEY", "t")
    monkeypatch.setenv("LUXIA_API_KEY", "l")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)

    async def fake_pool(client, request, info=None):
        info["query_structure"] = {"include_genres": ["드라마"], "exclude_genres": ["공포"],
                                   "reason": "따뜻한 분위기", "status": "ok"}
        return [{"id": 1, "title": "작품", "media_type": "movie", "genre_ids": [18], "year": "2020",
                 "vote_average": 7.5, "vote_count": 300, "similarity": 0.4}]

    monkeypatch.setattr(main, "_fetch_pool", fake_pool)
    monkeypatch.setattr(embedding_service, "rank_candidates_by_similarity", lambda q, p: p)
    monkeypatch.setattr(candidate_service, "select_candidates", lambda pool, q: [{**pool[0], "label": "검색어 유사도"}])

    asyncio.run(pc.run([{"query": "힐링 영화", "media": ["movie"], "ott": []}]))
    out = capsys.readouterr().out
    assert "포함 장르: ['드라마']" in out and "제외 장르: ['공포']" in out
    assert "판단 근거: [ok] 따뜻한 분위기" in out
    assert "[검색어 유사도] 작품 (2020, movie)  유사도 0.400 · 평점 7.5(300표) · 장르 드라마" in out
