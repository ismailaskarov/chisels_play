create table if not exists projects (
    id text primary key,
    project_name text not null,
    client_name text,
    status text not null default 'Draft',
    furniture_name text,
    quote_number text,
    calculated_price double precision not null default 0,
    final_client_price double precision not null default 0,
    snapshot jsonb not null default '{}'::jsonb,
    archived boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists projects_updated_at_idx on projects (updated_at desc);
create index if not exists projects_status_idx on projects (status);
create index if not exists projects_archived_idx on projects (archived);

alter table projects enable row level security;
grant select, insert, update, delete on table projects to service_role;
revoke all on table projects from anon, authenticated;
