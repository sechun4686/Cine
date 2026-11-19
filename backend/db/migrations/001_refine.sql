-- 001: '추가 요청으로 다시 추천받기'(refine) 로그
-- schema.sql로 테이블을 이미 만든 Supabase에서 한 번 실행한다 (SQL Editor에 붙여넣고 Run).
-- 새로 만드는 경우에는 schema.sql에 이미 반영되어 있어 실행하지 않아도 된다.

alter table recommendations add column if not exists parent_request_id uuid;
alter table feedback add column if not exists refine_text text;

alter table feedback drop constraint if exists feedback_feedback_check;
alter table feedback add constraint feedback_feedback_check
    check (feedback in ('accepted', 'watched', 'not_interested', 'refine'));
