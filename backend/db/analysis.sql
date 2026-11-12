-- CineAgent 사용자 테스트 분석용 쿼리 (Supabase SQL Editor에서 실행, 결과는 CSV로 내보내기 가능)

-- 1) 참가자별 요약: 검색 수, 평균 응답 시간, 피드백 수
select r.participant_id,
       count(distinct r.request_id)                             as searches,
       round(avg(r.elapsed_sec)::numeric, 1)                    as avg_sec,
       count(f.id) filter (where f.feedback = 'accepted')       as accepted,
       count(f.id) filter (where f.feedback <> 'accepted')      as rejected
from recommendations r
left join feedback f on f.request_id = r.request_id
where r.status = 'ok' and r.exclude_ids = '[]'::jsonb           -- 재추천용 새 검색은 제외
group by r.participant_id
order by r.participant_id;

-- 2) 첫 추천 수락률: 첫 번째로 보여준 작품(shown_order = 1)에 대한 피드백 중 '이거 볼래요' 비율
--    (버튼을 아무것도 누르지 않은 경우는 분모에서 빠진다)
select count(*) filter (where feedback = 'accepted')            as accepted,
       count(*)                                                 as total,
       round(100.0 * count(*) filter (where feedback = 'accepted') / nullif(count(*), 0), 1) as accept_rate_pct
from feedback
where shown_order = 1;

-- 3) 몇 번째 추천에서 수락했는지 분포 (1 = 첫 추천)
select shown_order, count(*) as accepted
from feedback
where feedback = 'accepted'
group by shown_order
order by shown_order;

-- 4) 응답 시간과 오류
select status, count(*), round(avg(elapsed_sec)::numeric, 1) as avg_sec,
       round(max(elapsed_sec)::numeric, 1) as max_sec
from recommendations
group by status;

-- 5) 가중치 민감도 분석용 원자료: 요청별 후보 점수 + 그 요청의 가중치
--    (CSV로 내보낸 뒤 가중치를 바꿔 가며 순위를 다시 계산한다)
select c.request_id, r.query, r.weights, c.rank, c.title, c.label,
       c.technical, c.narrative, c.public, c.final_score, c.similarity
from candidates c
join recommendations r on r.request_id = c.request_id
order by r.created_at, c.rank;

-- 6) 평가 실패가 얼마나 자주 일어났는지 (에이전트별)
select agent, count(*) as failed
from candidates, jsonb_array_elements_text(failed_agents) as agent
group by agent;
