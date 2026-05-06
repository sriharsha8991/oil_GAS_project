-- 0003_projects_documents_jobs.sql
-- PR-7 — multi-tenant scoping. Adds:
--   * organization field on users_profile (snapshotted into Qdrant payloads)
--   * projects table  (user-created groupings)
--   * documents table (durable doc identity, scoped to a project)
--   * jobs table      (per-ingestion-run state; Realtime fan-out source)

------------------------------------------------------------
-- users_profile.organization
------------------------------------------------------------

alter table public.users_profile
    add column if not exists organization text;

------------------------------------------------------------
-- projects
------------------------------------------------------------

create table if not exists public.projects (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    name text not null,
    description text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists projects_user_id_idx
    on public.projects(user_id);

alter table public.projects enable row level security;

drop policy if exists projects_owner_select on public.projects;
create policy projects_owner_select on public.projects
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists projects_owner_insert on public.projects;
create policy projects_owner_insert on public.projects
    for insert to authenticated
    with check (user_id = auth.uid());

drop policy if exists projects_owner_update on public.projects;
create policy projects_owner_update on public.projects
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists projects_owner_delete on public.projects;
create policy projects_owner_delete on public.projects
    for delete to authenticated
    using (user_id = auth.uid());

------------------------------------------------------------
-- documents
------------------------------------------------------------

create table if not exists public.documents (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    project_id uuid not null references public.projects(id) on delete cascade,
    filename text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists documents_user_id_idx
    on public.documents(user_id);
create index if not exists documents_project_id_idx
    on public.documents(project_id);

alter table public.documents enable row level security;

drop policy if exists documents_owner_select on public.documents;
create policy documents_owner_select on public.documents
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists documents_owner_insert on public.documents;
create policy documents_owner_insert on public.documents
    for insert to authenticated
    with check (user_id = auth.uid());

drop policy if exists documents_owner_update on public.documents;
create policy documents_owner_update on public.documents
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists documents_owner_delete on public.documents;
create policy documents_owner_delete on public.documents
    for delete to authenticated
    using (user_id = auth.uid());

------------------------------------------------------------
-- jobs (per-ingestion run; FK -> documents)
------------------------------------------------------------

create table if not exists public.jobs (
    id uuid primary key,                     -- celery task id
    document_id uuid not null references public.documents(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    status text not null default 'queued',   -- queued | running | succeeded | failed
    current_phase text,
    pages_total int,
    pages_done int,
    error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    ended_at timestamptz
);

create index if not exists jobs_document_id_idx on public.jobs(document_id);
create index if not exists jobs_user_id_idx on public.jobs(user_id);
create index if not exists jobs_status_idx on public.jobs(status);

alter table public.jobs enable row level security;

drop policy if exists jobs_owner_select on public.jobs;
create policy jobs_owner_select on public.jobs
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists jobs_owner_insert on public.jobs;
create policy jobs_owner_insert on public.jobs
    for insert to authenticated
    with check (user_id = auth.uid());

drop policy if exists jobs_owner_update on public.jobs;
create policy jobs_owner_update on public.jobs
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

------------------------------------------------------------
-- Realtime publication: emit row changes on jobs so the
-- frontend can subscribe instead of polling.
------------------------------------------------------------

do $$
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        begin
            alter publication supabase_realtime add table public.jobs;
        exception when duplicate_object then
            null;  -- already in publication
        end;
    end if;
end $$;
