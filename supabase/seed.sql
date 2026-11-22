-- ============================================================================
-- seed.sql — local development corpus
-- ============================================================================
-- Creates:
--   • Alice  → her own personal workspace, populated with notes + a document
--   • Bob    → his own personal workspace with NEARLY IDENTICAL vocabulary
--   • Carol  → her own workspace, plus a viewer membership in Alice's
--
-- Bob's deliberate vocabulary overlap means tenant leakage is visible to the
-- naked eye during manual testing, not just in assertions.
--
-- Seed embeddings are deterministic pseudo-vectors, NOT model output. They exist
-- so the vector arm of hybrid_search can be exercised without a provider. Real
-- ingest always overwrites them.
--
-- Applying this twice is safe: every insert is guarded.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Users — the signup trigger creates profiles, workspaces, and owner membership
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
   '{"full_name":"Alice"}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000',
   '22222222-2222-2222-2222-222222222222', 'authenticated', 'authenticated',
   'bob@nexus.local', crypt('password123', gen_salt('bf')),
   now(), '{"provider":"email","providers":["email"]}',
   '{"full_name":"Bob"}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000',
   '33333333-3333-3333-3333-333333333333', 'authenticated', 'authenticated',
   'carol@nexus.local', crypt('password123', gen_salt('bf')),
   now(), '{"provider":"email","providers":["email"]}',
   '{"full_name":"Carol"}', now(), now(), '', '', '', '')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Deterministic pseudo-embedding helper (validation/testing only)
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

-- ---------------------------------------------------------------------------
-- Alice: a populated workspace
-- ---------------------------------------------------------------------------
do $$
declare
  v_ws      uuid;
  v_note1   uuid := 'aaaaaaaa-0000-0000-0000-000000000001';
  v_note2   uuid := 'aaaaaaaa-0000-0000-0000-000000000002';
  v_doc     uuid := 'dddddddd-0000-0000-0000-000000000001';
  v_coll    uuid := 'cccccccc-0000-0000-0000-000000000001';
  v_tag     uuid;
begin
  select id into v_ws from public.workspaces
   where owner_id = '11111111-1111-1111-1111-111111111111';

  insert into public.collections (id, workspace_id, name, description)
  values (v_coll, v_ws, 'Engineering', 'Notes about building NEXUS')
  on conflict (id) do nothing;

  insert into public.notes (id, workspace_id, collection_id, created_by, title, content, status,
                            embedding_model, summary)
  values
    (v_note1, v_ws, v_coll, '11111111-1111-1111-1111-111111111111',
     'Deployment secrets policy',
     E'Secrets live in exactly one place per environment.\n\nThe Supabase service role key is stored only in Fly secrets. It is never placed in Vercel environment variables, because the frontend bundle is public by construction.\n\nThe JWT secret used to verify inbound user tokens is also a Fly secret. Rotating it invalidates every active session, so it is rotated deliberately.',
     'ready', 'text-embedding-3-small',
     'Where secrets live per environment, and why the service role key never reaches the frontend.'),
    (v_note2, v_ws, v_coll, '11111111-1111-1111-1111-111111111111',
     'Retrieval scoring notes',
     E'Reciprocal Rank Fusion is used instead of score normalization because cosine distance and ts_rank_cd are on incomparable scales.\n\nThe fusion constant k is set to 60. Vector weight is 1.0 and keyword weight is 0.7, reflecting that personal knowledge queries are paraphrase-heavy but exact identifiers still matter.\n\nSimilarity, not rank, gates the grounded decision: HNSW returns neighbours for any query, so only an absolute cosine threshold can express irrelevance.',
     'ready', 'text-embedding-3-small',
     'Why retrieval fuses two arms with RRF and why the relevance gate is cosine similarity.')
  on conflict (id) do nothing;

  insert into public.documents (id, workspace_id, collection_id, uploaded_by, title,
                                filename, mime_type, file_path, file_size,
                                raw_text, page_count, status, embedding_model, summary)
  values (v_doc, v_ws, v_coll, '11111111-1111-1111-1111-111111111111',
          'thesis-notes.pdf', 'thesis-notes.pdf', 'application/pdf',
          v_ws || '/' || v_doc || '/thesis-notes.pdf', 1843200,
          E'Chapter 3: Deployment. Fly secrets are set with flyctl secrets set. The worker claims jobs with FOR UPDATE SKIP LOCKED so two workers never process the same row.',
          4, 'ready', 'text-embedding-3-small', 'Chapter on deployment mechanics and queue claiming.')
  on conflict (id) do nothing;

  insert into public.tags (workspace_id, name) values (v_ws, 'research')
  on conflict do nothing;
  select id into v_tag from public.tags where workspace_id = v_ws and lower(name) = 'research';

  insert into public.note_tags (note_id, tag_id, workspace_id)
  values (v_note1, v_tag, v_ws) on conflict do nothing;

  insert into public.document_chunks (workspace_id, note_id, chunk_index, content, token_count,
                                      heading_path, embedding, embedding_model, version)
  values
    (v_ws, v_note1, 0,
     'The Supabase service role key is stored only in Fly secrets. It is never placed in Vercel environment variables, because the frontend bundle is public by construction.',
     38, 'Deployment secrets policy', pg_temp.pseudo_embedding('alice-secrets-1'),
     'text-embedding-3-small', 1),
    (v_ws, v_note1, 1,
     'The JWT secret used to verify inbound user tokens is also a Fly secret. Rotating it invalidates every active session.',
     24, 'Deployment secrets policy', pg_temp.pseudo_embedding('alice-secrets-2'),
     'text-embedding-3-small', 1),
    (v_ws, v_note2, 0,
     'Reciprocal Rank Fusion is used instead of score normalization because cosine distance and ts_rank_cd are on incomparable scales. The fusion constant k is set to 60.',
     30, 'Retrieval scoring notes', pg_temp.pseudo_embedding('alice-fusion'),
     'text-embedding-3-small', 1)
  on conflict (note_id, chunk_index, version) where note_id is not null do nothing;

  insert into public.document_chunks (workspace_id, document_id, chunk_index, content, token_count,
                                      page_number, heading_path, embedding, embedding_model, version)
  values
    (v_ws, v_doc, 0,
     'Fly secrets are set with flyctl secrets set. The worker claims jobs with FOR UPDATE SKIP LOCKED so two workers never process the same row.',
     31, 3, 'Chapter 3 > Deployment', pg_temp.pseudo_embedding('alice-thesis-1'),
     'text-embedding-3-small', 1)
  on conflict (document_id, chunk_index, version) where document_id is not null do nothing;
end $$;

-- ---------------------------------------------------------------------------
-- Bob: separate workspace, deliberately similar vocabulary
-- ---------------------------------------------------------------------------
do $$
declare
  v_ws    uuid;
  v_note  uuid := 'eeeeeeee-0000-0000-0000-000000000001';
begin
  select id into v_ws from public.workspaces
   where owner_id = '22222222-2222-2222-2222-222222222222';

  insert into public.notes (id, workspace_id, created_by, title, content, status,
                            embedding_model, summary)
  values (v_note, v_ws, '22222222-2222-2222-2222-222222222222',
          'Bob private deployment notes',
          E'Bob also writes about deployment secrets, but this note belongs to a different workspace entirely.\n\nThe service role key for Bob''s project is not the same as anyone else''s. This text exists solely to prove that workspace isolation holds when two tenants use near-identical vocabulary.',
          'ready', 'text-embedding-3-small', 'Bob''s own notes about secrets and keys.')
  on conflict (id) do nothing;

  insert into public.document_chunks (workspace_id, note_id, chunk_index, content, token_count,
                                      heading_path, embedding, embedding_model, version)
  values
    (v_ws, v_note, 0,
     'Bob also writes about deployment secrets, but this note belongs to a different workspace entirely.',
     17, 'Bob private deployment notes', pg_temp.pseudo_embedding('bob-secrets'),
     'text-embedding-3-small', 1)
  on conflict (note_id, chunk_index, version) where note_id is not null do nothing;
end $$;

-- ---------------------------------------------------------------------------
-- Carol: viewer membership in Alice's workspace (demonstrates sharing)
-- ---------------------------------------------------------------------------
do $$
declare
  v_alice_ws uuid;
begin
  select id into v_alice_ws from public.workspaces
   where owner_id = '11111111-1111-1111-1111-111111111111';

  insert into public.workspace_members (workspace_id, user_id, role, invited_by)
  values (v_alice_ws, '33333333-3333-3333-3333-333333333333', 'viewer',
          '11111111-1111-1111-1111-111111111111')
  on conflict (workspace_id, user_id) do nothing;
end $$;

-- ---------------------------------------------------------------------------
-- A conversation with a cited answer, to exercise the citation read path
-- ---------------------------------------------------------------------------
do $$
declare
  v_ws      uuid;
  v_conv    uuid := 'ffffffff-0000-0000-0000-000000000001';
  v_msg     uuid := '99999999-0000-0000-0000-000000000002';
  v_chunk   uuid;
begin
  select id into v_ws from public.workspaces
   where owner_id = '11111111-1111-1111-1111-111111111111';

  insert into public.conversations (id, workspace_id, created_by, title)
  values (v_conv, v_ws, '11111111-1111-1111-1111-111111111111', 'Where do secrets live?')
  on conflict (id) do nothing;

  insert into public.chat_messages (id, workspace_id, conversation_id, role, content, grounded)
  values
    ('99999999-0000-0000-0000-000000000001', v_ws, v_conv, 'user',
     'Where did I put the service role key?', null),
    (v_msg, v_ws, v_conv, 'assistant',
     'In Fly secrets, never in Vercel [1]. The frontend bundle is public by construction, so the key stays server-side.', true)
  on conflict (id) do nothing;

  select id into v_chunk from public.document_chunks
   where workspace_id = v_ws and note_id is not null order by chunk_index limit 1;

  insert into public.message_sources
    (workspace_id, message_id, chunk_id, source_kind, note_id, source_title, marker, quote, relevance_score)
  values
    (v_ws, v_msg, v_chunk, 'note',
     (select note_id from public.document_chunks where id = v_chunk),
     'Deployment secrets policy', 1,
     'The Supabase service role key is stored only in Fly secrets.', 0.8123)
  on conflict do nothing;
end $$;

-- ---------------------------------------------------------------------------
-- A queued job, so the worker has something to show on a fresh boot
-- ---------------------------------------------------------------------------
do $$
declare
  v_ws   uuid;
  v_note uuid;
begin
  select id into v_ws from public.workspaces
   where owner_id = '11111111-1111-1111-1111-111111111111';
  select id into v_note from public.notes
   where workspace_id = v_ws and title = 'Retrieval scoring notes' limit 1;

  -- Idempotent: at most one demo job per workspace, so re-seeding does not
  -- accumulate duplicates.
  if not exists (
    select 1 from public.processing_jobs
     where workspace_id = v_ws and type = 'summary' and note_id = v_note
  ) then
    insert into public.processing_jobs (workspace_id, note_id, type, status, payload)
    values (v_ws, v_note, 'summary', 'queued', '{"reason":"seed"}'::jsonb);
  end if;
end $$;
