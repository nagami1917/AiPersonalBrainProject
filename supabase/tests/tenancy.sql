-- ============================================================================
-- tenancy.sql — workspace isolation assertions
-- ============================================================================
-- Run against a database with migrations applied. It is safe to run before or
-- after seed.sql: fixtures use a private aaaa…/bbbb…/cccc… UUID space and every
-- insert is idempotent, so a second run against the same database also passes.
--
--   psql "$DATABASE_URL" -f supabase/tests/tenancy.sql
--
-- Every "SECURITY FAILURE" notice means isolation is broken. Every "correctly
-- rejected"/"expect 0" line is the assertion passing. Designed to be read by a
-- human in the psql output, and to fail loudly if a policy is ever loosened.
-- ============================================================================

-- ===== fixtures: three users, three personal workspaces =====
-- UUIDs are namespaced under aaaa.../bbbb.../cccc... so this script never
-- collides with seed.sql and can be run repeatedly.
insert into auth.users (id, email, raw_user_meta_data) values
  ('aaaaaaaa-1111-1111-1111-111111111111','rls-alice@nexus.test','{"full_name":"RLS Alice"}'),
  ('bbbbbbbb-2222-2222-2222-222222222222','rls-bob@nexus.test','{"full_name":"RLS Bob"}'),
  ('cccccccc-3333-3333-3333-333333333333','rls-carol@nexus.test','{"full_name":"RLS Carol"}')
on conflict (id) do nothing;

grant select, insert, update, delete on all tables in schema public to authenticated;

\echo '== signup trigger created one personal workspace + owner membership each =='
select p.display_name, w.name as workspace, wm.role
from public.profiles p
join public.workspaces w on w.owner_id = p.id
join public.workspace_members wm on wm.workspace_id = w.id and wm.user_id = p.id
order by p.display_name;

\echo ''
\echo '== Alice invites Carol into her workspace as viewer =='
do $$
declare alice_ws uuid;
begin
  select id into alice_ws from public.workspaces where owner_id = 'aaaaaaaa-1111-1111-1111-111111111111';
  insert into public.workspace_members (workspace_id, user_id, role)
  values (alice_ws, 'cccccccc-3333-3333-3333-333333333333', 'viewer')
  on conflict (workspace_id, user_id) do nothing;
end $$;

do $$
declare alice_ws uuid;
begin
  select id into alice_ws from public.workspaces where owner_id = 'aaaaaaaa-1111-1111-1111-111111111111';
  if not exists (select 1 from public.notes where workspace_id=alice_ws and title='Secrets policy') then
    insert into public.notes (workspace_id, created_by, title, content, status, embedding_model)
    values (alice_ws, 'aaaaaaaa-1111-1111-1111-111111111111',
            'Secrets policy', 'Service role key lives in Fly secrets only.', 'ready', 'text-embedding-3-small');
  end if;
  if not exists (select 1 from public.documents where workspace_id=alice_ws and title='thesis.pdf') then
    insert into public.documents (workspace_id, uploaded_by, title, filename, file_path, mime_type, status, embedding_model, raw_text)
    values (alice_ws, 'aaaaaaaa-1111-1111-1111-111111111111',
            'thesis.pdf', 'thesis.pdf', alice_ws || '/d1/thesis.pdf', 'application/pdf', 'ready',
            'text-embedding-3-small', 'Fly secrets are set with flyctl.');
  end if;
end $$;

\echo '== Bob (separate workspace) sees nothing of Alices =='
set role authenticated;
set request.jwt.claim.sub = 'bbbbbbbb-2222-2222-2222-222222222222';
select count(*) as bob_visible_notes from public.notes;
select count(*) as bob_visible_docs from public.documents;
select count(*) as bob_sees_alice_ws from public.workspaces
  where id = (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111');
reset role;

\echo '== Carol (viewer in Alices workspace) CAN read =='
set role authenticated;
set request.jwt.claim.sub = 'cccccccc-3333-3333-3333-333333333333';
select count(*) as carol_visible_notes from public.notes;
select count(*) as member_roster from public.workspace_members
  where workspace_id = (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111');
\echo '-- viewer must NOT be able to write'
do $$
declare alice_ws uuid; n int;
begin
  select id into alice_ws from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111';
  begin
    insert into public.notes (workspace_id, created_by, title, content, status, embedding_model)
    values (alice_ws, 'cccccccc-3333-3333-3333-333333333333','viewer sneaks in','x','ready','text-embedding-3-small');
    raise notice 'SECURITY FAILURE: viewer inserted a note';
  exception when others then
    raise notice 'correctly rejected viewer insert: %', sqlerrm;
  end;
  update public.notes set title='pwned' where workspace_id = alice_ws;
  get diagnostics n = row_count;
  raise notice 'viewer UPDATE affected % rows (expect 0)', n;
end $$;
\echo '-- viewer cannot add members either'
do $$
declare alice_ws uuid;
begin
  select id into alice_ws from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111';
  begin
    insert into public.workspace_members (workspace_id, user_id, role)
    values (alice_ws, 'bbbbbbbb-2222-2222-2222-222222222222','member');
    raise notice 'SECURITY FAILURE: viewer added a member';
  exception when others then
    raise notice 'correctly rejected viewer member insert: %', sqlerrm;
  end;
end $$;
reset role;

\echo ''
\echo '== XOR constraint: a chunk must have exactly one parent =='
do $$
declare ws uuid;
begin
  select id into ws from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111';
  begin
    insert into public.document_chunks (workspace_id, chunk_index, content, embedding_model)
    values (ws, 0, 'orphan chunk', 'text-embedding-3-small');
    raise notice 'SECURITY FAILURE: chunk with no parent accepted';
  exception when check_violation then
    raise notice 'correctly rejected parentless chunk';
  end;
  begin
    insert into public.document_chunks (workspace_id, note_id, document_id, chunk_index, content, embedding_model)
    values (ws,
            (select id from public.notes where workspace_id=ws limit 1),
            (select id from public.documents where workspace_id=ws limit 1),
            0,'two parents','text-embedding-3-small');
    raise notice 'SECURITY FAILURE: chunk with two parents accepted';
  exception when check_violation then
    raise notice 'correctly rejected two-parent chunk';
  end;
end $$;

\echo ''
\echo '== hybrid_search is workspace-scoped and reports source_kind =='
do $$
declare ws uuid;
begin
  select id into ws from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111';
  insert into public.document_chunks (workspace_id, document_id, chunk_index, content, embedding_model)
  values (ws, (select id from public.documents where workspace_id=ws limit 1), 90,
          'Fly secrets are set with flyctl secrets set', 'text-embedding-3-small')
  on conflict do nothing;
  insert into public.document_chunks (workspace_id, note_id, chunk_index, content, embedding_model)
  values (ws, (select id from public.notes where workspace_id=ws limit 1), 90,
          'The service role key lives in Fly secrets only', 'text-embedding-3-small')
  on conflict do nothing;
end $$;

select source_kind, source_title, left(content, 42) as snippet,
       keyword_rank, round(fused_score::numeric,5) as fused
from public.hybrid_search(
  p_workspace_id => (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111'),
  p_query => 'fly secrets'
);

\echo ''
\echo '== same search from Bobs workspace returns nothing (tenant filter) =='
select count(*) as bob_results
from public.hybrid_search(
  p_workspace_id => (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
  p_query => 'fly secrets'
);

\echo ''
\echo '== workspace-consistency constraints: cross-tenant rows are refused by the DATABASE =='
\echo '   (these hold even for a service-role connection, where RLS does not apply)'
\set ON_ERROR_STOP 0

\echo '-- expected: ERROR foreign key — chunk in ws A pointing at a note in ws B'
begin;
insert into public.document_chunks (workspace_id, note_id, chunk_index, content, embedding_model)
values (
  (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
  (select id from public.notes where workspace_id =
     (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111') limit 1),
  991, 'smuggled chunk', 'text-embedding-3-small');
rollback;

\echo '-- expected: ERROR foreign key — note in A tagged with a tag from B'
begin;
with tag_b as (
  insert into public.tags (workspace_id, name)
  values ((select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
          'bob-workspace-tag')
  on conflict do nothing returning id)
insert into public.note_tags (note_id, tag_id, workspace_id)
values (
  (select id from public.notes where workspace_id =
     (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111') limit 1),
  (select id from tag_b),
  (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111'));
rollback;

\echo '-- expected: ERROR foreign key — knowledge_link across two workspaces'
begin;
with a as (
  insert into public.knowledge_entities (workspace_id, name, normalized_name)
  values ((select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111'),
          'RLS Alpha', 'rls alpha')
  on conflict (workspace_id, normalized_name) do update set name = excluded.name
  returning id),
b as (
  insert into public.knowledge_entities (workspace_id, name, normalized_name)
  values ((select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
          'RLS Beta', 'rls beta')
  on conflict (workspace_id, normalized_name) do update set name = excluded.name
  returning id)
insert into public.knowledge_links (workspace_id, source_id, target_id, relation)
select (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111'),
       (select id from a), (select id from b), 'smuggled';
rollback;

\echo '-- expected: ERROR foreign key — citation joining a message in A to a chunk in B'
begin;
-- Give Bob a real note and chunk first, so the only thing wrong with the
-- citation below is the workspace mismatch. (A NULL or parentless chunk would
-- "fail" for the wrong reason and prove nothing.)
insert into public.notes (workspace_id, created_by, title, content, status, embedding_model)
select (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
       'bbbbbbbb-2222-2222-2222-222222222222', 'Bob citation target',
       'Bob text', 'ready', 'text-embedding-3-small'
where not exists (
  select 1 from public.notes where title = 'Bob citation target');

insert into public.document_chunks (workspace_id, note_id, chunk_index, content, embedding_model)
select (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
       (select id from public.notes where workspace_id =
          (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222') limit 1),
       980, 'Bob chunk used as a cross-tenant citation target', 'text-embedding-3-small'
where not exists (
  select 1 from public.document_chunks where chunk_index = 980 and content like 'Bob chunk%');

with c as (
  insert into public.conversations (workspace_id, created_by, title)
  values ((select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111'),
          'aaaaaaaa-1111-1111-1111-111111111111', 'smuggle attempt')
  returning id),
m as (
  insert into public.chat_messages (workspace_id, conversation_id, role, content)
  select (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111'),
         id, 'assistant', 'x' from c returning id)
insert into public.message_sources
  (workspace_id, message_id, chunk_id, source_kind, note_id, source_title, marker)
select (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222'),
       m.id,
       (select id from public.document_chunks where workspace_id =
          (select id from public.workspaces where owner_id='bbbbbbbb-2222-2222-2222-222222222222') limit 1),
       'note',
       (select id from public.notes where workspace_id =
          (select id from public.workspaces where owner_id='aaaaaaaa-1111-1111-1111-111111111111') limit 1),
       'smuggled', 1
from m;
rollback;

\set ON_ERROR_STOP 1

\echo ''
\echo '   All four statements above MUST have errored. A row that inserted'
\echo '   successfully is a missing composite foreign key.'
