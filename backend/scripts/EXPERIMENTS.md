# 실험 실행 가이드

두 실험은 순서대로 한다. 실험 1의 원자료 CSV를 실험 2가 그대로 읽는다 (실험 2는 LLM 호출 없음).

`backend` 폴더에서, `.env`에 `TMDB_API_KEY`와 `LUXIA_API_KEY`가 있는 상태로 실행한다.
결과는 `backend/scripts/results/`에 저장된다.

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
