-- =============================================================================
-- Pipeline sync schema: six mirror tables plus the pipeline_upsert RPC that
-- supabase_sync.py drains the local sync_outbox into.
--
-- OPERATOR NOTES (read before applying):
--
-- 1. Apply this migration BEFORE the sync service (supabase_sync.py) runs for
--    the first time. If the RPC is missing, PostgREST answers HTTP 404, the
--    sync classifies 404 as a non-retryable 4xx, and every due outbox row is
--    POISONED (sent_at set, last_error = 'HTTP404'). Poisoned rows are never
--    retried automatically.
--
-- 2. Retention and recovery of poisoned rows is an operator decision. The rows
--    stay in sync_outbox with their last_error for inspection; recovery, if
--    wanted, is a one-off UPDATE clearing sent_at and last_error for the
--    affected rows, after which the sync re-sends them.
--
-- 3. pipeline_documents is RESERVED: no pipeline code enqueues 'document'
--    events yet. The shape is a best guess and may change once a producer
--    exists. It never stores document bytes, only a bucket storage_path.
--
-- 4. Versioning contract: each enqueue carries the entity's FULL state at that
--    version, not a partial diff. The RPC applies a row only when its version
--    is strictly higher than the stored one; equal or lower versions are silent
--    no-ops (2xx), so replays and out-of-order batches are safe. Columns absent
--    from a later payload are written as NULL.
--
-- 5. All ids are text (not uuid): job, reply and status-event ids are not
--    guaranteed to be uuids, and a uuid column would turn one odd id into a
--    400 and a poisoned row.
--
-- 6. RLS is enabled on all six tables with NO policies: anon and authenticated
--    read nothing. service_role bypasses RLS. The REVOKE on the RPC is
--    essential: Supabase grants EXECUTE on new public functions to PUBLIC by
--    default, and a SECURITY DEFINER writer callable with the anon key would be
--    a public write endpoint.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists public.pipeline_jobs (
    id          text primary key,
    company     text,
    role        text,
    url         text,
    platform    text,
    state       text,
    queued_at   timestamptz not null default now(),
    version     bigint not null,
    synced_at   timestamptz not null default now()
);

create index if not exists pipeline_jobs_queued_at_idx
    on public.pipeline_jobs (queued_at desc);
create index if not exists pipeline_jobs_state_idx
    on public.pipeline_jobs (state);

create table if not exists public.pipeline_attempts (
    attempt_id  text primary key,
    job_id      text not null references public.pipeline_jobs (id),
    phase       text,
    status      text,
    state       text,
    started_at  timestamptz,
    finished_at timestamptz,
    detail      text,
    latency_s   double precision,
    dry_run     boolean,
    version     bigint not null,
    synced_at   timestamptz not null default now()
);

create index if not exists pipeline_attempts_job_started_idx
    on public.pipeline_attempts (job_id, started_at desc);
create index if not exists pipeline_attempts_started_at_idx
    on public.pipeline_attempts (started_at desc);

create table if not exists public.pipeline_submissions (
    id           text primary key,
    job_id       text not null references public.pipeline_jobs (id),
    attempt_id   text,
    submitted_at timestamptz,
    confirmed    boolean,
    version      bigint not null,
    synced_at    timestamptz not null default now()
);

create index if not exists pipeline_submissions_job_id_idx
    on public.pipeline_submissions (job_id);

-- Reserved: no producer yet. Never document bytes, only a bucket path.
create table if not exists public.pipeline_documents (
    id           text primary key,
    job_id       text,
    attempt_id   text,
    kind         text,
    filename     text,
    sha256       text,
    byte_size    bigint,
    storage_path text,
    created_at   timestamptz,
    version      bigint not null,
    synced_at    timestamptz not null default now()
);

create index if not exists pipeline_documents_job_id_idx
    on public.pipeline_documents (job_id);

create table if not exists public.pipeline_replies (
    id             text primary key,
    application_id text,
    job_id         text,
    message_id     text,
    thread_key     text,
    source         text,
    from_domain    text,
    received_at    timestamptz,
    kind           text,
    status_after   text,
    confidence     double precision,
    match_method   text,
    needs_review   boolean,
    subject        text,
    version        bigint not null,
    synced_at      timestamptz not null default now()
);

create index if not exists pipeline_replies_received_at_idx
    on public.pipeline_replies (received_at desc);
create index if not exists pipeline_replies_application_id_idx
    on public.pipeline_replies (application_id);
create index if not exists pipeline_replies_job_id_idx
    on public.pipeline_replies (job_id);
create index if not exists pipeline_replies_needs_review_idx
    on public.pipeline_replies (needs_review)
    where needs_review;

create table if not exists public.pipeline_status_events (
    id             text primary key,
    application_id text,
    status         text,
    reply_id       text references public.pipeline_replies (id),
    "at"           timestamptz,
    actor          text,
    version        bigint not null,
    synced_at      timestamptz not null default now()
);

create index if not exists pipeline_status_events_application_at_idx
    on public.pipeline_status_events (application_id, "at" desc);

-- ---------------------------------------------------------------------------
-- Row Level Security: enabled, no policies (service_role bypasses RLS)
-- ---------------------------------------------------------------------------

alter table public.pipeline_jobs          enable row level security;
alter table public.pipeline_attempts      enable row level security;
alter table public.pipeline_submissions   enable row level security;
alter table public.pipeline_documents     enable row level security;
alter table public.pipeline_replies       enable row level security;
alter table public.pipeline_status_events enable row level security;

-- ---------------------------------------------------------------------------
-- RPC: public.pipeline_upsert(target text, rows jsonb) returns jsonb
--
-- Parameter names are part of the wire contract: PostgREST binds the JSON body
-- keys "target" and "rows" to them. Called as
--   POST /rest/v1/rpc/pipeline_upsert  {"target": "<table>", "rows": [...]}
--
-- Behaviour:
--   * target must be one of the six table names and rows must be a JSON array,
--     otherwise RAISE with SQLSTATE 22023 (PostgREST -> 400 -> sync poisons).
--   * Within a batch, duplicates of the same key are collapsed to the highest
--     version so ON CONFLICT never sees the same row twice.
--   * Rows with a NULL key or NULL version are skipped.
--   * INSERT ... ON CONFLICT DO UPDATE applies only when the stored version is
--     strictly lower than the incoming one. queued_at is never updated.
--   * synced_at is always now() on insert and on applied update.
--   * FK violations (23503) and type errors (22007 etc.) are NOT caught here:
--     23503 surfaces as PostgREST 409 (sync retries), type errors as 400
--     (sync poisons just that row via its one-at-a-time resend).
--   * Returns {"received": <rows in batch>, "applied": <rows inserted/updated>}.
-- ---------------------------------------------------------------------------

create or replace function public.pipeline_upsert(target text, rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    batch    jsonb := rows;
    received integer := 0;
    applied  integer := 0;
begin
    if target is null or target not in (
        'pipeline_jobs',
        'pipeline_attempts',
        'pipeline_submissions',
        'pipeline_documents',
        'pipeline_replies',
        'pipeline_status_events'
    ) then
        raise exception 'pipeline_upsert: unknown target %', coalesce(target, '<null>')
            using errcode = '22023';
    end if;

    if batch is null or jsonb_typeof(batch) <> 'array' then
        raise exception 'pipeline_upsert: rows must be a JSON array'
            using errcode = '22023';
    end if;

    received := jsonb_array_length(batch);

    if target = 'pipeline_jobs' then
        with deduped as (
            select distinct on (r.id) r.*
            from jsonb_populate_recordset(null::public.pipeline_jobs, batch) as r
            where r.id is not null and r.version is not null
            order by r.id, r.version desc
        )
        insert into public.pipeline_jobs as t
            (id, company, role, url, platform, state, queued_at, version, synced_at)
        select r.id, r.company, r.role, r.url, r.platform, r.state,
               coalesce(r.queued_at, now()), r.version, now()
        from deduped as r
        on conflict (id) do update set
            company   = excluded.company,
            role      = excluded.role,
            url       = excluded.url,
            platform  = excluded.platform,
            state     = excluded.state,
            version   = excluded.version,
            synced_at = now()
        where t.version < excluded.version;
        get diagnostics applied = row_count;

    elsif target = 'pipeline_attempts' then
        with deduped as (
            select distinct on (r.attempt_id) r.*
            from jsonb_populate_recordset(null::public.pipeline_attempts, batch) as r
            where r.attempt_id is not null and r.version is not null
            order by r.attempt_id, r.version desc
        )
        insert into public.pipeline_attempts as t
            (attempt_id, job_id, phase, status, state, started_at, finished_at,
             detail, latency_s, dry_run, version, synced_at)
        select r.attempt_id, r.job_id, r.phase, r.status, r.state, r.started_at, r.finished_at,
               r.detail, r.latency_s, r.dry_run, r.version, now()
        from deduped as r
        on conflict (attempt_id) do update set
            job_id      = excluded.job_id,
            phase       = excluded.phase,
            status      = excluded.status,
            state       = excluded.state,
            started_at  = excluded.started_at,
            finished_at = excluded.finished_at,
            detail      = excluded.detail,
            latency_s   = excluded.latency_s,
            dry_run     = excluded.dry_run,
            version     = excluded.version,
            synced_at   = now()
        where t.version < excluded.version;
        get diagnostics applied = row_count;

    elsif target = 'pipeline_submissions' then
        with deduped as (
            select distinct on (r.id) r.*
            from jsonb_populate_recordset(null::public.pipeline_submissions, batch) as r
            where r.id is not null and r.version is not null
            order by r.id, r.version desc
        )
        insert into public.pipeline_submissions as t
            (id, job_id, attempt_id, submitted_at, confirmed, version, synced_at)
        select r.id, r.job_id, r.attempt_id, r.submitted_at, r.confirmed, r.version, now()
        from deduped as r
        on conflict (id) do update set
            job_id       = excluded.job_id,
            attempt_id   = excluded.attempt_id,
            submitted_at = excluded.submitted_at,
            confirmed    = excluded.confirmed,
            version      = excluded.version,
            synced_at    = now()
        where t.version < excluded.version;
        get diagnostics applied = row_count;

    elsif target = 'pipeline_documents' then
        with deduped as (
            select distinct on (r.id) r.*
            from jsonb_populate_recordset(null::public.pipeline_documents, batch) as r
            where r.id is not null and r.version is not null
            order by r.id, r.version desc
        )
        insert into public.pipeline_documents as t
            (id, job_id, attempt_id, kind, filename, sha256, byte_size, storage_path,
             created_at, version, synced_at)
        select r.id, r.job_id, r.attempt_id, r.kind, r.filename, r.sha256, r.byte_size,
               r.storage_path, r.created_at, r.version, now()
        from deduped as r
        on conflict (id) do update set
            job_id       = excluded.job_id,
            attempt_id   = excluded.attempt_id,
            kind         = excluded.kind,
            filename     = excluded.filename,
            sha256       = excluded.sha256,
            byte_size    = excluded.byte_size,
            storage_path = excluded.storage_path,
            created_at   = excluded.created_at,
            version      = excluded.version,
            synced_at    = now()
        where t.version < excluded.version;
        get diagnostics applied = row_count;

    elsif target = 'pipeline_replies' then
        with deduped as (
            select distinct on (r.id) r.*
            from jsonb_populate_recordset(null::public.pipeline_replies, batch) as r
            where r.id is not null and r.version is not null
            order by r.id, r.version desc
        )
        insert into public.pipeline_replies as t
            (id, application_id, job_id, message_id, thread_key, source, from_domain,
             received_at, kind, status_after, confidence, match_method, needs_review,
             subject, version, synced_at)
        select r.id, r.application_id, r.job_id, r.message_id, r.thread_key, r.source,
               r.from_domain, r.received_at, r.kind, r.status_after, r.confidence,
               r.match_method, r.needs_review, r.subject, r.version, now()
        from deduped as r
        on conflict (id) do update set
            application_id = excluded.application_id,
            job_id         = excluded.job_id,
            message_id     = excluded.message_id,
            thread_key     = excluded.thread_key,
            source         = excluded.source,
            from_domain    = excluded.from_domain,
            received_at    = excluded.received_at,
            kind           = excluded.kind,
            status_after   = excluded.status_after,
            confidence     = excluded.confidence,
            match_method   = excluded.match_method,
            needs_review   = excluded.needs_review,
            subject        = excluded.subject,
            version        = excluded.version,
            synced_at      = now()
        where t.version < excluded.version;
        get diagnostics applied = row_count;

    elsif target = 'pipeline_status_events' then
        with deduped as (
            select distinct on (r.id) r.*
            from jsonb_populate_recordset(null::public.pipeline_status_events, batch) as r
            where r.id is not null and r.version is not null
            order by r.id, r.version desc
        )
        insert into public.pipeline_status_events as t
            (id, application_id, status, reply_id, "at", actor, version, synced_at)
        select r.id, r.application_id, r.status, r.reply_id, r."at", r.actor, r.version, now()
        from deduped as r
        on conflict (id) do update set
            application_id = excluded.application_id,
            status         = excluded.status,
            reply_id       = excluded.reply_id,
            "at"           = excluded."at",
            actor          = excluded.actor,
            version        = excluded.version,
            synced_at      = now()
        where t.version < excluded.version;
        get diagnostics applied = row_count;
    end if;

    return jsonb_build_object('received', received, 'applied', applied);
end;
$$;

-- ---------------------------------------------------------------------------
-- Grants: only service_role may call the writer.
-- ---------------------------------------------------------------------------

revoke execute on function public.pipeline_upsert(text, jsonb) from public, anon, authenticated;
grant  execute on function public.pipeline_upsert(text, jsonb) to service_role;

-- ---------------------------------------------------------------------------
-- pipeline_link_reply: the dashboard's "link this reply to an application".
--
-- Id spaces: pipeline_replies.application_id and pipeline_status_events.application_id hold
-- job_applications.id (the reply poller correlates against job_applications rows), so the target is
-- checked there, never against pipeline_jobs (whose ids are the 24/7 queue's own).
--
-- Link and event commit together. Version policy: the pipeline sends every reply at version 1, so the
-- link raises the mirror row's version by one and a later re-send of the original row (same version
-- 1) cannot undo it. The event id is derived from (reply id, application id), so a retried link
-- converges on one event. The pipeline's own SQLite store is not told about the link; its local
-- status history and revival guard do not see dashboard links (documented limitation).
-- ---------------------------------------------------------------------------

create or replace function public.pipeline_link_reply(p_reply_id text, p_application_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_kind     text;
    v_received timestamptz;
    v_current  text;
    v_status   text;
    v_event    text;
    v_written  integer := 0;
begin
    if p_reply_id is null or btrim(p_reply_id) = '' or p_application_id is null then
        raise exception 'pipeline_link_reply: reply id and application id are required'
            using errcode = '22023';
    end if;

    select kind, received_at, application_id into v_kind, v_received, v_current
    from public.pipeline_replies where id = p_reply_id for update;
    if not found then
        raise exception 'pipeline_link_reply: reply not found' using errcode = 'P0002';
    end if;
    if not exists (select 1 from public.job_applications where id = p_application_id) then
        raise exception 'pipeline_link_reply: application not found' using errcode = 'P0002';
    end if;
    if v_current is not null and v_current <> p_application_id::text then
        raise exception 'pipeline_link_reply: reply is already linked to another application'
            using errcode = 'P0001';
    end if;

    update public.pipeline_replies
    set application_id = p_application_id::text,
        needs_review   = false,
        version        = version + 1,
        synced_at      = now()
    where id = p_reply_id and (application_id is distinct from p_application_id::text or needs_review);

    -- Mirrors reply_repo.KIND_TO_STATUS.
    v_status := case v_kind
        when 'confirmation' then 'confirmed'
        when 'assessment'   then 'assessment'
        when 'interview'    then 'interview'
        when 'offer'        then 'offer'
        when 'rejection'    then 'rejected'
    end;
    if v_status is not null then
        v_event := 'link:' || md5(p_reply_id || ':' || p_application_id::text);
        insert into public.pipeline_status_events (id, application_id, status, reply_id, "at", actor, version)
        values (v_event, p_application_id::text, v_status, p_reply_id, coalesce(v_received, now()), 'user', 1)
        on conflict (id) do nothing;
        get diagnostics v_written = row_count;
    end if;

    return jsonb_build_object('linked', true, 'event', v_event, 'event_written', v_written = 1);
end;
$$;

revoke execute on function public.pipeline_link_reply(text, bigint) from public, anon, authenticated;
grant  execute on function public.pipeline_link_reply(text, bigint) to service_role;
