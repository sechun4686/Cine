import { useNavigate } from "react-router-dom";
import "../Main.css";

function AgentCard({ icon, title, subtitle, score, className }) {
  return (
    <div className={`agent-card ${className}`}>
      <div className="agent-icon">{icon}</div>

      <div className="agent-info">
        <div className="agent-title">{title}</div>
        <div className="agent-subtitle">{subtitle}</div>
      </div>

      <div className="agent-score">{score}</div>
    </div>
  );
}

function Main() {
  const navigate = useNavigate();

  return (
    <div className="app">

      {/* Header */}
      <header className="header">
        <div className="logo">
          <div className="logo-icon">✦</div>
          <span>Cine Agent</span>
        </div>

        <div className="status">
          <span className="status-dot"></span>
          3 AI 에이전트가 함께 골라드려요
        </div>
      </header>


      {/* Main */}
      <main className="hero">

        {/* 왼쪽 */}
        <section className="hero-left">

          <div className="eyebrow">
            <span>✦</span>
            MULTI-AGENT CURATION
          </div>

          <h1>
            오늘 밤,<br />
            <span>무엇을 볼지</span> 고민되나요?
          </h1>

          <p className="description">
            세 명의 AI 큐레이터가 당신의 취향을 각자의 관점으로 분석하고,
            <br />
            가장 완벽한 한 편을 찾아드려요.
          </p>


          {/* 버튼 */}
          <button
            className="start-button"
            onClick={() => navigate("/input")}
          >
            나만의 작품 찾기
            <span>→</span>
          </button>


          <div className="features">
            <div>
              <span>✓</span> 무료로 시작
            </div>

            <div>
              <span>✓</span> 회원가입 필요없음
            </div>
          </div>

        </section>


        {/* 오른쪽 */}
        <section className="visual">

          {/* 배경 */}
          <div className="glow"></div>

          <div className="circle circle-1"></div>
          <div className="circle circle-2"></div>
          <div className="circle circle-3"></div>


          {/* 시네필 에이전트 */}
          <AgentCard
            icon="▣"
            title="시네필 에이전트"
            subtitle="작품성 · 연출 · 미학"
            score="9.2"
            className="card-cinephile"
          />


          {/* 대중성 에이전트 */}
          <AgentCard
            icon="♧"
            title="대중성 에이전트"
            subtitle="몰입도 · 재미 · 반응"
            score="8.8"
            className="card-popular"
          />


          {/* 취향 분석 에이전트 */}
          <AgentCard
            icon="♧"
            title="취향 분석 에이전트"
            subtitle="개인화 · 맥락 · 발견"
            score="9.5"
            className="card-preference"
          />


          {/* 중앙 */}
          <div className="center">

            <div className="center-icon">
              ✦
            </div>

            <div className="center-title">
              당신의 취향
            </div>

            <div className="center-subtitle">
              세 관점의 교집합
            </div>

          </div>

        </section>
      </main>


      {/* 하단 */}
      <div className="bottom-text">
        취향을 말씀해주시면 약 10초 후 추천이 완성됩니다
      </div>

    </div>
  );
}

export default Main;