-- ============================================================================
-- seed.sql — local development corpus
-- ============================================================================
-- Creates two users with deliberately overlapping-looking content so that
-- cross-tenant leakage is visible in manual testing as well as in the test
-- suite. Run after migrations: `supabase db reset` applies migrations + seed.
--
-- Embeddings here are deterministic pseudo-vectors (unit-normalized from a
-- hash), NOT model output. They exist so the vector arm of hybrid_search can be
-- exercised without calling a provider. Real ingest always overwrites them.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Users (auth.users insert fires handle_new_user → profiles)
-- ---------------------------------------------------------------------------
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password,
  email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
  created_at, updated_at, confirmation_token, recovery_token,
  email_change_token_new, email_change
)
values
  ('00000000-0000-0000-0000-000000000000',
   '11111111-1111-1111-1111-111111111111', 'authenticated', 'authenticated',
   'alice@nexus.local', crypt('password123', gen_salt('bf')),
   now(), '{"provider":"email","providers":["email"]}',
   '{"full_name":"Alice Rivera"}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000',
   '22222222-2222-2222-2222-222222222222', 'authenticated', 'authenticated',
   'bob@nexus.local', crypt('password123', gen_salt('bf')),
   now(), '{"provider":"email","providers":["email"]}',
   '{"full_name":"Bob Santos"}', now(), now(), '', '', '', '')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Alice's workspace
-- ---------------------------------------------------------------------------
insert into public.collections (id, user_id, name, description)
values ('aaaaaaaa-0000-0000-0000-000000000001',
        '11111111-1111-1111-1111-111111111111',
        'Engineering', 'Notes about building NEXUS')
on conflict (id) do nothing;

insert into public.documents
  (id, user_id, collection_id, source_type, title, mime_type, raw_text, page_count,
   status, embedding_model, summary, metadata)
values
  ('dddddddd-0000-0000-0000-000000000001',
   '11111111-1111-1111-1111-111111111111',
   'aaaaaaaa-0000-0000-0000-000000000001',
   'note', 'Deployment secrets policy', null,
   E'Secrets live in exactly one place per environment.\n\nThe Supabase service role key is stored only in Fly secrets. It is never placed in Vercel environment variables, because the frontend bundle is public by construction.\n\nThe JWT secret used to verify inbound user tokens is also a Fly secret. Rotating it invalidates every active session, so it is rotated deliberately and not during an incident unless tokens are known to be compromised.\n\nLocal development uses the Supabase CLI stack, which has its own service role key. Do not copy production secrets into a local .env file.',
   1, 'ready', 'text-embedding-3-small',
   'Where secrets live per environment, and why the service role key never reaches the frontend.',
   '{}'::jsonb),
  ('dddddddd-0000-0000-0000-000000000002',
   '11111111-1111-1111-1111-111111111111',
   'aaaaaaaa-0000-0000-0000-000000000001',
   'note', 'Retrieval scoring notes', null,
   E'Reciprocal Rank Fusion is used instead of score normalization because cosine distance and ts_rank_cd are on incomparable scales.\n\nThe fusion constant k is set to 60. Vector weight is 1.0 and keyword weight is 0.7, reflecting that personal knowledge queries are paraphrase-heavy but exact identifiers still matter.\n\nRRF needs no calibration and stays stable as the corpus grows, which matters more than squeezing a few points of recall at small scale.',
   1, 'ready', 'text-embedding-3-small',
   'Why retrieval fuses two arms with RRF and how the weights were chosen.',
   '{}'::jsonb),
  ('dddddddd-0000-0000-0000-000000000003',
   '11111111-1111-1111-1111-111111111111',
   'aaaaaaaa-0000-0000-0000-000000000001',
   'note', 'Job queue backoff behaviour', null,
   E'The worker claims jobs with FOR UPDATE SKIP LOCKED, so two workers never process the same row.\n\nRetry backoff is exponential: min(60 * 2^attempts, 3600) seconds, written into run_after. After max_attempts the job moves to dead and the document surfaces a visible failure instead of an endless spinner.\n\nA sweeper requeues jobs stuck in running with a locked_at older than fifteen minutes, which is how a crashed worker recovers.',
   1, 'ready', 'text-embedding-3-small',
   'How the Postgres job queue claims work, backs off, and recovers from crashes.',
   '{}'::jsonb)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Bob's workspace — intentionally similar vocabulary, different owner.
-- If any query ever returns Bob's text to Alice, the test suite has failed.
-- ---------------------------------------------------------------------------
insert into public.collections (id, user_id, name, description)
values ('bbbbbbbb-0000-0000-0000-000000000001',
        '22222222-2222-2222-2222-222222222222',
        'Personal', 'Bob private stuff')
on conflict (id) do nothing;

insert into public.documents
  (id, user_id, collection_id, source_type, title, mime_type, raw_text, page_count,
   status, embedding_model, summary, metadata)
values
  ('eeeeeeee-0000-0000-0000-000000000001',
   '22222222-2222-2222-2222-222222222222',
   'bbbbbbbb-0000-0000-0000-000000000001',
   'note', 'Bob private deployment notes', null,
   E'Bob also writes about deployment secrets but this document belongs to a different tenant entirely.\n\nThe service role key for Bob''s project is not the same as anyone else''s. This text exists solely to prove that tenant isolation holds when two users have near-identical vocabulary.',
   1, 'ready', 'text-embedding-3-small',
   'Bob''s own deployment notes about secrets and keys.',
   '{}'::jsonb)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Chunks with deterministic pseudo-embeddings (see file header).
-- ---------------------------------------------------------------------------
create or replace function pg_temp.pseudo_embedding(seed text, dim integer default 1536)
returns vector
language sql
immutable
as $$
  select (
    '[' || string_agg(
      (case when (abs(hashtext(seed || ':' || g)) % 1000) / 1000.0 = 0
            then 0.001
            else (abs(hashtext(seed || ':' || g)) % 1000) / 1000.0
       end)::text,
      ',' order by g
    ) || ']'
  )::vector
  from generate_series(1, dim) as g;
$$;

insert into public.chunks
  (id, user_id, document_id, ordinal, content, token_count, char_start, char_end,
   heading_path, embedding, embedding_model, version)
values
  ('cccccccc-0000-0000-0000-000000000001',
   '11111111-1111-1111-1111-111111111111',
   'dddddddd-0000-0000-0000-000000000001', 0,
   'The Supabase service role key is stored only in Fly secrets. It is never placed in Vercel environment variables, because the frontend bundle is public by construction.',
   38, 72, 232, 'Deployment secrets policy',
   pg_temp.pseudo_embedding('alice-secrets-1'), 'text-embedding-3-small', 1),
  ('cccccccc-0000-0000-0000-000000000002',
   '11111111-1111-1111-1111-111111111111',
   'dddddddd-0000-0000-0000-000000000001', 1,
   'The JWT secret used to verify inbound user tokens is also a Fly secret. Rotating it invalidates every active session.',
   24, 234, 343, 'Deployment secrets policy',
   pg_temp.pseudo_embedding('alice-secrets-2'), 'text-embedding-3-small', 1),
  ('cccccccc-0000-0000-0000-000000000003',
   '11111111-1111-1111-1111-111111111111',
   'dddddddd-0000-0000-0000-000000000002', 0,
   'Reciprocal Rank Fusion is used instead of score normalization because cosine distance and ts_rank_cd are on incomparable scales. The fusion constant k is set to 60.',
   30, 0, 158, 'Retrieval scoring notes',
   pg_temp.pseudo_embedding('alice-fusion'), 'text-embedding-3-small', 1),
  ('cccccccc-0000-0000-0000-000000000004',
   '11111111-1111-1111-1111-111111111111',
   'dddddddd-0000-0000-0000-000000000003', 0,
   'The worker claims jobs with FOR UPDATE SKIP LOCKED, so two workers never process the same row. Retry backoff is exponential.',
   23, 0, 127, 'Job queue backoff behaviour',
   pg_temp.pseudo_embedding('alice-jobs'), 'text-embedding-3-small', 1),
  ('cccccccc-0000-0000-0000-000000000005',
   '22222222-2222-2222-2222-222222222222',
   'eeeeeeee-0000-0000-0000-000000000001', 0,
   'Bob also writes about deployment secrets but this document belongs to a different tenant entirely.',
   17, 0, 103, 'Bob private deployment notes',
   pg_temp.pseudo_embedding('bob-secrets'), 'text-embedding-3-small', 1)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- One conversation with a cited answer, to exercise the citation read path.
-- ---------------------------------------------------------------------------
insert into public.conversations (id, user_id, title)
values ('ffffffff-0000-0000-0000-000000000001',
        '11111111-1111-1111-1111-111111111111',
        'Where do secrets live?')
on conflict (id) do nothing;

insert into public.messages (id, user_id, conversation_id, role, content, grounded)
values
  ('99999999-0000-0000-0000-000000000001',
   '11111111-1111-1111-1111-111111111111',
   'ffffffff-0000-0000-0000-000000000001', 'user',
   'Where did I put the service role key?', null),
  ('99999999-0000-0000-0000-000000000002',
   '11111111-1111-1111-1111-111111111111',
   'ffffffff-0000-0000-0000-000000000001', 'assistant',
   'In Fly secrets, never in Vercel [1]. The frontend bundle is public by construction, so the key is kept server-side only.', true)
on conflict (id) do nothing;

insert into public.message_citations
  (user_id, message_id, chunk_id, document_id, document_title, marker, quote, score)
values
  ('11111111-1111-1111-1111-111111111111',
   '99999999-0000-0000-0000-000000000002',
   'cccccccc-0000-0000-0000-000000000001',
   'dddddddd-0000-0000-0000-000000000001',
   'Deployment secrets policy', 1,
   'The Supabase service role key is stored only in Fly secrets.', 0.8123)
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- One queued job, so the worker has something to show on a fresh boot.
-- ---------------------------------------------------------------------------
insert into public.jobs (user_id, document_id, job_type, status, payload)
values ('11111111-1111-1111-1111-111111111111',
        'dddddddd-0000-0000-0000-000000000002',
        'summary', 'queued', '{"reason":"seed"}'::jsonb);
