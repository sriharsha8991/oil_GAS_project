-- 0005_user_memories.sql
-- Long-term per-user memory: passive captures from chat turns + privacy controls.
-- See wiki/11-memory-and-chatgpt-roadmap.md §2.3.

create table if not exists public.user_memories (
    user_id      uuid not null references auth.users(id) on delete cascade,
    scope        text not null check (scope in (
                     'preference', 'fact', 'project_pin', 'style', 'dismissed'
                 )),
    key          text not null,
    value        jsonb not null,
    source_msg   text,
    confidence   real not null default 0.5
                     check (confidence >= 0 and confidence <= 1),
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    primary key (user_id, scope, key)
);

create index if not exists user_memories_user_scope_idx
    on public.user_memories(user_id, scope);

create index if not exists user_memories_user_updated_idx
    on public.user_memories(user_id, updated_at desc);

alter table public.user_memories enable row level security;

drop policy if exists user_memories_owner_select on public.user_memories;
create policy user_memories_owner_select on public.user_memories
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists user_memories_owner_insert on public.user_memories;
create policy user_memories_owner_insert on public.user_memories
    for insert to authenticated
    with check (user_id = auth.uid());

drop policy if exists user_memories_owner_update on public.user_memories;
create policy user_memories_owner_update on public.user_memories
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists user_memories_owner_delete on public.user_memories;
create policy user_memories_owner_delete on public.user_memories
    for delete to authenticated
    using (user_id = auth.uid());
