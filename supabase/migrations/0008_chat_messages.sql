-- 0008_chat_messages.sql
-- Persistent chat transcript. Pairs with chat_sessions: when a user opens
-- an old sidebar entry whose Redis STM has expired, we can still hydrate
-- the message log from Postgres. Temp / incognito chats are NEVER written
-- here (they have no chat_sessions row either).
--
-- One row per turn (role='user' or 'assistant'). ``sources`` is the
-- assistant turn's serialized source list; ``meta`` carries the path /
-- verification / tool-call info that already lives on the wire response.

create table if not exists public.chat_messages (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null
        references public.chat_sessions(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    sources jsonb,
    meta jsonb,
    created_at timestamptz not null default now()
);

create index if not exists chat_messages_session_idx
    on public.chat_messages(session_id, created_at);

create index if not exists chat_messages_user_idx
    on public.chat_messages(user_id, created_at desc);

-- RLS — owner-only (same pattern as chat_sessions).
alter table public.chat_messages enable row level security;

drop policy if exists chat_messages_owner_select on public.chat_messages;
create policy chat_messages_owner_select on public.chat_messages
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists chat_messages_owner_insert on public.chat_messages;
create policy chat_messages_owner_insert on public.chat_messages
    for insert to authenticated
    with check (
        user_id = auth.uid()
        and exists (
            select 1 from public.chat_sessions s
            where s.id = session_id and s.user_id = auth.uid()
        )
    );

drop policy if exists chat_messages_owner_update on public.chat_messages;
create policy chat_messages_owner_update on public.chat_messages
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists chat_messages_owner_delete on public.chat_messages;
create policy chat_messages_owner_delete on public.chat_messages
    for delete to authenticated
    using (user_id = auth.uid());
