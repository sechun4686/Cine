
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import "./Loading.css";

const API_BASE_URL = "http://127.0.0.1:8000";

const AGENTS = [
  {
    id: "technical",
    icon: "🎬",
    name: "Technical 에이전트",
    desc: "기술적 완성도 평가",
    speed: 1500,
    steps: [
      ["감독 연출 역량 분석 중...", "감독 연출 역량 분석 완료"],
      ["촬영·영상미 평가 중...", "촬영·영상미 평가 완료"],
      ["제작 완성도 점수 계산 중...", "제작 완성도 점수 계산 완료"],
    ],
  },
  {
    id: "narrative",
    icon: "📖",
    name: "Narrative 에이전트",
    desc: "서사·감성 평가",
    speed: 1900,
    steps: [
      ["줄거리 구조 분석 중...", "줄거리 구조 분석 완료"],
      ["캐릭터 감성선 평가 중...", "캐릭터 감성선 평가 완료"],
      ["요청 키워드 부합도 확인 중...", "요청 키워드 부합도 확인 완료"],
      ["서사 점수 계산 중...", "서사 점수 계산 완료"],
    ],
  },
  {
    id: "public",
    icon: "👥",
    name: "Public 에이전트",
    desc: "대중성·트렌드 평가",
    speed: 1700,
    steps: [
      ["평점 신뢰도 확인 중...", "평점 신뢰도 확인 완료"],
      ["인기도·관객 반응 분석 중...", "인기도·관객 반응 분석 완료"],
      ["트렌드 부합도 평가 중...", "트렌드 부합도 평가 완료"],
      ["대중성 점수 계산 중...", "대중성 점수 계산 완료"],
    ],
  },
];

export default function Loading({
  filters,
  setResult,
  onComplete,
}) {
  const navigate = useNavigate();

  const [completed, setCompleted] = useState(() =>
    Object.fromEntries(AGENTS.map((agent) => [agent.id, 0]))
  );
  const [requestError, setRequestError] = useState("");
  const [resultReady, setResultReady] = useState(false);

  // 백엔드에 실제 추천 요청
  useEffect(() => {
    const controller = new AbortController();

    async function fetchRecommendation() {
      try {
        setRequestError("");
        setResultReady(false);

        if (!filters || typeof setResult !== "function") {
          throw new Error(
            "추천 입력값을 찾을 수 없습니다. 입력 페이지부터 다시 진행해 주세요."
          );
        }

        const payload = {
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
        };

        const response = await fetch(`${API_BASE_URL}/api/recommend`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            typeof data.detail === "string"
              ? data.detail
              : `추천 API 오류 (${response.status})`
          );
        }

        if (!data.best) {
          throw new Error(
            "추천 결과 형식이 예상과 다릅니다. 백엔드 응답을 확인해 주세요."
          );
        }

        setResult(data);
        setResultReady(true);
      } catch (error) {
        if (error.name === "AbortError") return;

        console.error("추천 API 호출 실패:", error);
        setRequestError(
          error.message || "서버에 연결하지 못했습니다."
        );
      }
    }

    fetchRecommendation();

    return () => controller.abort();
  }, [filters, setResult]);

  // 기존 로딩 애니메이션 유지
  useEffect(() => {
    const timers = AGENTS.map((agent) =>
      setInterval(() => {
        setCompleted((previous) => {
          if (previous[agent.id] >= agent.steps.length) {
            return previous;
          }

          return {
            ...previous,
            [agent.id]: previous[agent.id] + 1,
          };
        });
      }, agent.speed)
    );

    return () => timers.forEach(clearInterval);
  }, []);

  const allDone = AGENTS.every(
    (agent) => completed[agent.id] >= agent.steps.length
  );

  // 애니메이션과 실제 추천 요청이 모두 끝나야 이동
  useEffect(() => {
    if (!allDone || !resultReady || requestError) return;

    const timer = setTimeout(() => {
      if (onComplete) {
        onComplete();
      } else {
        navigate("/result");
      }
    }, 700);

    return () => clearTimeout(timer);
  }, [allDone, resultReady, requestError, onComplete, navigate]);

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
              : "3개의 AI 에이전트가 각자의 관점에서 작품을 평가하고 있습니다"}
          </p>
        </header>

        <div className="loading-cards">
          {AGENTS.map((agent) => {
            const total = agent.steps.length;
            const doneCount = completed[agent.id];
            const done = doneCount >= total;
            const visibleCount = done ? total : doneCount + 1;
            const progress = done
              ? 100
              : ((doneCount + 0.5) / total) * 100;

            return (
              <section
                key={agent.id}
                className={`loading-card loading-card-${agent.id} ${
                  done ? "is-done" : "is-running"
                }`}
              >
                <div className="loading-card-top">
                  <div className="loading-icon">{agent.icon}</div>
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
                  {agent.steps
                    .slice(0, visibleCount)
                    .map(([doing, finished], index) => {
                      const stepDone = index < doneCount;

                      return (
                        <li
                          key={doing}
                          className={`loading-step ${
                            stepDone ? "is-done" : "is-active"
                          }`}
                        >
                          <span className="loading-step-mark">
                            {stepDone ? (
                              "✓"
                            ) : (
                              <span className="loading-step-spinner" />
                            )}
                          </span>
                          {stepDone ? finished : doing}
                        </li>
                      );
                    })}
                </ul>

                <div className="loading-bar">
                  <div
                    className="loading-bar-fill"
                    style={{ width: `${progress}%` }}
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

