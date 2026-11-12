"""
log_service.py
추천 요청 / 후보 평가 / 사용자 피드백을 Supabase(PostgreSQL)에 기록한다.

테이블 구조: db/schema.sql
설정 (.env 또는 배포 서버의 Secrets)
    SUPABASE_URL          https://xxxx.supabase.co
    SUPABASE_SECRET_KEY   Supabase의 secret 키 (또는 예전 이름 service_role 키). 절대 프론트에 넣지 말 것

동작 원칙
    - 기록은 응답을 막지 않는다 (fire_and_forget으로 백그라운드 실행)
    - 기록이 실패해도 추천은 정상 동작한다 (예외는 로그만 남김)
    - Supabase 설정이 없거나 저장에 실패하면 logs/<테이블>.jsonl 파일에 대신 저장한다 (데이터 유실 방지)
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

LOG_DIR = Path(os.getenv("LOG_FILE_DIR") or Path(__file__).resolve().parent.parent / "logs")
TIMEOUT = 10
AGENT_TYPES = ("technical", "narrative", "public")

_background_tasks: set[asyncio.Task] = set()


# ------------------------------------------------------------------
# 저장 (Supabase REST API, 실패 시 파일)
# ------------------------------------------------------------------
def _supabase_config() -> tuple[str, str] | None:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    return (url, key) if url and key else None


def _headers(key: str) -> dict:
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    # 예전 형식(JWT) 키는 Authorization 헤더도 필요하다. 새 형식(sb_secret_...)은 apikey만 보낸다.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _write_file(table: str, rows: list[dict]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / f"{table}.jsonl", "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({"logged_at": _now(), **row}, ensure_ascii=False) + "\n")


async def save(table: str, rows: list[dict], client: httpx.AsyncClient | None = None) -> str:
    """
    rows를 table에 저장한다. 예외를 밖으로 던지지 않는다.
    return: "supabase" | "file" | "skipped"(빈 rows) | "failed"
    """
    if not rows:
        return "skipped"

    config = _supabase_config()
    if config:
        url, key = config
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=TIMEOUT) as own_client:
                    await _post(own_client, url, key, table, rows)
            else:
                await _post(client, url, key, table, rows)
            return "supabase"
        except Exception as e:
            print(f"[log_service] Supabase 저장 실패 ({table}, {len(rows)}행) -> 파일로 저장: {e!r}")

    try:
        await asyncio.to_thread(_write_file, table, rows)
        return "file"
    except Exception as e:
        print(f"[log_service] 파일 저장도 실패 ({table}): {e!r}")
        return "failed"


async def _post(client: httpx.AsyncClient, url: str, key: str, table: str, rows: list[dict]) -> None:
    response = await client.post(f"{url}/rest/v1/{table}", headers=_headers(key), json=rows, timeout=TIMEOUT)
    if response.status_code >= 400:
        # Supabase가 알려주는 오류 내용(컬럼 이름 오타 등)을 그대로 남긴다
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")


def fire_and_forget(coro) -> None:
    """응답을 기다리게 하지 않고 백그라운드에서 저장한다."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)                 # 실행 중 GC되지 않도록 참조 유지
    task.add_done_callback(_background_tasks.discard)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# 행(row) 만들기
# ------------------------------------------------------------------
def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def recommendation_row(
    request_id: str,
    participant_id: str,
    query: str,
    filters: dict,
    exclude_ids: list[str],
    elapsed_sec: float,
    status: str = "ok",
    error: str | None = None,
    pool_size: int | None = None,
    weight_result: dict | None = None,
    best: dict | None = None,
) -> dict:
    weight_result = weight_result or {}
    return {
        "request_id": request_id,
        "participant_id": participant_id or None,
        "query": query,
        "filters": filters,
        "exclude_ids": exclude_ids or [],
        "status": status,
        "error": error,
        "elapsed_sec": round(elapsed_sec, 2),
        "pool_size": pool_size,
        "weights": weight_result.get("weights"),
        "importance": weight_result.get("importance"),
        "weight_reason": weight_result.get("reason"),
        "weight_status": weight_result.get("status"),
        "best_key": _key(best) if best else None,
        "best_title": best.get("title") if best else None,
    }


def candidate_rows(request_id: str, ranked: list[dict]) -> list[dict]:
    """agent_service.evaluate_candidates 결과(최종 순위순)를 후보 테이블 행으로 바꾼다."""
    rows = []
    for rank, c in enumerate(ranked, 1):
        tool_usage = c.get("tool_usage") or {}
        rows.append({
            "request_id": request_id,
            "rank": rank,
            "item_key": _key(c),
            "title": c.get("title"),
            "label": c.get("label"),
            "similarity": _num(c.get("similarity")),
            "vote_average": _num(c.get("vote_average")),
            "vote_count": int(c.get("vote_count") or 0),
            "popularity": _num(c.get("popularity")),
            **{a: _num((c.get(a) or {}).get("score")) for a in AGENT_TYPES},
            "final_score": _num(c.get("final_score")),
            "failed_agents": c.get("failed_agents") or [],
            "reasons": {a: (c.get(a) or {}).get("reason") for a in AGENT_TYPES},
            "tools": {a: (tool_usage.get(a) or {}).get("tools", []) for a in AGENT_TYPES},
        })
    return rows


def feedback_row(
    request_id: str | None,
    participant_id: str,
    feedback: str,
    item: dict,
    shown_order: int | None = None,
    next_item: dict | None = None,
    need_new_search: bool | None = None,
) -> dict:
    return {
        "request_id": request_id or None,
        "participant_id": participant_id or None,
        "feedback": feedback,
        "item_key": _key(item),
        "title": item.get("title"),
        "shown_order": shown_order,
        "next_item_key": _key(next_item) if next_item else None,
        "next_title": next_item.get("title") if next_item else None,
        "need_new_search": need_new_search,
    }


def _key(item: dict) -> str:
    return f"{item.get('media_type') or 'movie'}:{item.get('id')}"
