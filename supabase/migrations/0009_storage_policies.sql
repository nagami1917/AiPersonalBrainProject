-- ============================================================================
-- 0009 — storage: private bucket + per-user path policy
-- ============================================================================
-- Layout: nexus-documents/{user_id}/{document_id}/{filename}
-- The bucket is private. Bytes move via short-lived signed URLs; the API never
-- proxies file content (ARCHITECTURE.md §4.1).
-- ============================================================================

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'nexus-documents',
  'nexus-documents',
  false,
  52428800, -- 50 MiB
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword',
    'text/plain',
    'text/markdown'
  ]
)
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- ---------------------------------------------------------------------------
-- Policies on storage.objects: the first path segment must be the caller's
-- user id. Signed URL generation by the API (service role) is unaffected.
-- ---------------------------------------------------------------------------
drop policy if exists nexus_objects_select_own on storage.objects;
create policy nexus_objects_select_own on storage.objects
  for select to authenticated
  using (
    bucket_id = 'nexus-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists nexus_objects_insert_own on storage.objects;
create policy nexus_objects_insert_own on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'nexus-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists nexus_objects_update_own on storage.objects;
create policy nexus_objects_update_own on storage.objects
  for update to authenticated
  using (
    bucket_id = 'nexus-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  )
  with check (
    bucket_id = 'nexus-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists nexus_objects_delete_own on storage.objects;
create policy nexus_objects_delete_own on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'nexus-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );
