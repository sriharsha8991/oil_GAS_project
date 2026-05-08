-- 0007_chat_sessions.sql
-- ChatGPT-style persisted chat sessions. Replaces the Redis-only session
-- model for *listing* purposes — STM (rolling messages, summary, subject)
-- still lives in Redis db2 with its TTL. This table is the durable index
-- the user sees in their sidebar: id, title, project scope, timestamps.
--
-- Temp / incognito chats (ChatRequest.temp=true) are NEVER inserted here.
--
-- Strict per-user isolation via RLS; project_id is optional (NULL = the
-- catch-all "Personal" bucket, mirrors the user_memories scoping rule).

create table if not exists public.chat_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    project_id uuid references public.projects(id) on delete cascade,
    title text not null default 'New chat',
    -- True until the auto-titler has named the conversation. The FE can
    -- keep showing a placeholder while this is true, then refresh.
    title_pending boolean not null default true,
    message_count integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    -- Sort key for the sidebar: most recently active session first.
    last_message_at timestamptz not null default now(),
    archived_at timestamptz
);

create index if not exists chat_sessions_user_recent_idx
    on public.chat_sessions(user_id, last_message_at desc);

create index if not exists chat_sessions_user_project_idx
    on public.chat_sessions(user_id, project_id);

-- RLS — owner-only.
alter table public.chat_sessions enable row level security;

drop policy if exists chat_sessions_owner_select on public.chat_sessions;
create policy chat_sessions_owner_select on public.chat_sessions
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists chat_sessions_owner_insert on public.chat_sessions;
create policy chat_sessions_owner_insert on public.chat_sessions
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

drop policy if exists chat_sessions_owner_update on public.chat_sessions;
create policy chat_sessions_owner_update on public.chat_sessions
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists chat_sessions_owner_delete on public.chat_sessions;
create policy chat_sessions_owner_delete on public.chat_sessions
    for delete to authenticated
    using (user_id = auth.uid());
