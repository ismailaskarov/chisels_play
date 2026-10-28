create table if not exists catalog_items (
    id text primary key default gen_random_uuid()::text,
    name text not null,
    category text not null,
    unit text not null,
    price double precision not null check (price > 0),
    source_type text,
    source_url text,
    material_type text,
    component_type text,
    thickness_mm double precision,
    brand_model text,
    added_for_part text,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    supplier text,
    created_by text,
    approved boolean not null default false,
    approved_by text,
    approved_at timestamptz,
    notes text,
    keywords text,
    synonyms text,
    description text,
    allowed_use text,
    match_priority text,
    needs_review boolean default false
);

alter table catalog_items add column if not exists keywords text;
alter table catalog_items add column if not exists synonyms text;
alter table catalog_items add column if not exists description text;
alter table catalog_items add column if not exists allowed_use text;
alter table catalog_items add column if not exists match_priority text;
alter table catalog_items add column if not exists needs_review boolean default false;

create table if not exists match_feedback (
    source_key text primary key,
    source_name text,
    source_material text,
    component_type text,
    catalog_name text,
    catalog_category text,
    catalog_unit text,
    catalog_price double precision,
    ai_catalog_name text,
    times_chosen integer not null default 1,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table catalog_items enable row level security;
alter table match_feedback enable row level security;

grant select, insert, update, delete on table catalog_items to service_role;
grant select, insert, update, delete on table match_feedback to service_role;
revoke all on table catalog_items from anon, authenticated;
revoke all on table match_feedback from anon, authenticated;

create table if not exists pricing_settings (
    name text primary key,
    kind text not null check (kind in ('category', 'multiplier')),
    value double precision not null check (value > 0),
    updated_by text,
    updated_at timestamptz not null default now()
);

alter table pricing_settings enable row level security;
grant select, insert, update, delete on table pricing_settings to service_role;
revoke all on table pricing_settings from anon, authenticated;
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
