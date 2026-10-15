"""
llm_service.py
Luxia LLM 호출과 응답(JSON) 파싱 공통 함수
"""

import json
import os
import re

import httpx

LUXIA_URL = "https://bridge.luxiacloud.com/luxia/v1/chat"
LUXIA_MODEL = "luxia3-llm-32b-0731"


async def call_luxia(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> str:
    api_key = os.getenv("LUXIA_API_KEY")  # .env의 키 이름이 다르면 여기만 수정
    if not api_key:
        raise ValueError("LUXIA_API_KEY 환경변수가 설정되지 않았습니다.")

    response = await client.post(
        LUXIA_URL,
        headers={
            "apikey": api_key,
            "Content-Type": "application/json",
        },
        json={
            "model": LUXIA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def extract_json(raw: str) -> dict | None:
    """LLM 응답에서 JSON dict를 꺼낸다. 실패하면 None."""
    clean = (raw or "").strip().replace("```json", "").replace("```", "").strip()

    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        # 앞뒤에 다른 말이 섞인 경우 { ... } 부분만 추출
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            return None
        try:
            result = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    return result if isinstance(result, dict) else None