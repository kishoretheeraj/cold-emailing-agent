-- Pipeline outbox mirror: the Supabase side of the Beelink's local sync_outbox.
--
-- The pipeline never writes here directly. supabase_sync.py drains its local SQLite outbox to
-- pipeline_upsert(target, rows) using the service role key.
--
-- Idempotency: every table carries a `version` integer. pipeline_upsert first tries an UPDATE
-- that applies only when the incoming version is strictly higher than the stored one, then an
-- INSERT for rows that do not exist yet, then the UPDATE once more: the trailing UPDATE closes a
-- concurrency gap where two calls insert concurrently and the lower version lands first (the
-- higher version's INSERT then finds the row present and would otherwise be lost). Replaying the
-- same event any number of times, or delivering events out of order, therefore converges on the
-- highest version and is otherwise a no-op. A later state of an entity must be sent with a higher
-- version. On update, a column the incoming row omits (NULL) keeps its stored value, so a partial
-- update such as a final attempt result never erases fields written earlier; on insert, omitted
-- columns take their DEFAULTs.
--
-- Access: RLS is enabled on all six tables with NO policies for anon or authenticated, so they are
-- denied by default. The service role bypasses RLS. Credentials are never synced here.

CREATE TABLE IF NOT EXISTS public.pipeline_jobs (
  id         text PRIMARY KEY,
  company    text,
  role       text,
  url        text,
  platform   text,
  queued_at  timestamptz,
  state      text,
  version    integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.pipeline_attempts (
  attempt_id  uuid PRIMARY KEY,
  job_id      text REFERENCES public.pipeline_jobs (id),
  started_at  timestamptz,
  finished_at timestamptz,
  status      text,
  detail      text,
  latency_s   numeric,
  dry_run     boolean,
  version     integer NOT NULL DEFAULT 1,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.pipeline_submissions (
  id                        uuid PRIMARY KEY,
  job_id                    text REFERENCES public.pipeline_jobs (id),
  attempt_id                uuid REFERENCES public.pipeline_attempts (attempt_id),
  submitted_at              timestamptz,
  confirmed                 boolean DEFAULT false,
  confirmation_artifact_path text,
  version                   integer NOT NULL DEFAULT 1,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.pipeline_documents (
  id           uuid PRIMARY KEY,
  job_id       text REFERENCES public.pipeline_jobs (id),
  kind         text CHECK (kind IN ('resume', 'cover', 'screenshot')),
  sha256       text,
  storage_path text,
  version      integer NOT NULL DEFAULT 1,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.pipeline_replies (
  message_id     text PRIMARY KEY,
  application_id text,
  job_id         text,
  thread_key     text,
  source         text,
  from_domain    text,
  subject        text,
  received_at    timestamptz,
  kind           text,
  status_after   text,
  confidence     real,
  match_method   text,
  needs_review   boolean DEFAULT false,
  version        integer NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.pipeline_status_events (
  id             text PRIMARY KEY,
  application_id text NOT NULL,
  status         text NOT NULL,
  reply_id       text,
  at             timestamptz NOT NULL,
  actor          text CHECK (actor IN ('pipeline', 'monitor', 'user')),
  version        integer NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_attempts_job ON public.pipeline_attempts (job_id, started_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_attempts_started ON public.pipeline_attempts (started_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_replies_review ON public.pipeline_replies (received_at) WHERE needs_review;
CREATE INDEX IF NOT EXISTS idx_pipeline_replies_application ON public.pipeline_replies (application_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_replies_job ON public.pipeline_replies (job_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_status_events_app ON public.pipeline_status_events (application_id, at);

ALTER TABLE public.pipeline_jobs          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_attempts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_documents     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_replies       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_status_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_submissions   ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.pipeline_jobs, public.pipeline_attempts, public.pipeline_submissions,
              public.pipeline_documents, public.pipeline_replies, public.pipeline_status_events
  FROM anon, authenticated;

COMMENT ON TABLE public.pipeline_jobs IS
  'Mirror of the Beelink pipeline job queue. Written only by pipeline_upsert (service role). RLS on, no policies: owner-scoped read policies are a TODO until the auth story is settled.';
COMMENT ON TABLE public.pipeline_attempts IS
  'One row per application attempt. Written only by pipeline_upsert (service role). RLS on, no policies: owner-scoped read policies are a TODO until the auth story is settled.';
COMMENT ON TABLE public.pipeline_submissions IS
  'One row per submitted application. Written only by pipeline_upsert (service role). RLS on, no policies: owner-scoped read policies are a TODO until the auth story is settled.';
COMMENT ON TABLE public.pipeline_documents IS
  'Resume, cover letter and screenshot references (hash and storage path only, never contents). Written only by pipeline_upsert (service role). RLS on, no policies: owner-scoped read policies are a TODO until the auth story is settled.';
COMMENT ON TABLE public.pipeline_replies IS
  'Employer replies matched (or queued for review) against applications. Written by pipeline_upsert and the dashboard link route (service role). RLS on, no policies: owner-scoped read policies are a TODO until the auth story is settled.';
COMMENT ON TABLE public.pipeline_status_events IS
  'Append-style status history per application. Written by pipeline_upsert and the dashboard link route (service role). RLS on, no policies: owner-scoped read policies are a TODO until the auth story is settled.';

CREATE OR REPLACE FUNCTION public.pipeline_upsert(target text, rows jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  pk         text;
  ins_cols   text;
  ins_select text;
  upd_cols   text;
  upd_select text;
  stmt       text;
  item       jsonb;
BEGIN
  pk := CASE target
    WHEN 'pipeline_jobs'          THEN 'id'
    WHEN 'pipeline_attempts'      THEN 'attempt_id'
    WHEN 'pipeline_submissions'   THEN 'id'
    WHEN 'pipeline_documents'     THEN 'id'
    WHEN 'pipeline_replies'       THEN 'message_id'
    WHEN 'pipeline_status_events' THEN 'id'
  END;
  IF pk IS NULL THEN
    RAISE EXCEPTION 'pipeline_upsert: unknown target' USING ERRCODE = '22023';
  END IF;
  IF rows IS NULL OR jsonb_typeof(rows) <> 'array' THEN
    RAISE EXCEPTION 'pipeline_upsert: rows must be a json array' USING ERRCODE = '22023';
  END IF;

  -- Insert list: every column except the audit timestamps. Omitted fields fall back to the
  -- column DEFAULT (e.g. needs_review=false, confirmed=false) via coalesce.
  SELECT
    string_agg(format('%I', c.column_name), ', ' ORDER BY c.ordinal_position),
    string_agg(
      CASE WHEN c.column_default IS NOT NULL
           THEN format('coalesce(r.%I, %s)', c.column_name, c.column_default)
           ELSE format('r.%I', c.column_name) END,
      ', ' ORDER BY c.ordinal_position)
  INTO ins_cols, ins_select
  FROM information_schema.columns c
  WHERE c.table_schema = 'public'
    AND c.table_name = target
    AND c.column_name NOT IN ('created_at', 'updated_at');

  -- Update list: non-pk columns. An omitted (NULL) incoming field keeps the stored value;
  -- the version only moves forward (the WHERE clause guarantees r.version > t.version).
  SELECT
    string_agg(format('%I', c.column_name), ', ' ORDER BY c.ordinal_position),
    string_agg(format('coalesce(r.%I, t.%I)', c.column_name, c.column_name),
               ', ' ORDER BY c.ordinal_position)
  INTO upd_cols, upd_select
  FROM information_schema.columns c
  WHERE c.table_schema = 'public'
    AND c.table_name = target
    AND c.column_name NOT IN ('created_at', 'updated_at', pk, 'version');

  FOR item IN
    SELECT e.value FROM jsonb_array_elements(rows) WITH ORDINALITY AS e(value, ord) ORDER BY e.ord
  LOOP
    -- UPDATE first: applies only when the incoming version is strictly higher. A column the
    -- incoming row omits keeps its stored value, so a partial update never erases fields.
    stmt := format(
      'UPDATE public.%1$I AS t SET (%2$s) = (%3$s), version = r.version, updated_at = now() '
      'FROM jsonb_populate_record(NULL::public.%1$I, $1) AS r '
      'WHERE t.%4$I = r.%4$I AND r.version > t.version',
      target, upd_cols, upd_select, pk);
    EXECUTE stmt USING item;

    -- INSERT when no row exists: column defaults apply to omitted fields.
    stmt := format(
      'INSERT INTO public.%1$I (%2$s) '
      'SELECT %3$s FROM jsonb_populate_record(NULL::public.%1$I, $1) AS r '
      'WHERE NOT EXISTS (SELECT 1 FROM public.%1$I WHERE %4$I = r.%4$I) '
      'ON CONFLICT DO NOTHING',
      target, ins_cols, ins_select, pk);
    EXECUTE stmt USING item;

    -- UPDATE again: a concurrent call may have inserted a lower version between our UPDATE and
    -- INSERT above (both UPDATE zero rows, the lower version inserts first, the higher version's
    -- INSERT then finds the row present). The second UPDATE converges on the highest version.
    stmt := format(
      'UPDATE public.%1$I AS t SET (%2$s) = (%3$s), version = r.version, updated_at = now() '
      'FROM jsonb_populate_record(NULL::public.%1$I, $1) AS r '
      'WHERE t.%4$I = r.%4$I AND r.version > t.version',
      target, upd_cols, upd_select, pk);
    EXECUTE stmt USING item;
  END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.pipeline_upsert(text, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.pipeline_upsert(text, jsonb) TO service_role;
