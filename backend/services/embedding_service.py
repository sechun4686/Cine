
from functools import lru_cache

from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=1)
def get_embedding_model():
    """KoSRoBERTa 모델을 한 번만 로드한다."""
    return SentenceTransformer("jhgan/ko-sroberta-multitask")


def rank_candidates_by_similarity(
    query: str,
    candidates: list[dict],
) -> list[dict]:
    """검색어와 영화 줄거리의 코사인 유사도를 계산한다."""

    if not query.strip() or not candidates:
        return candidates

    model = get_embedding_model()

    query_embedding = model.encode(
        [query],
        normalize_embeddings=True,
    )[0]

    # 줄거리가 비어 있는 작품은 빈 문자열로 처리
    overviews = [
        (item.get("overview") or "").strip()
        for item in candidates
    ]

    overview_embeddings = model.encode(
        overviews,
        normalize_embeddings=True,
        batch_size=16,
    )

    similarities = overview_embeddings @ query_embedding

    ranked_candidates = []

    for item, similarity in zip(candidates, similarities):
        ranked_item = {
            **item,
            "similarity": round(float(similarity), 4),
        }
        ranked_candidates.append(ranked_item)

    return sorted(
        ranked_candidates,
        key=lambda item: item["similarity"],
        reverse=True,
    )

