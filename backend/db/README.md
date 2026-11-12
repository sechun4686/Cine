# 로그 DB (Supabase) 설정 가이드

추천 요청, 후보 평가, 사용자 피드백을 Supabase(PostgreSQL)에 저장합니다.
사용자 테스트 결과 분석(첫 추천 수락률, 응답 시간)과 가중치 민감도 분석에 씁니다.

## 저장되는 데이터

| 테이블 | 1행의 의미 | 주요 컬럼 |
|---|---|---|
| `recommendations` | 추천 요청 1회 | 참가자 번호, 검색어, 입력 조건, 가중치, 최종 추천작, 응답 시간, 성공/실패 |
| `candidates` | 요청마다 평가한 후보 1개 | 순위, 세 에이전트 점수, 최종 점수, 평가 근거, 선택한 도구 |
| `feedback` | 결과 화면 버튼 클릭 1회 | 이거 볼래요 / 이미 봤어요 / 관심 없어요, 몇 번째 추천이었는지, 재추천된 작품 |

세 테이블은 `request_id`로 연결됩니다. 자세한 컬럼은 `schema.sql`을 보세요.

## 처음 설정 (10분)

1. https://supabase.com 에 가입하고 **New project**를 만듭니다. Region은 **Northeast Asia (Seoul)**를 고르세요.
2. 왼쪽 메뉴 **SQL Editor** → **New query**에 `schema.sql` 내용을 전부 붙여넣고 **Run**을 누릅니다.
3. **Table Editor**에서 `recommendations`, `candidates`, `feedback` 세 테이블이 생겼는지 확인합니다.
4. **Project Settings → API Keys**(또는 **API**)에서 두 값을 복사합니다.
   - Project URL → `SUPABASE_URL` (`https://xxxx.supabase.co`. 끝에 `/rest/v1`이 붙어 있어도 괜찮습니다)
   - **secret** 키(`sb_secret_...`) 또는 예전 화면의 **service_role** 키 → `SUPABASE_SECRET_KEY`
   - publishable/anon 키가 아닙니다. secret 키는 서버 전용이라 프론트엔드나 GitHub에 올리면 안 됩니다.
5. `backend/.env`에 두 값을 추가하고 백엔드를 다시 시작합니다(`.env.example` 참고).
6. 추천을 한 번 받아 보고 Table Editor에 행이 생기는지 확인합니다.

배포 서버(HF Spaces 등)에서는 같은 두 값을 서버의 Secrets에 등록하면 됩니다.

## Supabase 설정이 없거나 저장에 실패하면
추천은 정상 동작하고, 기록은 `backend/logs/<테이블>.jsonl` 파일에 대신 저장됩니다.
백엔드 터미널에 `[log_service] Supabase 저장 실패 ...`와 원인이 출력되니 키와 URL을 확인하세요.

## 사용자 테스트 링크
참가자마다 링크 끝에 `?pid=번호`를 붙여서 보내세요. 이후 모든 기록에 그 번호가 남아 설문 응답과 연결할 수 있습니다.

```
https://<배포 주소>/?pid=P01
https://<배포 주소>/?pid=P02
```

## 분석
`analysis.sql`에 바로 쓸 수 있는 쿼리가 있습니다(참가자별 요약, 첫 추천 수락률, 응답 시간, 민감도 분석용 원자료).
SQL Editor에서 실행한 결과는 오른쪽 위 메뉴에서 **CSV로 내보내기**할 수 있습니다.

## 주의
- 무료 프로젝트는 **7일 동안 요청이 없으면 일시 중지**됩니다. 대시보드에서 **Restore**를 누르면 다시 켜집니다. 사용자 테스트 전에 꼭 확인하세요.
- 개발 중에 쌓인 기록은 테스트 시작 전에 지우거나, 분석할 때 `created_at`으로 걸러 내세요.
