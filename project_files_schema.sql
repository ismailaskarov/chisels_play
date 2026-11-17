-- Run this once in Supabase SQL Editor after the existing projects table exists.

create table if not exists project_files (
    id text primary key,
    project_id text not null references projects(id) on delete cascade,
    filename text not null,
    storage_path text not null unique,
    content_type text not null,
    size_bytes bigint not null check (size_bytes > 0),
    sha256 text not null,
    primary_source boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (project_id, sha256)
);

create index if not exists project_files_project_id_idx
    on project_files (project_id, primary_source desc, created_at asc);

alter table project_files enable row level security;
grant select, insert, update, delete on table project_files to service_role;
revoke all on table project_files from anon, authenticated;

insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
values (
    'project-files',
    'project-files',
    false,
    20971520,
    array['application/pdf', 'image/jpeg', 'image/png']
)
on conflict (id) do update set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;
