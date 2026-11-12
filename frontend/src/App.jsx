import { useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import Main from './pages/Main'
import Input from './pages/Input'
import Loading from './pages/Loading'
import Result from './pages/Result'
import { initParticipantId } from './participant'

function App() {
  // 공통 상태: 입력값, 백엔드 응답, 재추천용 제외 목록
  const [filters, setFilters] = useState({
    ott: [], genres: [], media: 'movie', runtime: 120, query: ''
  })
  const [result, setResult] = useState(null)
  const [excludeIds, setExcludeIds] = useState([])
  // 로그용: 사용자 테스트 참가자 번호(?pid=), 이번 검색에서 몇 번째로 보여주는 추천인지
  const [participantId] = useState(initParticipantId)
  const [shownOrder, setShownOrder] = useState(1)

  const shared = {
    filters, setFilters,
    result, setResult,
    excludeIds, setExcludeIds,
    participantId,
    shownOrder, setShownOrder,
  }

  return (
    <Routes>
      <Route path="/" element={<Main />} />
      <Route path="/input" element={<Input {...shared} />} />
      <Route path="/loading" element={<Loading {...shared} />} />
      <Route path="/result" element={<Result {...shared} />} />
    </Routes>
  )
}

export default App