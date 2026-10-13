
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Tooltip,
  ResponsiveContainer
} from 'recharts'
import { useNavigate } from 'react-router-dom'
import '../main.css'

const agentInfo = [
  {
    key: 'technical',
    label: '작품성 · 연출',
    shortLabel: '작품성',
    color: '#35B8D0'
  },
  {
    key: 'narrative',
    label: '서사 · 감성',
    shortLabel: '서사',
    color: '#50BBAA'
  },
  {
    key: 'public',
    label: '대중성 · 몰입도',
    shortLabel: '대중성',
    color: '#F0A16B'
  }
]

function toTenPointScore(value) {
  const score = Number(value ?? 0)
  return Math.max(0, Math.min(10, score > 10 ? score / 10 : score))
}

function ScoreTooltip({ active, payload }) {
  if (!active || !payload?.length) return null

  const item = payload[0].payload
  const agent = agentInfo.find((a) => a.key === item.key)

  return (
    <div className="score-tooltip">
      <div className="tooltip-heading">
        <span
          className="tooltip-dot"
          style={{ background: agent?.color }}
        />
        {item.subject}
      </div>
      <strong className="tooltip-score">
        {Number(item.score).toFixed(1)}
        <span> / 10점</span>
      </strong>
      <p>{item.reason}</p>
    </div>
  )
}

function Result({ result }) {
  const navigate = useNavigate()
  const best = result?.best
  const others = result?.others ?? []

  if (!best) {
    return (
      <div className="result-page">
        <header className="result-header">
          <div>
            <span className="result-eyebrow">CINE AGENT</span>
            <h1>아직 추천 결과가 없어요</h1>
            <p>먼저 입력 조건을 선택하고 추천을 시작해 주세요.</p>
          </div>
        </header>
        <button type="button" onClick={() => navigate('/input')}>
          추천 조건 선택하기
        </button>
      </div>
    )
  }

  const scores = best.scores ?? {}
  const reasons = best.reasons ?? {}
  const weights = best.weights ?? result.weights ?? {}

  const chartData = agentInfo.map((agent) => ({
    subject: agent.label,
    key: agent.key,
    score: toTenPointScore(scores[agent.key]),
    reason: reasons[agent.key] ?? best.reason ?? '추천 근거가 없습니다.'
  }))

  const finalScore = toTenPointScore(best.final_score)
  const cast = Array.isArray(best.cast)
    ? best.cast.join(', ')
    : best.cast || '출연 정보 없음'

  return (
    <div className="result-page">
      <header className="result-header">
        <div>
          <span className="result-eyebrow">YOUR PERFECT MATCH</span>
          <h1>세 에이전트의 선택이 모였어요</h1>
        </div>

        <div className="match-badge">
          <strong>{finalScore.toFixed(1)}</strong>
          <span>FINAL SCORE</span>
        </div>
      </header>

      <section className="movie-hero">
        <div className="poster-area">
          {best.poster_url ? (
            <img src={best.poster_url} alt={`${best.title} 포스터`} />
          ) : (
            <div className="poster-placeholder">CINE AGENT</div>
          )}
        </div>

        <div className="movie-info">
          <span className="recommend-label">오늘의 최종 추천</span>
          <h2>{best.title}</h2>
          <p className="movie-subtitle">
            {best.title} · {best.year || '개봉 연도 정보 없음'}
          </p>

          <div className="movie-meta">
            <span>★ {finalScore.toFixed(1)}</span>
            <span>{best.director || '감독 정보 없음'}</span>
          </div>

          <p className="movie-cast">출연 · {cast}</p>

          <p className="movie-description">
            {best.overview || best.editor_comment || best.reason || '작품 정보가 없습니다.'}
          </p>

          <div className="editor-comment">
            <span className="comment-icon">✦</span>
            <div>
              <strong>세 에이전트가 함께 고른 이유</strong>
              <p>{best.editor_comment || best.reason || '입력한 조건과 작품 정보를 바탕으로 선정했습니다.'}</p>
            </div>
          </div>
        </div>
      </section>

      <div className="result-panels">
        <section className="result-panel score-panel">
          <div className="panel-eyebrow">AGENT SCORE</div>
          <h3>어떤 점이 잘 맞았을까요?</h3>
          <p className="panel-hint">
            차트의 각 항목에 마우스를 올려 세부 점수를 확인하세요.
          </p>

          <div className="radar-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart
                data={chartData}
                cx="50%"
                cy="50%"
                outerRadius="68%"
              >
                <PolarGrid stroke="#E6E3DD" />
                <PolarAngleAxis
                  dataKey="subject"
                  tick={{ fill: '#666666', fontSize: 12 }}
                />
                <PolarRadiusAxis
                  angle={90}
                  domain={[0, 10]}
                  tick={false}
                  axisLine={false}
                />
                <Radar
                  name="에이전트 점수"
                  dataKey="score"
                  stroke="#35B8D0"
                  fill="#35B8D0"
                  fillOpacity={0.22}
                  strokeWidth={2.5}
                  dot={{
                    r: 5,
                    fill: '#FFFFFF',
                    stroke: '#35B8D0',
                    strokeWidth: 2
                  }}
                  activeDot={{
                    r: 7,
                    fill: '#35B8D0',
                    stroke: '#FFFFFF',
                    strokeWidth: 2
                  }}
                />
                <Tooltip content={<ScoreTooltip />} cursor={false} />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          <div className="agent-score-list">
            {agentInfo.map((agent) => (
              <div className="agent-score-item" key={agent.key}>
                <span
                  className="agent-color-dot"
                  style={{ background: agent.color }}
                />
                <span>{agent.shortLabel}</span>
                <strong>
                  {toTenPointScore(scores[agent.key]).toFixed(1)}
                </strong>
              </div>
            ))}
          </div>
        </section>

        <section className="result-panel weight-panel">
          <div className="panel-eyebrow">WEIGHT LOGIC</div>
          <h3>이번 추천의 판단 기준</h3>
          <p className="weight-intro">
            세 가지 관점의 점수와 추천 근거를 확인해 보세요.
          </p>

          {agentInfo.map((agent) => {
            const weight = Number(weights[agent.key] ?? 0)
            const score = toTenPointScore(scores[agent.key])

            return (
              <div className="weight-item" key={agent.key}>
                <div className="weight-item-top">
                  <div>
                    <span
                      className="weight-dot"
                      style={{ background: agent.color }}
                    />
                    <strong>{agent.label}</strong>
                  </div>
                  <strong>{Math.round(weight * 100)}%</strong>
                </div>

                <div className="weight-track">
                  <div
                    className="weight-fill"
                    style={{
                      width: `${weight * 100}%`,
                      background: agent.color
                    }}
                  />
                </div>

                <p className="weight-reason">
                  {reasons[agent.key] ?? best.reason ?? '추천 근거 정보 없음'}
                </p>

                <span className="weight-score">
                  해당 항목 점수 {score.toFixed(1)} / 10
                </span>
              </div>
            )
          })}
        </section>
      </div>

      {others.length > 0 && (
        <section className="other-movies">
          <div className="panel-eyebrow">MORE PICKS</div>
          <h3>함께 고려한 작품</h3>

          <div className="other-movie-list">
            {others.map((movie) => (
              <article
                className="other-movie-card"
                key={`${movie.media_type || 'movie'}-${movie.id}`}
              >
                {movie.poster_url ? (
                  <img src={movie.poster_url} alt={`${movie.title} 포스터`} />
                ) : (
                  <div className="other-poster-placeholder">CINE</div>
                )}
                <div>
                  <strong>{movie.title}</strong>
                  <p>{movie.year} · {movie.director || '감독 정보 없음'}</p>
                  <span>★ {toTenPointScore(movie.final_score).toFixed(2)}</span>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      <footer className="result-footer">
        <p>이 추천이 마음에 드시나요?</p>
        <span>입력 조건을 바꿔 새로운 작품을 발견해 보세요.</span>
        <button type="button" onClick={() => navigate('/input')}>
          다시 추천받기 <span>↻</span>
        </button>
      </footer>
    </div>
  )
}

export default Result

