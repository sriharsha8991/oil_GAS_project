-- Wellsynthai: invite-only auth model with org-scoped admins.
--
-- Adds:
--   * 'admin' value to public.user_role enum
--   * users_profile.user_quota   (max users this admin may invite; 0 = unlimited)
--   * users_profile.invited_by   (auth.users.id of the inviter; null for super_admin / pre-existing)
--
-- Idempotent.

-- 1. Add 'admin' to the user_role enum if missing.
do $$
begin
  if not exists (
    select 1 from pg_enum e
      join pg_type t on t.oid = e.enumtypid
     where t.typname = 'user_role' and e.enumlabel = 'admin'
  ) then
    alter type public.user_role add value 'admin';
  end if;
end$$;

-- 2. Add user_quota + invited_by columns.
alter table public.users_profile
  add column if not exists user_quota integer not null default 0,
  add column if not exists invited_by uuid references auth.users(id) on delete set null;

-- 3. Helpful index for "users invited by X" lookups (quota counting).
create index if not exists users_profile_invited_by_idx
  on public.users_profile (invited_by);
