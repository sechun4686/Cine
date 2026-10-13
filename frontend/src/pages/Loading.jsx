import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import "./Loading.css";
// 에이전트 이름/단계 문구는 여기만 고치면 화면에 반영돼요.
// steps: [진행 중일 때 문구, 끝났을 때 문구]
// speed: 한 단계가 끝나는 데 걸리는 시간(ms) — 에이전트마다 다르게 해서 각자 평가 중인 느낌을 줌
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

export default function Loading({ onComplete }) {
  const navigate = useNavigate();
  // 에이전트별로 "끝난 단계 수" (0이면 1단계 진행 중)
  const [completed, setCompleted] = useState(() =>
    Object.fromEntries(AGENTS.map((a) => [a.id, 0]))
  );

  // 에이전트마다 자기 속도로 단계를 하나씩 완료 처리
  useEffect(() => {
    const timers = AGENTS.map((agent) =>
      setInterval(() => {
        setCompleted((prev) => {
          if (prev[agent.id] >= agent.steps.length) return prev;
          return { ...prev, [agent.id]: prev[agent.id] + 1 };
        });
      }, agent.speed)
    );
    return () => timers.forEach(clearInterval);
  }, []);

  const allDone = AGENTS.every((a) => completed[a.id] >= a.steps.length);

  // 3개 에이전트가 모두 끝나면 잠깐 보여준 뒤 다음 화면으로 이동
  // (나중에 실제 API 응답을 연결하면, 응답이 왔을 때 onComplete를 호출하도록 바꾸면 됨)
  
  useEffect(() => {
    if (!allDone) return;

    const t = setTimeout(() => {
      if (onComplete) {
        onComplete();
      } else {
        navigate("/result");
      }
    }, 1000);

    return () => clearTimeout(t);
  }, [allDone, onComplete, navigate]);

  return (
    <div className="loading-page">
      <div className="loading-container">
        <header className="loading-heading">
          <p className="loading-eyebrow">ANALYZING</p>
          <h1 className="loading-title">에이전트가 분석 중입니다</h1>
          <p className="loading-subtitle">
            3개의 AI 에이전트가 각자의 관점에서 작품을 평가하고 있습니다
          </p>
        </header>

        <div className="loading-cards">
          {AGENTS.map((agent) => {
            const total = agent.steps.length;
            const doneCount = completed[agent.id];
            const done = doneCount >= total;
            // 끝난 단계 + 지금 진행 중인 단계 1개까지만 보여줌
            const visibleCount = done ? total : doneCount + 1;
            const progress = done ? 100 : ((doneCount + 0.5) / total) * 100;

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
                  {agent.steps.slice(0, visibleCount).map(([doing, finished], i) => {
                    const stepDone = i < doneCount;
                    return (
                      <li
                        key={doing}
                        className={`loading-step ${stepDone ? "is-done" : "is-active"}`}
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

        <p className="loading-footer">
          잠깐만 기다려 주세요. 최적의 콘텐츠를 찾고 있습니다.
        </p>
      </div>
    </div>
  );
}
