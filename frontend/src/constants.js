// 입력 선택지 (백엔드 OTT_MAP / GENRE_MAP의 키와 글자가 정확히 같아야 함)
export const OTT_LIST = [
  '넷플릭스', '웨이브', '왓챠', '디즈니+', '애플TV+', '쿠팡플레이', '티빙',
]

export const GENRE_LIST = [
  '로맨스', '코미디', '스릴러', '액션', 'SF', '판타지',
  '공포', '범죄', '드라마', '애니메이션', '다큐멘터리', '가족',
]

export const MEDIA_OPTIONS = [
  { value: 'movie', label: '영화' },
  { value: 'tv', label: '드라마' },
]

// 에이전트 색상 (입력 화면, 결과 화면 공통 사용)
export const AGENT_COLORS = {
  technical: '#5599ff',
  narrative: '#55cc88',
  public: '#cc88ff',
}

export const AGENT_LABELS = {
  technical: '기술적 완성도',
  narrative: '서사·감성',
  public: '대중성',
}

export const POINT_COLOR = '#ffcc44'

// 백엔드 주소 (나중에 .env의 VITE_API_URL로 교체 가능)
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'