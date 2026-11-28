# 실험 실행 가이드

두 실험은 순서대로 한다. 실험 1의 원자료 CSV를 실험 2가 그대로 읽는다 (실험 2는 LLM 호출 없음).

`backend` 폴더에서, `.env`에 `TMDB_API_KEY`와 `LUXIA_API_KEY`가 있는 상태로 실행한다.
결과는 `backend/scripts/results/`에 저장된다.

## 평가 규칙 변경 후 재실험 (필수)

에이전트 평가 규칙을 바꿨으므로 이전 결과는 현재 시스템의 결과가 아니다. 코드를 더 고치지 않는 상태(동결)에서
실험 1 → 실험 2 → 실험 4를 이전과 같은 조건으로 한 번씩 다시 돌리고, 그 결과를 그대로 쓴다.
(여러 번 돌렸다면 일부만 고르지 말고 전부의 평균을 보고한다.) User study 출력물도 같은 버전으로 만든다.

바뀐 규칙 (multi와 single 모두에 똑같이 적용된다)
- 핵심 도구 2개 고정: 관점마다 핵심 데이터는 항상 보고, 에이전트는 추가 도구 0~2개만 스스로 고른다
  (technical: director, director_filmography / narrative: overview_full, keywords / public: vote_stats, reviews)
- 관점별 점수 기준: 각 관점은 자기 기준으로만 채점한다. 요청의 분위기·내용 적합도는 서사 관점이 맡고,
  기술·대중성 관점은 요청이 그 요소를 직접 언급할 때만 요청과의 일치를 반영한다.
- 실험 4 보고서에 관점 간 점수 상관(후광 효과)이 추가됐다. single이 multi보다 높으면 관점이 덜 분리된 것이다.

## 실험 1. 점수 일관성 및 재평가

쿼리 5개마다 후보 5편을 한 번만 뽑아 고정하고, 같은 후보를 5회 다시 평가한다.
조건 4개를 돌려 점수 편차가 어디서 오는지 나눠 본다.

```bash
python scripts/measure_reproducibility.py --label t02-free                                    # 서비스 설정 그대로
python scripts/measure_reproducibility.py --label t02-fixed --fixed-tools                     # 도구 고정
python scripts/measure_reproducibility.py --label t07-free  --eval-temperature 0.7             # 예전 temperature
python scripts/measure_reproducibility.py --label t07-fixed --eval-temperature 0.7 --fixed-tools
```

| 조건 | LLM 호출 | 비교로 알 수 있는 것 |
|---|---|---|
| t02-free vs t07-free | 각 약 775회 | temperature를 0.7 → 0.2로 낮춘 효과 |
| t02-free vs t02-fixed | 775회 / 400회 | 점수 편차 중 도구 선택이 차지하는 몫 |
| t02-fixed | 400회 | 같은 데이터를 다시 채점할 때의 순수 편차 |

먼저 `--runs 2 --queries 1`로 짧게 돌려 동작을 확인한 뒤 본 실험을 돌리는 것을 권장한다.

보고서에 쓸 지표 (`*.md` 요약표)
- 에이전트별 점수 표준편차, 범위(최대−최소), 평가 실패율
- 쿼리별 1위 일치율, 순위 상관(Kendall τ)

## 실험 2. 가중치 민감도 분석

```bash
python scripts/weight_sensitivity.py scripts/results/reproducibility_t02-free_*_raw.csv --label t02-free
```

에이전트 점수는 그대로 두고 가중치만 바꿔 순위를 다시 계산한다.

1. 가중치 그리드 66가지 → 1위 유지율, 순위 상관, 삼각 그래프(`*_grid.png`)
2. Softmax 온도 0.5 ~ 3 / 동일 가중치
3. LLM 중요도 ±1 (중요도를 1점 틀렸을 때 추천이 유지되는지)
4. 에이전트 점수끼리의 상관 (높으면 관점이 겹쳐 가중치 영향이 작음)
5. 1·2위 점수 차

서비스와 같은 조건(t02-free) 결과를 기본으로 쓰고, 여러 CSV를 한 번에 넣으면 합쳐서 분석한다.
사용자 테스트 후에는 Supabase 로그(`db/analysis.sql` 5번)로도 같은 분석을 할 수 있다.

## 실험 4. Single vs Multi agent 비교

```bash
python scripts/compare_single_multi.py --runs 1 --queries 1 --label check        # 동작 확인 (LLM 약 50회)
python scripts/compare_single_multi.py --runs 3 --label full                      # 본 실험 (LLM 약 610회)
python scripts/compare_single_multi.py --runs 3 --label score --single-mode score # 보조 분석 (LLM 약 535회)
python scripts/compare_single_multi.py --runs 3 --label full-new --query-set new  # 추가 쿼리 5개 (설문 B형)
```

쿼리 세트 (`--query-set`)
- `base` (기본): 실험 1과 같은 쿼리 5개 → 설문 A형
- `new`: 추가 쿼리 5개 → 설문 B형. 상황(비 오는 날, 주말 정주행), 대상(친구), 제외 조건(잔인하지 않은 공포), 주제(SF)로 범위를 넓혔고, 결과를 보기 전에 정했다
- `all`: 10개를 한 번에

설문은 A형(base)과 B형(new)으로 나눠 참가자 번호 홀짝으로 배정한다. 참가자 1명은 5문항만 답한다.

쿼리마다 후보 5편과 가중치를 한 번만 정해 고정하고, 회차마다 multi → single 순서로 평가한다.
평가 기준 문구·점수 기준·가중치 계산·temperature는 두 방식이 같다.

| single 모드 | 도구 선택 | 채점 | 후보 1편당 LLM 호출 | 비교로 알 수 있는 것 |
|---|---|---|---|---|
| multi (기준) | 에이전트 3개가 각자 | 에이전트 3개가 각자 | 6회 | - |
| **full (기본)** | 에이전트 1개가 14개 도구 중 직접 선택 (관점마다 2~4개, 필수 도구는 multi와 같음) | 1회에 세 관점 | 2회 | **완전한 multi vs single**: 관점을 나눈 구조 전체의 효과 |
| score | multi가 고른 데이터를 그대로 받음 | 1회에 세 관점 | 1회 | 채점을 나눈 효과만 (도구 선택은 multi와 같음) |

full과 score 결과를 함께 보면 multi의 효과가 도구 선택에서 오는지, 채점에서 오는지 나눠 볼 수 있다.
코드: `services/single_agent_service.py` (`evaluate_candidates_single(select_tools=True)`가 full 모드)

보고서에 쓸 지표 (`compare_*.md`)
1. 두 방식의 1위 일치율, 순위 상관(Kendall τ)
2. 같은 후보에 대한 관점별 점수 차이(single − multi), 후보 간 점수 분산(작품을 얼마나 구분하는지)
3. 방식별 일관성: 회차 간 점수 표준편차, 1위 일치율, 실패율 (`--runs 2` 이상)
4. 비용: LLM 호출 수, LLM 응답 시간 합, 전체 소요 시간

설문 자료 (full 모드 결과로 만든다)
- `compare_*_survey.md`: 쿼리마다 두 방식의 1위 결과를 A/B로 배치 (방식 이름·점수 없음, A/B 위치는 절반씩). 구글 폼에 그대로 옮긴다
- `compare_*_survey_key.csv`: A/B 정답표. 참가자에게 보여주지 않는다
- 두 방식 1위가 같은 작품인 문항(`same_title`)은 근거 설명만 비교한다

`compare_*_single_raw.csv`, `compare_*_multi_raw.csv`는 실험 1 원자료와 같은 형식이라
`weight_sensitivity.py`에 넣어 두 방식의 가중치 민감도도 비교할 수 있다.
