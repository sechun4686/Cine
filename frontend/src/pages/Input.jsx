import { useState } from "react";
import { useNavigate } from "react-router-dom";
import "./Input.css";

// label: 화면에 보이는 이름 / value: 백엔드(OTT_MAP, GENRE_MAP)에서 쓰는 키
const OTT_OPTIONS = [
  { label: "Netflix", value: "넷플릭스" },
  { label: "TVING", value: "티빙" },
  { label: "Disney+", value: "디즈니+" },
  { label: "Watcha", value: "왓챠" },
  { label: "wavve", value: "웨이브" },
  { label: "Coupang Play", value: "쿠팡플레이" },
  { label: "Apple TV+", value: "애플TV+" },
];

const GENRE_OPTIONS = [
  "스릴러",
  "드라마",
  "액션",
  "로맨스",
  "SF",
  "공포",
  "애니메이션",
  "코미디",
  "다큐멘터리",
  "범죄",
];

// value: movie / tv / animation  (중복 선택 가능, 빈 배열 = 전체)
const MEDIA_OPTIONS = [
  { label: "영화", value: "movie" },
  { label: "드라마/시리즈", value: "tv" },
  { label: "애니메이션", value: "animation" },
];

const RUNTIME_MIN = 0;
const RUNTIME_MAX = 180;
const RUNTIME_STEP = 10;

export default function Input({ onSubmit, setFilters }) {
  const navigate = useNavigate();
  const [ott, setOtt] = useState([]);
  const [genres, setGenres] = useState([]);
  const [media, setMedia] = useState([]);
  const [runtime, setRuntime] = useState(120);
  const [query, setQuery] = useState("");

  const toggle = (list, setList, item) => {
    setList(list.includes(item) ? list.filter((v) => v !== item) : [...list, item]);
  };

  const fillPercent =
    ((runtime - RUNTIME_MIN) / (RUNTIME_MAX - RUNTIME_MIN)) * 100;

  const handleSubmit = () => {
    const payload = {
      ott,
      genres,
      media,
      runtime,
      query: query.trim(),
    };

    if (setFilters) {
      setFilters(payload);
    }

    if (onSubmit) {
      onSubmit(payload);
    }

    navigate("/loading");
    };

  return (
    <div className="input-page">
      <div className="input-container">
        <header className="input-heading">
          <p className="input-eyebrow">STEP 1</p>
          <h1 className="input-title">취향을 알려주세요</h1>
          <p className="input-subtitle">
            더 많이 입력할수록 더 정확한 추천을 드릴 수 있습니다
          </p>
        </header>

        {/* OTT */}
        <section className="input-card">
          <h2 className="input-card-title">OTT 플랫폼</h2>
          <p className="input-card-desc">보유 중인 서비스를 선택하세요 (복수 선택 가능)</p>
          <div className="input-chips">
            {OTT_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`input-chip ${ott.includes(o.value) ? "is-selected" : ""}`}
                aria-pressed={ott.includes(o.value)}
                onClick={() => toggle(ott, setOtt, o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </section>

        {/* 장르 */}
        <section className="input-card">
          <h2 className="input-card-title">선호 장르</h2>
          <p className="input-card-desc">좋아하는 장르를 선택하세요 (복수 선택 가능)</p>
          <div className="input-chips">
            {GENRE_OPTIONS.map((g) => (
              <button
                key={g}
                type="button"
                className={`input-chip ${genres.includes(g) ? "is-selected" : ""}`}
                aria-pressed={genres.includes(g)}
                onClick={() => toggle(genres, setGenres, g)}
              >
                {g}
              </button>
            ))}
          </div>
        </section>

        {/* 미디어 유형 */}
        <section className="input-card">
          <h2 className="input-card-title">미디어 유형</h2>
          <p className="input-card-desc">콘텐츠 종류를 선택하세요 (복수 선택 가능)</p>
          <div className="input-chips">
            {MEDIA_OPTIONS.map((m) => (
              <button
                key={m.value}
                type="button"
                className={`input-chip ${media.includes(m.value) ? "is-selected" : ""}`}
                aria-pressed={media.includes(m.value)}
                onClick={() => toggle(media, setMedia, m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
        </section>

        {/* 런타임 */}
        <section className="input-card">
          <h2 className="input-card-title">최대 런타임</h2>
          <p className="input-card-desc">{runtime}분 이하의 콘텐츠를 추천합니다</p>
          <div className="input-slider-row">
            <input
              className="input-slider"
              type="range"
              min={RUNTIME_MIN}
              max={RUNTIME_MAX}
              step={RUNTIME_STEP}
              value={runtime}
              aria-label="최대 런타임(분)"
              style={{ "--fill": `${fillPercent}%` }}
              onChange={(e) => setRuntime(Number(e.target.value))}
            />
            <span className="input-slider-value">{runtime}분</span>
          </div>
          <div className="input-slider-scale">
            <span>0분</span>
            <span>1시간30분</span>
            <span>3시간</span>
          </div>
        </section>

        {/* 자연어 검색 */}
        <section className="input-card">
          <h2 className="input-card-title">자연어 검색</h2>
          <p className="input-card-desc">
            자유롭게 원하는 콘텐츠를 설명해 주세요 (선택)
          </p>
          <textarea
            className="input-textarea"
            value={query}
            placeholder="예) 친구들과 함께 보기 좋은 가볍고 힐링되는 드라마 보고싶어요. 스토리가 탄탄하고 깊은 감동을 주는 컨텐츠가 필요해요..."
            onChange={(e) => setQuery(e.target.value)}
          />
        </section>

        <button type="button" className="input-submit" onClick={handleSubmit}>
          에이전트 분석 시작하기 <span>→</span>
        </button>
      </div>
    </div>
  );
}
