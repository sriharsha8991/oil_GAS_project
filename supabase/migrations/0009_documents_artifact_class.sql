-- 0009 — L4 artifact class taxonomy on documents.
-- Adds the PRD §10 evidence-tier metadata to the documents table so the
-- retrieval reranker can weight by artifact class and so revisions /
-- supersession can be tracked without losing the underlying document_id.

alter table public.documents
    add column if not exists artifact_class text not null default 'other',
    add column if not exists revision int not null default 1,
    add column if not exists superseded_by uuid
        references public.documents(id) on delete set null,
    add column if not exists confidentiality text not null default 'internal';

-- Closed enum guard (mirrors app.domain.well_taxonomy.ARTIFACT_CLASSES + 'other').
alter table public.documents
    drop constraint if exists documents_artifact_class_chk;
alter table public.documents
    add constraint documents_artifact_class_chk
    check (artifact_class in (
        'master', 'program', 'offset', 'ddr', 'handover',
        'service', 'risk', 'action', 'learning', 'media', 'other'
    ));

alter table public.documents
    drop constraint if exists documents_confidentiality_chk;
alter table public.documents
    add constraint documents_confidentiality_chk
    check (confidentiality in ('public', 'internal', 'restricted', 'third_party'));

create index if not exists documents_artifact_class_idx
    on public.documents(artifact_class);
create index if not exists documents_superseded_by_idx
    on public.documents(superseded_by);
