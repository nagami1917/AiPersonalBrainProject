-- ============================================================================
-- 0012 — note links (backlinks)
-- ============================================================================
-- Notes reference each other with [[wiki links]]. This table is the resolved
-- edge, not the text: extraction happens once at write time so a backlink panel
-- is an index lookup rather than a scan of every note body on every page load.
--
-- A link whose target does not exist yet is still stored, with a null
-- target_note_id. That is deliberate: an unresolved link is a promise the user
-- wrote ("I will write this"), and hiding it would make the backlink panel lie
-- about what the note connects to.
-- ============================================================================

create table if not exists public.note_links (
  id             uuid primary key default gen_random_uuid(),
  workspace_id   uuid not null references public.workspaces(id) on delete cascade,

  -- the note that contains the [[link]]
  source_note_id uuid not null references public.notes(id) on delete cascade,

  -- the note it points at, when a note with that title exists in the workspace
  target_note_id uuid references public.notes(id) on delete set null,

  -- The link text as the author typed it, so the panel can show "Q3 Budget"
  -- rather than "Q3 budget". Kept even when resolved: a later rename of the
  -- target must not silently rewrite what somebody else's note said.
  target_title   text not null check (char_length(btrim(target_title)) between 1 and 500),
  created_at     timestamptz not null default now()
);

-- Matching is case- and whitespace-insensitive, but *display* must preserve the
-- author's capitalisation. A generated column keeps the two from drifting: the
-- unique index constrains the normalised form, the UI reads the original.
alter table public.note_links
  add column if not exists target_title_normalized text
    generated always as (lower(btrim(target_title))) stored;

-- One edge per (source, target title): a note linking the same title twice is
-- still one relationship, and the panel should say so once.
create unique index if not exists uq_note_links_source_target
  on public.note_links (source_note_id, target_title_normalized);

create index if not exists idx_note_links_target on public.note_links (target_note_id);
create index if not exists idx_note_links_workspace on public.note_links (workspace_id);

-- A link must not point outside the workspace, and neither end may be dangling.
alter table public.note_links
  drop constraint if exists note_links_source_workspace_fkey,
  add constraint note_links_source_workspace_fkey
    foreign key (source_note_id, workspace_id) references public.notes (id, workspace_id)
    on delete cascade;

alter table public.note_links
  drop constraint if exists note_links_no_self_link,
  add constraint note_links_no_self_link check (target_note_id is null or target_note_id <> source_note_id);

comment on table public.note_links is
  'Resolved [[wiki links]] between notes. Derived from note content at save time; rebuilt on every save.';

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table public.note_links enable row level security;

drop policy if exists note_links_read on public.note_links;
create policy note_links_read on public.note_links
  for select using (public.is_workspace_member(workspace_id));

drop policy if exists note_links_write on public.note_links;
create policy note_links_write on public.note_links
  for all using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));

grant select, insert, update, delete on public.note_links to authenticated;
