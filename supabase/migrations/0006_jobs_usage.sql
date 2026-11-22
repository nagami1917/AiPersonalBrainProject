-- ============================================================================
-- 0006 — background jobs (queue) and usage accounting
-- ============================================================================
-- Postgres-backed queue (ADR-002). Claim pattern documented in DATABASE.md §3.8.
-- ============================================================================

create table if not exists public.jobs (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  document_id  uuid references public.documents(id) on delete cascade,
  job_type     text not null check (job_type in ('ingest','reindex','entities','summary','purge')),
  status       text not null default 'queued'
                 check (status in ('queued','running','succeeded','failed','dead','superseded')),
  priority     integer not null default 100,
  attempts     integer not null default 0 check (attempts >= 0),
  max_attempts integer not null default 5 check (max_attempts >= 1),
  run_after    timestamptz not null default now(),
  locked_by    text,
  locked_at    timestamptz,
  payload      jsonb not null default '{}'::jsonb,
  last_error   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),

  constraint jobs_attempts_within_max check (attempts <= max_attempts)
);

-- the claim query's only index: cheap, partial, ordered
create index if not exists idx_jobs_claimable
  on public.jobs (priority, run_after)
  where status = 'queued';

-- staleness sweeper: requeue workers that died mid-job
create index if not exists idx_jobs_running_locked
  on public.jobs (locked_at)
  where status = 'running';

create index if not exists idx_jobs_user_status on public.jobs (user_id, status);
create index if not exists idx_jobs_document on public.jobs (document_id);

drop trigger if exists trg_jobs_updated_at on public.jobs;
create trigger trg_jobs_updated_at
  before update on public.jobs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- usage_events — per-tenant cost and quota visibility from day one
-- ---------------------------------------------------------------------------
create table if not exists public.usage_events (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  event_type     text not null check (event_type in ('chat','embed','ingest','search')),
  document_id    uuid references public.documents(id) on delete set null,
  tokens         integer not null default 0 check (tokens >= 0),
  cost_estimate  numeric(10,6) not null default 0 check (cost_estimate >= 0),
  created_at     timestamptz not null default now()
);

create index if not exists idx_usage_events_user_created
  on public.usage_events (user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Enqueue helper: used by API and worker so job insertion is uniform.
-- ---------------------------------------------------------------------------
create or replace function public.enqueue_job(
  p_user_id     uuid,
  p_job_type    text,
  p_document_id uuid default null,
  p_payload     jsonb default '{}'::jsonb,
  p_priority    integer default 100,
  p_run_after   timestamptz default now()
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job_id uuid;
begin
  insert into public.jobs (user_id, document_id, job_type, payload, priority, run_after)
  values (p_user_id, p_document_id, p_job_type, coalesce(p_payload, '{}'::jsonb), p_priority, p_run_after)
  returning id into v_job_id;

  return v_job_id;
end;
$$;

comment on function public.enqueue_job(uuid, text, uuid, jsonb, integer, timestamptz) is
  'Uniform job insertion. SECURITY DEFINER so the API can enqueue under RLS.';
