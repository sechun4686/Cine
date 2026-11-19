// 로딩 화면 에이전트 아이콘 (24x24, currentColor 라인 아이콘)
// 색은 부모의 color(= 카드별 --accent)를 따른다.

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

// Technical: 카메라 뷰파인더 (프레임 모서리 + 렌즈) — 연출·제작 완성도
export function TechnicalIcon({ size = 22 }) {
  return (
    <svg {...base} width={size} height={size}>
      <path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8" />
      <path d="M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8" />
      <path d="M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16" />
      <path d="M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16" />
      <circle cx="12" cy="12" r="3.5" />
      <circle cx="12" cy="12" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Narrative: 서사 곡선 (발단 → 절정 → 결말) — 줄거리·감정선
export function NarrativeIcon({ size = 22 }) {
  return (
    <svg {...base} width={size} height={size}>
      <path d="M3.5 18c3-0.5 4.5-3 6-6.5S13 5 14.5 5s2.5 4 3.5 8 1.5 5 2.5 5" />
      <circle cx="3.5" cy="18" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="5" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="20.5" cy="18" r="1.3" fill="currentColor" stroke="none" />
      <path d="M3 21h18" strokeOpacity="0.35" />
    </svg>
  );
}

// Public: 반응 막대 + 상승 추세 — 평점·인기도·관객 반응
export function PublicIcon({ size = 22 }) {
  return (
    <svg {...base} width={size} height={size}>
      <path d="M5 20v-5" />
      <path d="M10 20v-8" />
      <path d="M15 20v-6" />
      <path d="M20 20V9" />
      <path d="M4 10.5 9.5 6l4 3L19 4" />
      <path d="M15.5 4H19v3.5" />
    </svg>
  );
}
