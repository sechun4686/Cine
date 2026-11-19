// 자체 SVG 아이콘 모음. 색은 currentColor를 따르므로 부모의 color로 바꾼다.
// 이모지 대신 이 파일에 아이콘을 추가해서 쓴다 (선 굵기 1.8, 24x24 기준으로 통일).

function Svg({ size = 16, children, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  )
}

export function IconHome(props) {
  return (
    <Svg {...props}>
      <path d="M3.5 10.5 12 4l8.5 6.5" />
      <path d="M5.5 9v10.5h4.5V14h4v5.5h4.5V9" />
    </Svg>
  )
}
