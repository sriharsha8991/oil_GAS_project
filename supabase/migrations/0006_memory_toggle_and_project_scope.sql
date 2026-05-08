-- 0006_memory_toggle_and_project_scope.sql
-- Per-user toggle for cross-session memory + project-scoped memories.
-- See user request: "give user an option whether to use cross session
-- memory or not (single toggle)" + "clean separation of project-wise
-- memory". Scoping rule: strict isolation — project-scoped memories
-- are surfaced ONLY when chatting in that project; global memories
-- (project_id NULL) are surfaced ONLY when chatting outside any project.

-- 1. Memory toggle on the profile row.
alter table public.users_profile
    add column if not exists memory_enabled boolean not null default true;

-- 2. Project scope on user_memories. NULL == global / personal scope.
alter table public.user_memories
    add column if not exists project_id uuid
        references public.projects(id) on delete cascade;

-- 3. Replace the (user_id, scope, key) PK with one that includes project_id.
--    NULLS NOT DISTINCT (Postgres 15+) treats two NULLs as equal so the
--    "global" tier still has uniqueness on (user_id, scope, key).
alter table public.user_memories
    drop constraint if exists user_memories_pkey;

-- A surrogate primary key keeps FKs / RLS unchanged while we use a
-- compound unique index for the actual upsert target.
alter table public.user_memories
    add column if not exists id uuid not null default gen_random_uuid();

alter table public.user_memories
    add constraint user_memories_pkey primary key (id);

drop index if exists user_memories_unique_scope_key;
create unique index user_memories_unique_scope_key
    on public.user_memories(user_id, project_id, scope, key)
    nulls not distinct;

-- 4. Lookup index so project-scoped recalls stay cheap.
create index if not exists user_memories_user_project_idx
    on public.user_memories(user_id, project_id);

-- 5. RLS: project-scoped rows must also belong to a project the user owns.
--    The existing owner-only policies on user_id already prevent cross-user
--    leakage; we tighten with an extra check on project ownership for INSERT
--    and UPDATE so a user cannot tag a memory with another tenant's project.

drop policy if exists user_memories_owner_insert on public.user_memories;
create policy user_memories_owner_insert on public.user_memories
    for insert to authenticated
    with check (
        user_id = auth.uid()
        and (
            project_id is null
            or exists (
                select 1 from public.projects p
                where p.id = project_id and p.user_id = auth.uid()
            )
        )
    );

drop policy if exists user_memories_owner_update on public.user_memories;
create policy user_memories_owner_update on public.user_memories
    for update to authenticated
    using (user_id = auth.uid())
    with check (
        user_id = auth.uid()
        and (
            project_id is null
            or exists (
                select 1 from public.projects p
                where p.id = project_id and p.user_id = auth.uid()
            )
        )
    );
