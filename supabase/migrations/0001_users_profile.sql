-- Wellsynthai PR-5: users_profile baseline
-- Run via Supabase SQL editor (or `supabase db push` once the CLI is wired).

-- 1. Locked role enum (matches frontend FRONTEND_SPEC + ROLES_AND_DASHBOARDS).
do $$
begin
  if not exists (select 1 from pg_type where typname = 'user_role') then
    create type public.user_role as enum (
      'super_admin',
      'drilling_engineer',
      'geologist',
      'asset_manager',
      'qa_steward'
    );
  end if;
end$$;

-- 2. users_profile: 1-1 with auth.users; holds role + display name.
create table if not exists public.users_profile (
  id            uuid primary key references auth.users(id) on delete cascade,
  email         text not null,
  display_name  text,
  role          public.user_role not null default 'drilling_engineer',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- 3. updated_at maintenance.
create or replace function public.tg_set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end$$;

drop trigger if exists set_updated_at on public.users_profile;
create trigger set_updated_at
  before update on public.users_profile
  for each row execute function public.tg_set_updated_at();

-- 4. Auto-create a profile row when a new auth.users row appears.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.users_profile (id, email, display_name)
  values (new.id, new.email, coalesce(new.raw_user_meta_data->>'display_name', new.email));
  return new;
end$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 5. RLS: user reads/updates own row; super_admin reads all.
alter table public.users_profile enable row level security;

drop policy if exists "users_profile_self_select" on public.users_profile;
create policy "users_profile_self_select"
  on public.users_profile for select
  using (auth.uid() = id);

drop policy if exists "users_profile_admin_select" on public.users_profile;
create policy "users_profile_admin_select"
  on public.users_profile for select
  using (
    exists (
      select 1 from public.users_profile p
      where p.id = auth.uid() and p.role = 'super_admin'
    )
  );

drop policy if exists "users_profile_self_update" on public.users_profile;
create policy "users_profile_self_update"
  on public.users_profile for update
  using (auth.uid() = id)
  with check (
    -- non-admins cannot change their own role
    auth.uid() = id
    and (
      role = (select role from public.users_profile where id = auth.uid())
      or exists (
        select 1 from public.users_profile p
        where p.id = auth.uid() and p.role = 'super_admin'
      )
    )
  );

-- Service-role key bypasses RLS automatically; backend writes use it.
