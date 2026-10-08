import mock from '../mock/result.json'

function Result() {
  return (
    <div>
      <h1>결과 화면</h1>
      <p>{mock.best.title} / {mock.best.final_score}점</p>
    </div>
  )
}
export default Result