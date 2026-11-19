// 로딩 화면 진행 상태 (백엔드 /api/recommend/stream 이벤트 -> 화면 상태)

export const AGENT_IDS = ["technical", "narrative", "public"];

const DEFAULT_TOTAL = 5;   // 후보 수를 받기 전 표시용
const PREP_SHARE = 10;     // 후보 준비 단계가 진행 바에서 차지하는 비율(%)

const emptyAgent = () => ({
  selected: {},   // 후보 index -> true
  data: {},
  scored: {},
  failed: 0,
  tools: [],      // 가장 최근에 고른 도구 라벨
});

export function initialProgress() {
  return {
    stages: {},   // pool / weights / similarity / candidates / details / editor -> "running" | "done"
    total: null,
    agents: Object.fromEntries(AGENT_IDS.map((id) => [id, emptyAgent()])),
    fallback: false,
    finished: false,   // 결과 수신 완료
    error: "",         // 요청 실패 메시지
  };
}

export function progressReducer(state, event) {
  switch (event.type) {
    case "reset":
      return initialProgress();

    case "fallback":
      return { ...state, fallback: true };

    case "finished":
      return { ...state, finished: true };

    case "error":
      return { ...state, error: event.message };

    case "stage": {
      const next = {
        ...state,
        stages: { ...state.stages, [event.stage]: event.status },
      };
      if (event.stage === "candidates" && typeof event.count === "number") {
        next.total = event.count;
      }
      return next;
    }

    case "agent": {
      const prev = state.agents[event.agent];
      if (!prev) return state;

      const agent = { ...prev };
      if (event.phase === "tools_selected") {
        agent.selected = { ...prev.selected, [event.index]: true };
        if (Array.isArray(event.tools) && event.tools.length) {
          agent.tools = event.tools;
        }
      } else if (event.phase === "data_ready") {
        agent.data = { ...prev.data, [event.index]: true };
      } else if (event.phase === "scored") {
        agent.scored = { ...prev.scored, [event.index]: true };
        if (event.failed) agent.failed = prev.failed + 1;
      }
      return { ...state, agents: { ...state.agents, [event.agent]: agent } };
    }

    default:
      return state;
  }
}

const count = (obj) => Object.keys(obj).length;

// 카드 하나에 보여줄 값: 진행률, 완료 여부, 단계 목록
export function agentView(state, agentId) {
  const agent = state.agents[agentId];
  const total = state.total ?? DEFAULT_TOTAL;

  // 결과를 받았으면 모두 완료로 표시
  if (state.finished) {
    return {
      done: true,
      progress: 100,
      steps: [
        { done: true, text: `후보 ${total}편 준비 완료` },
        { done: true, text: "평가 데이터 조회 완료" },
        { done: true, text: doneScoreText(total, agent.failed) },
      ],
    };
  }

  // 구버전 백엔드: 세부 진행을 알 수 없음
  if (state.fallback) {
    return {
      done: false,
      progress: null,
      steps: [{ done: false, text: "작품을 평가하고 있어요..." }],
    };
  }

  const prepDone = state.stages.details === "done" || count(agent.selected) > 0;
  const selected = count(agent.selected);
  const data = count(agent.data);
  const scored = count(agent.scored);

  const phaseRatio = total > 0 ? (selected + data + scored) / (3 * total) : 0;
  const progress = prepDone
    ? PREP_SHARE + (100 - PREP_SHARE) * Math.min(phaseRatio, 1)
    : prepProgress(state.stages);

  const done = state.total !== null && scored >= total;

  const steps = [
    {
      done: prepDone,
      text: prepDone ? `후보 ${total}편 준비 완료` : prepText(state.stages),
    },
  ];

  if (prepDone) {
    const toolHint = agent.tools.length ? ` · ${agent.tools.join(", ")}` : "";
    steps.push(
      data >= total
        ? { done: true, text: "평가 데이터 조회 완료" }
        : { done: false, text: `평가 데이터 조회 중 (${data}/${total})${toolHint}` }
    );
  }

  if (prepDone && data > 0) {
    steps.push(
      done
        ? { done: true, text: doneScoreText(total, agent.failed) }
        : { done: false, text: `작품 평가 중 (${scored}/${total})` }
    );
  }

  return { done, progress, steps };
}

function doneScoreText(total, failed) {
  return failed > 0
    ? `${total}편 평가 완료 · ${failed}편 평가 실패`
    : `${total}편 평가 완료`;
}

function prepText(stages) {
  if (stages.candidates === "done") return "작품 상세정보 불러오는 중...";
  if (stages.similarity === "done") return "평가할 후보 고르는 중...";
  if (stages.pool === "done") return "검색어와 줄거리 비교 중...";
  return "조건에 맞는 작품 모으는 중...";
}

function prepProgress(stages) {
  // 준비 단계 안에서도 조금씩 차오르게 한다 (최대 PREP_SHARE)
  const done = ["pool", "similarity", "candidates"].filter((s) => stages[s] === "done").length;
  return (PREP_SHARE * done) / 4;
}

// 상단 부제목 / 하단 문구
export function headline(state) {
  if (state.finished) return "분석이 끝났어요. 결과를 보여드릴게요";
  if (state.stages.editor === "running" || state.stages.editor === "done") {
    return "Lead Editor가 최종 추천 총평을 쓰고 있습니다";
  }
  return "3개의 AI 에이전트가 각자의 관점에서 작품을 평가하고 있습니다";
}
