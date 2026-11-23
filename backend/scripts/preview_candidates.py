"""
preview_candidates.py
검색어마다 'Query Structuring(포함/제외 장르) -> 후보군 -> 후보 5편'만 출력해 후보 선정이 요청에 맞는지 확인한다.
에이전트 평가는 하지 않는다 (LLM 호출: 쿼리마다 검색어 구조화 1회).

실행 (backend 폴더에서)
    python scripts/preview_candidates.py                                   # 실험 쿼리 5개
    python scripts/preview_candidates.py --query "비 오는 날 보기 좋은 영화"
    python scripts/preview_candidates.py --query "우주 배경 영화" --media movie --ott 넷플릭스 --genres SF
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_reproducibility import DEFAULT_QUERIES  # noqa: E402  (실험과 같은 쿼리)


# 화면 장르 목록에는 없지만 TMDB가 붙이는 장르 (표시용)
EXTRA_GENRE_NAMES = {36: "역사", 10402: "음악", 10770: "TV 영화", 10762: "키즈", 10766: "연속극"}


def genre_names(ids: list[int], media_type: str) -> str:
    from services.tmdb_service import MOVIE_GENRES, TV_GENRES
    table = TV_GENRES if media_type == "tv" else MOVIE_GENRES
    names = dict(EXTRA_GENRE_NAMES)
    for name, gid in table.items():
        if gid is not None:
            names.setdefault(gid, name)
    return ", ".join(names.get(i, str(i)) for i in ids) or "-"


def format_candidates(query: str, info: dict, pool_size: int, candidates: list[dict],
                      user_genres: list[str] | None = None) -> str:
    st = info.get("query_structure") or {}
    include = st.get("include_genres") or []
    if user_genres:
        include_line = f"{user_genres} (사용자 선택. 추론한 {include or '없음'}은 쓰지 않음)"
    else:
        include_line = f"{include or '없음 (장르 조건 없이 수집)'}"

    lines = [
        f"\n[검색어] {query}",
        f"  포함 장르: {include_line}",
        f"  제외 장르: {st.get('exclude_genres') or '없음'}",
        f"  판단 근거: [{st.get('status', '-')}] {st.get('reason', '-')}",
        f"  후보군: {pool_size}편",
        "  후보 5편:",
    ]
    for c in candidates:
        media = c.get("media_type", "movie")
        lines.append(
            f"    - [{c.get('label')}] {c.get('title')} ({c.get('year') or '-'}, {media})"
            f"  유사도 {c.get('similarity', 0):.3f} · 평점 {c.get('vote_average', 0):.1f}"
            f"({c.get('vote_count', 0)}표) · 장르 {genre_names(c.get('genre_ids') or [], media)}"
        )
    return "\n".join(lines)


async def run(specs: list[dict]) -> None:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env", override=True)

    import httpx
    import main
    from services.candidate_service import select_candidates
    from services.embedding_service import rank_candidates_by_similarity

    for key in ("TMDB_API_KEY", "LUXIA_API_KEY"):
        if not os.getenv(key):
            sys.exit(f"{key}가 backend/.env에 없습니다.")

    async with httpx.AsyncClient(timeout=60) as client:
        for spec in specs:
            request = main.RecommendRequest(**spec)
            info: dict = {}
            pool = await main._fetch_pool(client, request, info)
            pool = await asyncio.to_thread(rank_candidates_by_similarity, request.query, pool)
            candidates = select_candidates(pool, request.query)
            print(format_candidates(request.query, info, len(pool), candidates, request.genres))


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="검색어별 후보 5편 미리보기 (에이전트 평가 없음)")
    parser.add_argument("--query", default="", help="확인할 검색어 (없으면 실험 쿼리 5개)")
    parser.add_argument("--media", nargs="*", default=["movie"], help="movie / tv / animation (기본 movie)")
    parser.add_argument("--ott", nargs="*", default=[], help="예: 넷플릭스 왓챠")
    parser.add_argument("--genres", nargs="*", default=[], help="화면에서 고른 장르 (예: SF 액션)")
    args = parser.parse_args()

    if args.query:
        specs = [{"query": args.query, "media": args.media, "ott": args.ott, "genres": args.genres}]
    else:
        specs = [{"query": q["query"], "media": q["media"], "ott": q["ott"]} for q in DEFAULT_QUERIES]
    asyncio.run(run(specs))


if __name__ == "__main__":
    main_cli()
