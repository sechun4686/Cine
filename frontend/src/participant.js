// 사용자 테스트 참가자 번호
// 테스트 링크에 ?pid=P01 처럼 붙여서 보내면, 이후 모든 추천/피드백 로그에 이 번호가 함께 기록된다.
// (한 번 들어오면 브라우저에 저장되므로 다른 화면으로 이동해도 유지된다)

const STORAGE_KEY = 'cine_participant_id'

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function initParticipantId() {
  const fromUrl = new URLSearchParams(window.location.search).get('pid')
  const clean = (fromUrl || '').trim().replace(/[^\w-]/g, '').slice(0, 32)

  if (clean) {
    try {
      localStorage.setItem(STORAGE_KEY, clean)
    } catch {
      // 저장이 막힌 브라우저(시크릿 모드 등)에서도 이번 방문 동안은 사용
    }
    return clean
  }
  return readStored()
}
