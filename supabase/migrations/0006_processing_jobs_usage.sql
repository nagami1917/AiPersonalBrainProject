-- ============================================================================
-- 0006 — processing_jobs (the queue) and usage_events
-- ============================================================================
-- Postgres-backed queue (ADR-002). A work item may target a note OR a document,
-- so it carries the same CHECK-constrained single-owner pattern as chunks.
-- ============================================================================

create table if not exists public.processing_jobs (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,

  -- exactly one target for content jobs; both null for workspace-level jobs
  note_id       uuid references public.notes(id) on delete cascade,
  document_id   uuid references public.documents(id) on delete cascade,

  job_type      text not null
                  check (job_type in ('ingest_note','ingest_document','reindex','entities','summary','purge')),
  status        text not null default 'queued'
                  check (status in ('queued','running','succeeded','failed','dead','superseded')),
  priority      integer not null default 100,
  attempts      integer not null default 0 check (attempts >= 0),
  max_attempts  integer not null default 5 check (max_attempts >= 1),
  run_after     timestamptz not null default now(),
  locked_by     text,
  locked_at     timestamptz,
  payload       jsonb not null default '{}'::jsonb,
  last_error    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  constraint jobs_attempts_within_max check (attempts <= max_attempts),
  constraint jobs_single_target check (not (note_id is not null and document_id is not null))
);

-- the claim query's only index: partial, ordered, small
create index if not exists idx_jobs_claimable
  on public.processing_jobs (priority, run_after)
  where status = 'queued';

-- staleness sweeper: requeue workers that died mid-job
create index if not exists idx_jobs_running_locked
  on public.processing_jobs (locked_at)
  where status = 'running';

create index if not exists idx_jobs_workspace_status
  on public.processing_jobs (workspace_id, status);

create index if not exists idx_jobs_note on public.processing_jobs (note_id) where note_id is not null;
create index if not exists idx_jobs_document on public.processing_jobs (document_id) where document_id is not null;

drop trigger if exists trg_jobs_updated_at on public.processing_jobs;
create trigger trg_jobs_updated_at
  before update on public.processing_jobs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- usage_events — per-tenant cost visibility from day one
-- ---------------------------------------------------------------------------
create table if not exists public.usage_events (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  event_type    text not null check (event_type in ('chat','embed','ingest','search')),
  note_id       uuid references public.notes(id) on delete set null,
  document_id   uuid references public.documents(id) on delete set null,
  tokens        integer not null default 0 check (tokens >= 0),
  cost_estimate numeric(10,6) not null default 0 check (cost_estimate >= 0),
  created_at    timestamptz not null default now()
);

create index if not exists idx_usage_events_workspace_created
  on public.usage_events (workspace_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Enqueue helper: uniform job insertion for API and worker.
-- ---------------------------------------------------------------------------
create or replace function public.enqueue_job(
  p_workspace_id uuid,
  p_job_type     text,
  p_note_id      uuid default null,
  p_document_id  uuid default null,
  p_payload      jsonb default '{}'::jsonb,
  p_priority     integer default 100,
  p_run_after    timestamptz default now()
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_job_id uuid;
begin
  -- The function is SECURITY DEFINER so it can insert without relying on an
  -- insert policy; that makes this check load-bearing. Without it, granting
  -- execute to `authenticated` would let any signed-in user queue work in any
  -- workspace whose id they can guess.
  if not public.can_write_workspace(p_workspace_id) then
    raise exception 'not permitted to enqueue jobs in workspace %', p_workspace_id
      using errcode = '42501';
  end if;

  if p_note_id is not null and p_document_id is not null then
    raise exception 'a job targets a note or a document, not both'
      using errcode = '23514';
  end if;

  insert into public.processing_jobs
    (workspace_id, job_type, note_id, document_id, payload, priority, run_after)
  values
    (p_workspace_id, p_job_type, p_note_id, p_document_id,
     coalesce(p_payload, '{}'::jsonb), p_priority, p_run_after)
  returning id into v_job_id;

  return v_job_id;
end;
$$;

comment on function public.enqueue_job(uuid, text, uuid, uuid, jsonb, integer, timestamptz) is
  'Uniform job insertion. SECURITY DEFINER so the API can enqueue without a direct write policy.';
