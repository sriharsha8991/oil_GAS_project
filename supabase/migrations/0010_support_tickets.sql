-- 0010_support_tickets.sql
-- In-app help/support tickets. Users submit a ticket via POST /support/tickets
-- (with optional attachments + diagnostics blob); the BE persists it, mails the
-- support inbox, and exposes per-user history. Super-admins triage via
-- /admin/support/* (Slice 2). Soft-delete via deleted_at.
--
-- Attachments live in the existing `artifacts` bucket under
--   <ticket_user_id>/support/<ticket_id>/<filename>
-- so the bucket's path[1]==auth.uid() RLS already scopes user reads. Backend
-- writes via service-role; user fetches go through a signed-URL redirect from
-- /support/tickets/{id}/attachments/{aid} (mirrors documents page-image route).

create table if not exists public.support_tickets (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    category text not null check (category in ('bug', 'feature', 'account', 'billing', 'other')),
    subject text not null check (char_length(subject) between 1 and 120),
    message text not null check (char_length(message) between 1 and 4000),
    status text not null default 'open'
        check (status in ('open', 'in_progress', 'resolved', 'closed')),
    diagnostics jsonb,
    email_sent boolean not null default false,
    email_message_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);

create index if not exists support_tickets_user_created_idx
    on public.support_tickets(user_id, created_at desc)
    where deleted_at is null;

create index if not exists support_tickets_status_created_idx
    on public.support_tickets(status, created_at desc)
    where deleted_at is null;

create index if not exists support_tickets_category_idx
    on public.support_tickets(category)
    where deleted_at is null;

drop trigger if exists set_updated_at on public.support_tickets;
create trigger set_updated_at
    before update on public.support_tickets
    for each row execute function public.tg_set_updated_at();


create table if not exists public.support_ticket_attachments (
    id uuid primary key default gen_random_uuid(),
    ticket_id uuid not null
        references public.support_tickets(id) on delete cascade,
    storage_key text not null,        -- path inside `artifacts` bucket
    filename text not null,
    mime_type text not null,
    size_bytes integer not null check (size_bytes >= 0),
    created_at timestamptz not null default now()
);

create index if not exists support_ticket_attachments_ticket_idx
    on public.support_ticket_attachments(ticket_id);


create table if not exists public.support_ticket_notes (
    id uuid primary key default gen_random_uuid(),
    ticket_id uuid not null
        references public.support_tickets(id) on delete cascade,
    author_user_id uuid not null references auth.users(id) on delete cascade,
    text text not null check (char_length(text) between 1 and 4000),
    created_at timestamptz not null default now()
);

create index if not exists support_ticket_notes_ticket_created_idx
    on public.support_ticket_notes(ticket_id, created_at);


-- RLS: owner-only reads. Inserts come from the BE via service-role
-- (bypasses RLS), so no insert policy is needed for end users. Super-admin
-- triage endpoints also run as service-role; super-admin gating is enforced
-- in the application layer (require_role("super_admin")) — not in RLS — so
-- that the same JWT-scoped queries used for normal users cannot accidentally
-- escalate.
alter table public.support_tickets enable row level security;

drop policy if exists support_tickets_owner_select on public.support_tickets;
create policy support_tickets_owner_select on public.support_tickets
    for select to authenticated
    using (user_id = auth.uid() and deleted_at is null);

alter table public.support_ticket_attachments enable row level security;

drop policy if exists support_ticket_attachments_owner_select
    on public.support_ticket_attachments;
create policy support_ticket_attachments_owner_select
    on public.support_ticket_attachments
    for select to authenticated
    using (
        exists (
            select 1 from public.support_tickets t
            where t.id = ticket_id
              and t.user_id = auth.uid()
              and t.deleted_at is null
        )
    );

alter table public.support_ticket_notes enable row level security;

drop policy if exists support_ticket_notes_owner_select on public.support_ticket_notes;
create policy support_ticket_notes_owner_select on public.support_ticket_notes
    for select to authenticated
    using (
        exists (
            select 1 from public.support_tickets t
            where t.id = ticket_id
              and t.user_id = auth.uid()
              and t.deleted_at is null
        )
    );
