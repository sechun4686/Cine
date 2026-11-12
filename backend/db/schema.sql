-- CineAgent 로그 DB 스키마 (Supabase SQL Editor에 붙여넣고 Run)
--
-- recommendations : 추천 요청 1회 = 1행 (입력 조건, 추론된 가중치, 최종 추천작, 응답 시간)
-- candidates      : 요청마다 평가한 후보 1개 = 1행 (세 에이전트 점수, 최종 점수) -> 가중치 민감도 분석에 사용
-- feedback        : 결과 화면 버튼 클릭 1회 = 1행 (이거 볼래요 / 이미 봤어요 / 관심 없어요) -> 첫 추천 수락률
--
-- 테이블끼리는 request_id로 연결한다. 로그 저장 순서가 꼬여도 실패하지 않도록 외래키는 걸지 않았다.

create table if not exists recommendations (
    request_id      uuid primary key,
    created_at      timestamptz not null default now(),
    participant_id  text,                     -- 사용자 테스트 참가자 번호 (링크의 ?pid=P01)
    query           text,
    filters         jsonb,                    -- {"ott": [...], "genres": [...], "media": [...], "runtime": 120}
    exclude_ids     jsonb,                    -- 재추천 새 검색일 때 제외한 작품
    status          text not null,            -- ok | error
    error           text,
    elapsed_sec     real,                     -- 요청 처리 시간
    pool_size       integer,                  -- 후보군 크기
    weights         jsonb,                    -- {"technical": 0.289, "narrative": 0.563, "public": 0.148}
    importance      jsonb,                    -- LLM이 추론한 중요도 (0~5)
    weight_reason   text,
    weight_status   text,                     -- ok | filtered | fallback
    best_key        text,                     -- "movie:123"
    best_title      text
);

create table if not exists candidates (
    id              bigint generated always as identity primary key,
    request_id      uuid not null,
    rank            integer not null,         -- 최종 순위 (1 = 추천작)
    item_key        text not null,
    title           text,
    label           text,                     -- 선별 기준 (평점·인기도 균형 등)
    similarity      real,
    vote_average    real,
    vote_count      integer,
    popularity      real,
    technical       real,                     -- 평가 실패면 null
    narrative       real,
    public          real,
    final_score     real,
    failed_agents   jsonb,
    reasons         jsonb,                    -- 에이전트별 평가 근거
    tools           jsonb                     -- 에이전트별 선택 도구 {"technical": ["director", ...], ...}
);

create table if not exists feedback (
    id              bigint generated always as identity primary key,
    created_at      timestamptz not null default now(),
    request_id      uuid,
    participant_id  text,
    feedback        text not null check (feedback in ('accepted', 'watched', 'not_interested')),
    item_key        text,                     -- 피드백을 받은 작품
    title           text,
    shown_order     integer,                  -- 이 요청에서 몇 번째로 보여준 작품인지 (1 = 첫 추천)
    next_item_key   text,                     -- 재추천으로 새로 보여준 작품 (없으면 null)
    next_title      text,
    need_new_search boolean
);

create index if not exists candidates_request_idx on candidates (request_id);
create index if not exists feedback_request_idx on feedback (request_id);

-- 외부(브라우저)에서 직접 읽고 쓰지 못하게 RLS를 켜고 정책은 만들지 않는다.
-- 백엔드는 secret(service_role) 키를 쓰므로 RLS와 무관하게 저장할 수 있다.
alter table recommendations enable row level security;
alter table candidates enable row level security;
alter table feedback enable row level security;
