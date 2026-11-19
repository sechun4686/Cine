import { useEffect, useReducer, useRef } from "react";
import { useNavigate } from "react-router-dom";
import "./Loading.css";
import { TechnicalIcon, NarrativeIcon, PublicIcon } from "../components/AgentIcons";
import { requestRecommendationStream } from "../api/recommendStream";
import {
  agentView,
  headline,
  initialProgress,
  progressReducer,
} from "./loadingProgress";

const AGENTS = [
  {
    id: "technical",
    Icon: TechnicalIcon,
    name: "Technical 에이전트",
    desc: "기술적 완성도 평가",
  },
  {
    id: "narrative",
    Icon: NarrativeIcon,
    name: "Narrative 에이전트",
    desc: "서사·감성 평가",
  },
  {
    id: "public",
    Icon: PublicIcon,
    name: "Public 에이전트",
    desc: "대중성 평가",
  },
];

// 요청 하나와 그 진행 이벤트를 묶어 둔다.
// 개발 모드의 StrictMode는 effect를 두 번 실행한다. 같은 filters면 진행 중인 요청을 재사용하고,
// 다시 구독할 때는 지금까지 받은 이벤트를 처음부터 다시 적용한다.
function startJob(filters, excludeIds, participantId) {
  const job = { filters, excludeIds, events: [], listeners: new Set() };
  job.promise = requestRecommendationStream(
    filters,
    excludeIds,
    participantId,
    (event) => {
      job.events.push(event);
      job.listeners.forEach((listener) => listener(event));
    }
  );
  return job;
}

export default function Loading({
  filters,
  excludeIds,
  participantId,
  setResult,
  onComplete,
}) {
  const navigate = useNavigate();

  const [progress, dispatch] = useReducer(progressReducer, undefined, initialProgress);
  const jobRef = useRef(null);

  // 백엔드에 실제 추천 요청 + 진행 이벤트 구독
  useEffect(() => {
    let active = true;

    const current = jobRef.current;
    if (current?.filters !== filters || current?.excludeIds !== excludeIds) {
      jobRef.current = startJob(filters, excludeIds, participantId);
    }
    const job = jobRef.current;

    dispatch({ type: "reset" });
    job.events.forEach(dispatch);
    job.listeners.add(dispatch);

    job.promise
      .then((data) => {
        if (!active) return;
        setResult(data);
        dispatch({ type: "finished" });
      })
      .catch((error) => {
        if (!active) return;
        console.error("추천 API 호출 실패:", error);
        dispatch({
          type: "error",
          message: error.message || "서버에 연결하지 못했습니다.",
        });
      });

    return () => {
      active = false;
      job.listeners.delete(dispatch);
    };
  }, [filters, excludeIds, participantId, setResult]);

  const requestError = progress.error;
  const resultReady = progress.finished;

  // 결과를 받으면 완료 상태를 잠깐 보여준 뒤 이동
  useEffect(() => {
    if (!resultReady || requestError) return;

    const timer = setTimeout(() => {
      if (onComplete) {
        onComplete();
      } else {
        navigate("/result");
      }
    }, 700);

    return () => clearTimeout(timer);
  }, [resultReady, requestError, onComplete, navigate]);

  return (
    <div className="loading-page">
      <div className="loading-container">
        <header className="loading-heading">
          <p className="loading-eyebrow">ANALYZING</p>
          <h1 className="loading-title">
            {requestError
              ? "추천을 완료하지 못했어요"
              : "에이전트가 분석 중입니다"}
          </h1>
          <p className="loading-subtitle">
            {requestError
              ? "아래 오류를 확인하고 다시 시도해 주세요."
              : headline(progress)}
          </p>
        </header>

        <div className="loading-cards">
          {AGENTS.map((agent) => {
            const view = agentView(progress, agent.id);
            const { done } = view;

            return (
              <section
                key={agent.id}
                className={`loading-card loading-card-${agent.id} ${
                  done ? "is-done" : "is-running"
                }`}
              >
                <div className="loading-card-top">
                  <div className="loading-icon">
                    <agent.Icon />
                  </div>
                  <div className="loading-info">
                    <h2 className="loading-agent-name">{agent.name}</h2>
                    <p className="loading-status">
                      {done ? "평가 완료" : `${agent.desc} 중`}
                    </p>
                  </div>

                  {done ? (
                    <span className="loading-check" aria-label="평가 완료">
                      ✓
                    </span>
                  ) : (
                    <span className="loading-spinner" aria-label="평가 중" />
                  )}
                </div>

                <ul className="loading-steps">
                  {view.steps.map((step, index) => (
                    <li
                      key={index}
                      className={`loading-step ${
                        step.done ? "is-done" : "is-active"
                      }`}
                    >
                      <span className="loading-step-mark">
                        {step.done ? (
                          "✓"
                        ) : (
                          <span className="loading-step-spinner" />
                        )}
                      </span>
                      {step.text}
                    </li>
                  ))}
                </ul>

                <div
                  className={`loading-bar ${
                    view.progress === null ? "is-indeterminate" : ""
                  }`}
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={
                    view.progress === null ? undefined : Math.round(view.progress)
                  }
                >
                  <div
                    className="loading-bar-fill"
                    style={
                      view.progress === null
                        ? undefined
                        : { width: `${view.progress}%` }
                    }
                  />
                </div>
              </section>
            );
          })}
        </div>

        {requestError ? (
          <div className="loading-footer" role="alert">
            <p>추천에 실패했어요: {requestError}</p>
            <button
              type="button"
              onClick={() => navigate("/input")}
            >
              입력 조건 다시 선택하기
            </button>
          </div>
        ) : (
          <p className="loading-footer">
            잠깐만 기다려 주세요. 최적의 콘텐츠를 찾고 있습니다.
          </p>
        )}
      </div>
    </div>
  );
}
