
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

    # 줄거리가 빈 작품은 빈 문자열 임베딩이 엉뚱한 유사도를 받으므로 계산하지 않고 0으로 둔다
    overviews = [
        (item.get("overview") or "").strip()
        for item in candidates
    ]
    has_text = [i for i, text in enumerate(overviews) if text]

    similarities = [0.0] * len(candidates)

    if has_text:
        overview_embeddings = model.encode(
            [overviews[i] for i in has_text],
            normalize_embeddings=True,
            batch_size=16,
        )
        for i, sim in zip(has_text, overview_embeddings @ query_embedding):
            similarities[i] = float(sim)

    ranked_candidates = [
        {**item, "similarity": round(similarity, 4)}
        for item, similarity in zip(candidates, similarities)
    ]

    return sorted(
        ranked_candidates,
        key=lambda item: item["similarity"],
        reverse=True,
    )
