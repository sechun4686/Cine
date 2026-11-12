import sys
import types

import numpy as np

# sentence-transformers(무거움) 없이 테스트하기 위한 가짜 모듈
sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=object))

from services import embedding_service  # noqa: E402


class FakeModel:
    def encode(self, texts, **kwargs):
        assert all(t for t in texts), "빈 줄거리는 인코딩하지 않아야 함"
        return np.array([[1.0, 0.0] if "힐링" in t else [0.0, 1.0] for t in texts])


def test_empty_overview_gets_zero_similarity(monkeypatch):
    monkeypatch.setattr(embedding_service, "get_embedding_model", lambda: FakeModel())
    items = [{"id": 1, "overview": ""}, {"id": 2, "overview": "힐링 이야기"}, {"id": 3, "overview": "전쟁"}]
    ranked = embedding_service.rank_candidates_by_similarity("힐링", items)
    assert [x["id"] for x in ranked][0] == 2
    assert {x["id"]: x["similarity"] for x in ranked} == {1: 0.0, 2: 1.0, 3: 0.0}
