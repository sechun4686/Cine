
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Tooltip,
  ResponsiveContainer
} from 'recharts'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_URL } from '../constants'
import '../main.css'

const FEEDBACK_OPTIONS = [
  { value: 'watched', label: '이미 봤어요' },
  { value: 'not_interested', label: '관심 없어요' },
]

function candidateKey(item) {
  return `${item?.media_type || 'movie'}:${item?.id}`
}

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

function Result({
  result,
  setResult,
  filters,
  excludeIds = [],
  setExcludeIds,
  participantId = '',
  shownOrder = 1,
  setShownOrder,
}) {
  const navigate = useNavigate()
  const best = result?.best
  const others = result?.others ?? []
  const [pendingFeedback, setPendingFeedback] = useState('')
  const [feedbackError, setFeedbackError] = useState('')
  const [acceptedKey, setAcceptedKey] = useState('')
  const accepted = Boolean(best) && acceptedKey === candidateKey(best)

  // '이거 볼래요': 재추천 없이 기록만 남긴다 (첫 추천 수락률 분석용)
  async function handleAccept() {
    if (!best || accepted) return
    setAcceptedKey(candidateKey(best))
    setFeedbackError('')
    try {
      await fetch(`${API_URL}/api/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feedback: 'accepted',
          item: best,
          request_id: result?.request_id ?? '',
          participant_id: participantId,
          shown_order: shownOrder,
        }),
      })
    } catch (error) {
      // 기록 실패는 사용자 경험에 영향을 주지 않도록 콘솔에만 남긴다
      console.error('피드백 기록 실패:', error)
    }
  }

  // 피드백 기반 재추천: 평가가 끝난 남은 후보 중에서 고르고, 없으면 제외 목록을 붙여 새로 검색
  async function handleFeedback(feedback) {
    if (!best || pendingFeedback) return

    setPendingFeedback(feedback)
    setFeedbackError('')
    const nextExclude = [...new Set([...excludeIds, candidateKey(best)])]

    try {
      const response = await fetch(`${API_URL}/api/rerecommend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: filters?.query ?? '',
          feedback,
          rejected: best,
          candidates: others,
          request_id: result?.request_id ?? '',
          participant_id: participantId,
          shown_order: shownOrder,
        }),
      })
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : `재추천 API 오류 (${response.status})`
        )
      }

      setShownOrder?.(shownOrder + 1)

      if (data.need_new_search) {
        // 남은 후보가 마땅치 않음 -> 이번에 평가한 작품을 모두 빼고 새로 검색
        setExcludeIds?.([...new Set([...nextExclude, ...others.map(candidateKey)])])
        navigate('/loading')
        return
      }

      setExcludeIds?.(nextExclude)
      setResult?.({
        ...result,
        best: data.best,
        others: data.others ?? [],
        rerecommend_note: data.note,
      })
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (error) {
      console.error('재추천 실패:', error)
      setFeedbackError(error.message || '재추천에 실패했어요. 잠시 후 다시 시도해 주세요.')
    } finally {
      setPendingFeedback('')
    }
  }

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

          {result.rerecommend_note && (
            <p className="rerecommend-note">↻ {result.rerecommend_note}</p>
          )}

          <div className="editor-comment">
            <span className="comment-icon">✦</span>
            <div>
              <strong>세 에이전트가 함께 고른 이유</strong>
              <p>{best.editor_comment || best.reason || '입력한 조건과 작품 정보를 바탕으로 선정했습니다.'}</p>
            </div>
          </div>

          <div className="feedback-actions">
            {accepted ? (
              <p className="feedback-thanks">좋은 선택이에요! 즐거운 감상 되세요 🎬</p>
            ) : (
              <button
                type="button"
                className="feedback-button feedback-accept"
                disabled={Boolean(pendingFeedback)}
                onClick={handleAccept}
              >
                이거 볼래요
              </button>
            )}
            <div>
              {FEEDBACK_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.value}
                  className="feedback-button"
                  disabled={Boolean(pendingFeedback)}
                  onClick={() => handleFeedback(option.value)}
                >
                  {pendingFeedback === option.value ? '다른 작품 찾는 중...' : option.label}
                </button>
              ))}
            </div>
            {feedbackError && (
              <p className="feedback-error" role="alert">{feedbackError}</p>
            )}
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

