-- PR-6: Per-tenant artifact storage.
--
-- Three private buckets back the ingestion pipeline. Path scheme inside
-- every bucket:
--
--     <owner_id>/<doc_id>/<filename>
--
-- The first segment is auth.uid() and is what the RLS policies pin
-- against. The backend always writes via the service-role key (which
-- bypasses RLS), and serves reads to authenticated users via short-TTL
-- signed URLs. RLS below is defense-in-depth for any direct
-- authenticated client access.

insert into storage.buckets (id, name, public)
values
  ('pdfs',      'pdfs',      false),
  ('pages',     'pages',     false),
  ('artifacts', 'artifacts', false)
on conflict (id) do nothing;

-- Drop any prior policies with the same names so this migration is idempotent.
drop policy if exists "wellsynthai_owner_select" on storage.objects;
drop policy if exists "wellsynthai_owner_insert" on storage.objects;
drop policy if exists "wellsynthai_owner_update" on storage.objects;
drop policy if exists "wellsynthai_owner_delete" on storage.objects;
drop policy if exists "wellsynthai_super_admin_all" on storage.objects;

-- Owner can read their own objects (path's first segment == auth.uid()).
create policy "wellsynthai_owner_select"
on storage.objects for select to authenticated
using (
  bucket_id in ('pdfs', 'pages', 'artifacts')
  and (storage.foldername(name))[1] = auth.uid()::text
);

-- Owner can insert into their own prefix.
create policy "wellsynthai_owner_insert"
on storage.objects for insert to authenticated
with check (
  bucket_id in ('pdfs', 'pages', 'artifacts')
  and (storage.foldername(name))[1] = auth.uid()::text
);

-- Owner can overwrite their own objects.
create policy "wellsynthai_owner_update"
on storage.objects for update to authenticated
using (
  bucket_id in ('pdfs', 'pages', 'artifacts')
  and (storage.foldername(name))[1] = auth.uid()::text
)
with check (
  bucket_id in ('pdfs', 'pages', 'artifacts')
  and (storage.foldername(name))[1] = auth.uid()::text
);

-- Owner can delete their own objects.
create policy "wellsynthai_owner_delete"
on storage.objects for delete to authenticated
using (
  bucket_id in ('pdfs', 'pages', 'artifacts')
  and (storage.foldername(name))[1] = auth.uid()::text
);

-- Note: super-admin scope-all is intentionally NOT modelled as an RLS
-- policy. Backend admin operations use the Supabase service-role key,
-- which bypasses RLS entirely; an RLS policy that subqueries
-- public.users_profile from the storage role's context errors out with
-- "database schema is invalid or incompatible". If we ever need
-- direct super-admin downloads from a user-context client, wrap the
-- role check in a SECURITY DEFINER function and reference that.
