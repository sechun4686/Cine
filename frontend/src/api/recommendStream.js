import { API_URL } from "../constants";

// 추천 요청 + 진행 이벤트 수신
//   1) /api/recommend/stream (SSE) 으로 요청해 단계별 진행 이벤트를 onEvent로 넘긴다
//   2) 백엔드가 아직 스트리밍을 지원하지 않으면(404/405) 기존 /api/recommend 로 요청한다
//      이때는 진행 이벤트 없이 {type: "fallback"} 하나만 보낸다

function buildPayload(filters, excludeIds, participantId) {
  if (!filters) {
    throw new Error(
      "추천 입력값을 찾을 수 없습니다. 입력 페이지부터 다시 진행해 주세요."
    );
  }

  return {
    ...filters,
    ott: Array.isArray(filters.ott) ? filters.ott : [],
    genres: Array.isArray(filters.genres) ? filters.genres : [],
    media: Array.isArray(filters.media)
      ? filters.media
      : filters.media
        ? [filters.media]
        : [],
    runtime: Number(filters.runtime ?? 120),
    query: filters.query ?? "",
    refinements: Array.isArray(filters.refinements) ? filters.refinements : [],
    exclude_ids: Array.isArray(excludeIds) ? excludeIds : [],
    participant_id: participantId || "",
  };
}

function checkResult(data) {
  if (!data?.best) {
    throw new Error(
      "추천 결과 형식이 예상과 다릅니다. 백엔드 응답을 확인해 주세요."
    );
  }
  return data;
}

async function postJson(path, payload) {
  try {
    return await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // 브라우저의 'Failed to fetch'는 원인을 알려주지 않으므로 사용자용 문구로 바꾼다
    throw new Error(
      "서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요."
    );
  }
}

async function requestPlain(payload) {
  const response = await postJson("/api/recommend", payload);
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `추천 API 오류 (${response.status})`
    );
  }
  return checkResult(data);
}

// SSE 본문을 읽어 data: 줄마다 JSON 이벤트를 꺼낸다
async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      const data = chunk
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");

      if (data) {
        try {
          yield JSON.parse(data);
        } catch {
          // 깨진 이벤트 하나는 건너뛴다
        }
      }
    }

    if (done) return;
  }
}

export async function requestRecommendationStream(
  filters,
  excludeIds = [],
  participantId = "",
  onEvent = () => {}
) {
  const payload = buildPayload(filters, excludeIds, participantId);
  const response = await postJson("/api/recommend/stream", payload);

  // 스트리밍 엔드포인트가 없는 백엔드 -> 기존 방식으로 요청
  if (response.status === 404 || response.status === 405) {
    onEvent({ type: "fallback" });
    return requestPlain(payload);
  }

  if (!response.ok || !response.body) {
    throw new Error(`추천 API 오류 (${response.status})`);
  }

  for await (const event of readEvents(response)) {
    if (event.type === "result") {
      return checkResult(event.data);
    }
    if (event.type === "error") {
      throw new Error(
        typeof event.detail === "string"
          ? event.detail
          : `추천 API 오류 (${event.status})`
      );
    }
    onEvent(event);
  }

  throw new Error("서버 연결이 중간에 끊겼어요. 다시 시도해 주세요.");
}
