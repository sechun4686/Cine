import json
from typing import Callable, Literal

from pydantic import BaseModel, Field
import asyncio
import os
import time
import uuid
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from services.agent_service import evaluate_candidates
from services.candidate_service import select_candidates
from services.editor_service import build_editor_prompt
from services.embedding_service import rank_candidates_by_similarity
from services.llm_service import call_luxia
from services.rerecommend_service import candidate_key, pick_alternative
from services import log_service
from services.tmdb_service import fetch_filtered_pool
from services.weight_service import combine_query, infer_weights

load_dotenv(
    os.path.join(os.path.dirname(__file__), ".env"),
    override=True,
)

app = FastAPI(title="Cine Agent API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "https://cine-agent-eight.vercel.app",
    ],
    allow_origin_regex=r"^https://cine-agent-eight\.vercel\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
)

TMDB_BASE_URL = "https://api.themoviedb.org/3"


class RecommendRequest(BaseModel):
    ott: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)
    runtime: int = 120
    query: str = ""
    exclude_ids: list[str] = Field(default_factory=list)   # "movie:123" 형식. 재추천 시 이미 본/거절한 작품
    participant_id: str = ""                                # 사용자 테스트 참가자 번호 (로그용)
    refinements: list[str] = Field(default_factory=list)   # 결과 화면에서 덧붙인 추가 요청 (오래된 순)
    parent_request_id: str = ""                             # 로그용: 결과 화면에서 이어서 다시 검색했을 때 직전 추천 요청


class RerecommendRequest(BaseModel):
    query: str = ""
    feedback: Literal["watched", "not_interested"]
    rejected: dict                                          # 거절한 추천작 (응답의 best 그대로)
    candidates: list[dict] = Field(default_factory=list)    # 남은 평가 완료 후보 (응답의 others 그대로)
    request_id: str = ""                                    # 로그용: 이 후보들을 평가한 추천 요청
    participant_id: str = ""
    shown_order: int | None = None                          # 거절한 작품이 이번 검색에서 몇 번째로 보여준 작품인지


class FeedbackRequest(BaseModel):
    feedback: Literal["accepted", "watched", "not_interested", "refine"]
    item: dict                                              # 피드백을 받은 작품 (응답의 best 그대로)
    text: str = ""                                          # refine: 사용자가 덧붙인 추가 요청
    request_id: str = ""
    participant_id: str = ""
    shown_order: int | None = None


@app.get("/")
def health_check():
    return {"message": "Cine Agent API is running"}


@app.get("/api/tmdb/search")
async def search_tmdb(
    query: str = Query(min_length=1),
    media: str = "movie",
):
    token = os.getenv("TMDB_API_KEY")

    if not token:
        raise HTTPException(
            status_code=500,
            detail="TMDB_API_KEY 환경변수가 설정되지 않았습니다.",
        )

    if media not in ("movie", "tv"):
        raise HTTPException(
            status_code=400,
            detail="media는 movie 또는 tv여야 합니다.",
        )

    endpoint = f"{TMDB_BASE_URL}/search/{media}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "accept": "application/json",
                },
                params={
                    "query": query,
                    "language": "ko-KR",
                    "region": "KR",
                    "page": 1,
                },
            )
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"TMDB API 오류: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="TMDB 서버에 연결하지 못했습니다.",
        ) from exc

    results = []

    for item in data.get("results", []):
        results.append({
            "id": item.get("id"),
            "title": item.get("title") or item.get("name"),
            "overview": item.get("overview", ""),
            "year": (
                item.get("release_date")
                or item.get("first_air_date")
                or ""
            )[:4],
            "poster_url": (
                f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
                if item.get("poster_path")
                else ""
            ),
            "vote_average": item.get("vote_average", 0),
        })

    return {"results": results}


# ------------------------------------------------------------------
# 추천 파이프라인
#   1) 후보군 수집 (TMDB 하드 필터)      tmdb_service.fetch_filtered_pool
#      + 가중치 추론 (동시에 실행)         weight_service.infer_weights
#   2) 검색어-줄거리 유사도 계산          embedding_service.rank_candidates_by_similarity
#   3) 후보 5개 선별                     candidate_service.select_candidates
#   4) 3개 에이전트 평가 + 최종 점수      agent_service.evaluate_candidates
#   5) Lead Editor 총평                  editor_service.build_editor_prompt
# ------------------------------------------------------------------
POOL_SIZE = 60          # 전체 후보군 크기 (매체가 여러 개면 나눠서 수집)
OTHERS_COUNT = 4        # best 외에 함께 보여줄 후보 수 (재추천 후보로도 쓰므로 평가한 후보를 모두 보낸다)


def _resolve_media(request: RecommendRequest) -> list[tuple[str, list[str]]]:
    """
    프론트의 media 값을 (TMDB 매체, 장르 목록) 쌍으로 바꾼다.
    애니메이션은 영화 목록에서 '애니메이션' 장르로 찾는다.
    """
    media_list = [m for m in (request.media or []) if m in ("movie", "tv", "animation")]
    if not media_list:
        media_list = ["movie", "tv"]

    targets = []
    for media in media_list:
        if media == "animation":
            genres = list(dict.fromkeys([*request.genres, "애니메이션"]))
            targets.append(("movie", genres))
        else:
            targets.append((media, list(request.genres)))
    return targets


async def _fetch_pool(client: httpx.AsyncClient, request: RecommendRequest) -> list[dict]:
    """매체별로 후보를 모아 합친다. 장르 조건으로 비면 장르 조건을 빼고 한 번 더 찾는다."""
    targets = _resolve_media(request)
    per_media = max(POOL_SIZE // len(targets), 20)

    async def fetch_one(media_type: str, genres: list[str]) -> list[dict]:
        pool = await fetch_filtered_pool(
            client, media_type, genres, request.ott, request.runtime, target_count=per_media
        )
        if not pool and genres:
            print(f"[recommend] {media_type} 장르 조건 결과 없음 -> 장르 조건 완화 후 재검색")
            pool = await fetch_filtered_pool(
                client, media_type, [], request.ott, request.runtime, target_count=per_media
            )
        return pool

    results = await asyncio.gather(*(fetch_one(m, g) for m, g in targets))

    # 매체가 겹칠 수 있어 (매체, id) 기준으로 중복 제거
    merged = {}
    for pool in results:
        for item in pool:
            merged.setdefault((item["media_type"], item["id"]), item)
    return list(merged.values())


async def _write_editor_comment(client: httpx.AsyncClient, query: str, best: dict) -> str:
    prompt = build_editor_prompt(query, best, best["technical"], best["narrative"], best["public"])
    try:
        comment = await call_luxia(client, "너는 영화 추천 에디터야.", prompt, max_tokens=600)
        return comment.strip().strip('"')
    except Exception as e:
        print(f"[recommend] Lead Editor 총평 생성 실패: {e}")
        return ""


def _split_names(value) -> list[str]:
    if isinstance(value, list):
        return value
    if not value or value == "정보 없음":
        return []
    return [name.strip() for name in str(value).split(",") if name.strip()]


def _format_candidate(item: dict, weights: dict, editor_comment: str = "") -> dict:
    """프론트(Result.jsx)가 쓰는 응답 형식으로 변환한다. 점수는 0~10 기준."""
    director = item.get("director")
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "overview": item.get("overview", ""),
        "year": item.get("year", ""),
        "poster_url": item.get("poster_url", ""),
        "vote_average": item.get("vote_average", 0),
        "media_type": item.get("media_type"),
        "label": item.get("label", ""),                 # 후보 선별 기준 (평점·인기도 균형 등)
        "similarity": item.get("similarity", 0),
        "director": director if director and director != "정보 없음" else "",
        "cast": _split_names(item.get("cast")),
        "final_score": item.get("final_score", 0),
        "scores": {a: item[a]["score"] for a in ("technical", "narrative", "public")},
        "reasons": {a: item[a]["reason"] for a in ("technical", "narrative", "public")},
        "reason": editor_comment,
        "editor_comment": editor_comment,
        "weights": weights,
        "tool_usage": item.get("tool_usage", {}),       # 에이전트별 선택 도구와 근거 데이터
        "failed_agents": item.get("failed_agents", []),  # 평가에 실패해 최종 점수에서 제외된 에이전트
    }


def _log_candidates(query: str, pool_size: int, weight_result: dict, candidates: list[dict]) -> None:
    """선별된 후보 5개를 터미널에 출력한다."""
    w = weight_result["weights"]
    print("\n" + "=" * 70)
    print(f"[recommend] 검색어: {query or '(없음)'} | 후보군 {pool_size}개")
    print(f"[recommend] 가중치 T {w['technical']:.3f} / N {w['narrative']:.3f} / P {w['public']:.3f}"
          f" ({weight_result['status']}) 중요도={weight_result['importance']}")
    print("[recommend] 선별된 후보")
    for i, c in enumerate(candidates, 1):
        print(f"  C{i} [{c.get('label', '-')}] {c.get('title')} ({c.get('year') or '-'})"
              f" | 평점 {float(c.get('vote_average') or 0):.1f}"
              f" | 투표 {int(c.get('vote_count') or 0):,}"
              f" | 인기도 {float(c.get('popularity') or 0):.1f}"
              f" | 유사도 {float(c.get('similarity') or 0):.3f}")
    print("=" * 70)


def _fmt_score(score) -> str:
    """평가 실패(None)도 로그에 찍을 수 있게 문자열로 바꾼다."""
    return "실패" if score is None else f"{score:.1f}"


def _log_ranking(ranked: list[dict]) -> None:
    """에이전트 평가 후 최종 순위를 터미널에 출력한다."""
    print("[recommend] 최종 순위")
    for i, c in enumerate(ranked, 1):
        print(f"  {i}위 {c.get('title')} [{c.get('label', '-')}]"
              f" | 최종 {c['final_score']:.2f}"
              f" (T {_fmt_score(c['technical']['score'])} / N {_fmt_score(c['narrative']['score'])}"
              f" / P {_fmt_score(c['public']['score'])})")
    print("=" * 70 + "\n")


@app.post("/api/recommend")
async def recommend(request: RecommendRequest):
    """추천 파이프라인 실행 + 결과 로그 저장 (성공/실패 모두 기록)"""
    return await _recommend_logged(request)


SSE_PING_SEC = 15   # 이 시간 동안 이벤트가 없으면 연결 유지용 주석을 보낸다


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/recommend/stream")
async def recommend_stream(request: RecommendRequest):
    """
    /api/recommend와 같은 파이프라인을 실행하면서 진행 상황을 SSE로 보낸다 (로딩 화면 연동용).

    이벤트 (data: JSON)
        {"type": "stage", "stage": "pool|weights|similarity|candidates|details|editor", "status": "running|done", ...}
        {"type": "agent", "agent": "technical|narrative|public", "index": 0~4, "title",
         "phase": "tools_selected|data_ready|scored", ...}
        {"type": "result", "data": /api/recommend 응답과 같은 형식}
        {"type": "error", "status": HTTP 상태 코드, "detail": 메시지}

    오류도 이벤트로 보내므로 HTTP 상태는 항상 200이다.
    (500 응답은 CORS 헤더가 빠져 브라우저에서 'Failed to fetch'로만 보이는 문제도 피한다)
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def worker() -> None:
        try:
            data = await _recommend_logged(request, emit=queue.put_nowait)
            queue.put_nowait({"type": "result", "data": data})
        except HTTPException as exc:
            queue.put_nowait({"type": "error", "status": exc.status_code, "detail": exc.detail})
        except Exception as exc:
            print(f"[recommend/stream] 처리 실패: {exc!r}")
            queue.put_nowait({"type": "error", "status": 500,
                              "detail": "추천 처리 중 서버 오류가 발생했습니다."})
        finally:
            queue.put_nowait(None)

    async def events():
        # 클라이언트가 연결을 끊어도 작업은 끝까지 진행해 로그를 남긴다 (/api/recommend와 동일)
        task = asyncio.create_task(worker())
        app.state.stream_tasks = getattr(app.state, "stream_tasks", set())
        app.state.stream_tasks.add(task)
        task.add_done_callback(app.state.stream_tasks.discard)

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=SSE_PING_SEC)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _noop(_: dict) -> None:
    pass


async def _recommend_logged(request: RecommendRequest, emit: Callable[[dict], None] = _noop) -> dict:
    """추천 파이프라인 실행 + 결과 로그 저장 (성공/실패 모두 기록)"""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    info: dict = {}

    async def log_request(status: str, error: str | None = None, best: dict | None = None) -> None:
        await log_service.save("recommendations", [log_service.recommendation_row(
            request_id=request_id,
            participant_id=request.participant_id,
            query=combine_query(request.query, request.refinements),
            filters={"ott": request.ott, "genres": request.genres, "media": request.media,
                     "runtime": request.runtime},
            exclude_ids=request.exclude_ids,
            parent_request_id=request.parent_request_id,
            elapsed_sec=time.perf_counter() - started,
            status=status,
            error=error,
            pool_size=info.get("pool_size"),
            weight_result=info.get("weight_result"),
            best=best,
        )])

    try:
        response = await _run_recommend(request, info, emit)
    except HTTPException as exc:
        await log_request("error", error=f"{exc.status_code}: {exc.detail}")
        raise
    except Exception as exc:
        await log_request("error", error=repr(exc)[:500])
        raise

    ranked = info["ranked"]
    # 응답 전에 저장을 끝낸다. Cloud Run은 응답 후 CPU를 거의 주지 않아 백그라운드 저장이 유실될 수 있다.
    # save()는 예외를 던지지 않고, 두 테이블을 동시에 저장해 지연은 0.1~0.3초 수준이다.
    await asyncio.gather(
        log_request("ok", best=ranked[0]),
        log_service.save("candidates", log_service.candidate_rows(request_id, ranked)),
    )

    return {"request_id": request_id, **response}


async def _run_recommend(request: RecommendRequest, info: dict, emit: Callable[[dict], None] = _noop) -> dict:
    """
    추천 파이프라인 본체. info에 로그용 중간 결과(pool_size, weight_result, ranked)를 채운다.
    emit: 진행 이벤트 콜백 (스트리밍 엔드포인트에서만 사용)
    """
    if not os.getenv("TMDB_API_KEY"):
        raise HTTPException(status_code=500, detail="TMDB_API_KEY 환경변수가 설정되지 않았습니다.")
    if not os.getenv("LUXIA_API_KEY"):
        raise HTTPException(status_code=500, detail="LUXIA_API_KEY 환경변수가 설정되지 않았습니다.")

    # 유사도·에이전트·총평에는 합친 문장을, 가중치 추론에는 추가 요청을 구분해서 넘긴다
    query = combine_query(request.query, request.refinements)

    async with httpx.AsyncClient(timeout=60) as client:
        # 1) 후보군 수집 + 가중치 추론을 동시에 실행
        emit({"type": "stage", "stage": "pool", "status": "running"})
        try:
            pool, weight_result = await asyncio.gather(
                _fetch_pool(client, request),
                infer_weights(client, request.query, request.refinements),
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"TMDB API 오류: {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="TMDB 서버에 연결하지 못했습니다.",
            ) from exc

        if not pool:
            raise HTTPException(
                status_code=404,
                detail="선택한 조건에 맞는 작품을 찾지 못했습니다. 조건을 바꿔서 다시 시도해 주세요.",
            )

        # 재추천: 이미 추천했거나 거절한 작품 제외
        if request.exclude_ids:
            excluded = set(request.exclude_ids)
            pool = [item for item in pool if candidate_key(item) not in excluded]
            if not pool:
                raise HTTPException(
                    status_code=404,
                    detail="이 조건에서 더 추천할 작품이 없어요. 조건을 바꿔서 다시 시도해 주세요.",
                )

        weights = weight_result["weights"]
        info["pool_size"] = len(pool)
        info["weight_result"] = weight_result
        emit({"type": "stage", "stage": "pool", "status": "done", "count": len(pool)})
        emit({"type": "stage", "stage": "weights", "status": "done", "weights": weights})

        # 2) 유사도 계산 (임베딩 연산이 무거워 별도 스레드에서 실행)
        pool = await asyncio.to_thread(rank_candidates_by_similarity, query, pool)
        emit({"type": "stage", "stage": "similarity", "status": "done"})

        # 3) 후보 5개 선별
        candidates = select_candidates(pool, query)
        _log_candidates(query, len(pool), weight_result, candidates)
        emit({"type": "stage", "stage": "candidates", "status": "done", "count": len(candidates),
              "titles": [c.get("title") for c in candidates]})

        # 4) 에이전트 평가 + 가중치 적용 최종 점수
        ranked = await evaluate_candidates(client, candidates, query, weights, on_progress=emit)

        if not ranked:
            raise HTTPException(status_code=502, detail="에이전트 평가 결과가 없습니다.")

        _log_ranking(ranked)
        info["ranked"] = ranked

        # 5) Lead Editor 총평 (1위 작품만)
        best = ranked[0]
        emit({"type": "stage", "stage": "editor", "status": "running", "title": best.get("title")})
        editor_comment = await _write_editor_comment(client, query, best)
        emit({"type": "stage", "stage": "editor", "status": "done"})

    return {
        "best": _format_candidate(best, weights, editor_comment),
        "others": [_format_candidate(item, weights) for item in ranked[1:1 + OTHERS_COUNT]],
        "weights": weights,
        "weight_analysis": {
            "importance": weight_result["importance"],   # LLM이 추론한 T/N/P 중요도 (0~5)
            "reason": weight_result["reason"],
            "status": weight_result["status"],
        },
    }

def _as_evaluated(item: dict) -> dict:
    """_format_candidate 형식을 Lead Editor 프롬프트가 쓰는 형식으로 되돌린다."""
    scores = item.get("scores") or {}
    reasons = item.get("reasons") or {}
    return {
        **item,
        **{a: {"score": scores.get(a), "reason": reasons.get(a, "")} for a in ("technical", "narrative", "public")},
    }


@app.post("/api/rerecommend")
async def rerecommend(request: RerecommendRequest):
    """
    피드백 기반 재추천. 이미 평가한 후보 중에서 고르므로 에이전트 평가를 다시 하지 않고,
    새 추천작의 Lead Editor 총평만 생성한다 (LLM 1회).
    고를 후보가 없으면 need_new_search=True -> 프론트가 exclude_ids를 붙여 /api/recommend를 다시 호출한다.
    """
    pick, note = pick_alternative(request.rejected, request.candidates, request.feedback)

    await log_service.save("feedback", [log_service.feedback_row(
        request_id=request.request_id,
        participant_id=request.participant_id,
        feedback=request.feedback,
        item=request.rejected,
        shown_order=request.shown_order,
        next_item=pick,
        need_new_search=pick is None,
    )])

    if pick is None:
        return {"need_new_search": True, "note": note}

    if not os.getenv("LUXIA_API_KEY"):
        raise HTTPException(status_code=500, detail="LUXIA_API_KEY 환경변수가 설정되지 않았습니다.")

    async with httpx.AsyncClient(timeout=60) as client:
        editor_comment = await _write_editor_comment(client, request.query.strip(), _as_evaluated(pick))

    pick_key = candidate_key(pick)
    rejected_key = candidate_key(request.rejected)
    others = [
        c for c in request.candidates
        if candidate_key(c) not in (pick_key, rejected_key)
    ]

    return {
        "need_new_search": False,
        "note": note,
        "best": {**pick, "reason": editor_comment, "editor_comment": editor_comment},
        "others": others,
    }


@app.post("/api/feedback")
async def feedback(request: FeedbackRequest):
    """
    재추천을 서버에서 하지 않는 피드백을 기록한다.
    accepted: '이거 볼래요' / refine: 추가 요청을 덧붙여 다시 추천받기 (프론트가 /api/recommend를 새로 호출)
    """
    await log_service.save("feedback", [log_service.feedback_row(
        request_id=request.request_id,
        participant_id=request.participant_id,
        feedback=request.feedback,
        item=request.item,
        shown_order=request.shown_order,
        text=request.text.strip() if request.feedback == "refine" else None,
    )])
    return {"ok": True}
