-- Prepend BEGIN and migration 20261014000000; always rolls back.
-- pipeline_link_reply: link + event together, version bump survives a re-send, retries converge,
-- conflicting and unknown targets are refused, and only service_role may call it.
DO $$
DECLARE
  app_id BIGINT;
  other_id BIGINT;
  v_reply TEXT := 'dryrun-reply-' || gen_random_uuid();
  res JSONB;
  failed BOOLEAN;
BEGIN
  INSERT INTO job_applications (company, role, stage) VALUES ('Link review fixture', 'Product Manager', 'applied')
  RETURNING id INTO app_id;
  INSERT INTO job_applications (company, role, stage) VALUES ('Link review fixture', 'Product Manager', 'applied')
  RETURNING id INTO other_id;

  PERFORM pipeline_upsert('pipeline_replies', jsonb_build_array(jsonb_build_object(
    'id', v_reply, 'message_id', '<' || v_reply || '@x>', 'kind', 'interview', 'needs_review', true,
    'received_at', '2026-10-02T10:00:00+00:00', 'version', 1)));

  res := pipeline_link_reply(v_reply, app_id);
  IF (res->>'event_written')::boolean IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'DRYRUN FAIL: first link wrote no status event';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pipeline_replies WHERE id = v_reply AND application_id = app_id::text
                 AND needs_review = false AND version = 2) THEN
    RAISE EXCEPTION 'DRYRUN FAIL: reply not linked at version 2';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pipeline_status_events WHERE reply_id = v_reply
                 AND application_id = app_id::text AND status = 'interview' AND actor = 'user') THEN
    RAISE EXCEPTION 'DRYRUN FAIL: status event missing or keyed to the wrong reply';
  END IF;

  res := pipeline_link_reply(v_reply, app_id);
  IF (res->>'event_written')::boolean IS DISTINCT FROM false
     OR (SELECT count(*) FROM pipeline_status_events WHERE reply_id = v_reply) <> 1
     OR (SELECT version FROM pipeline_replies WHERE id = v_reply) <> 2 THEN
    RAISE EXCEPTION 'DRYRUN FAIL: a retried link did not converge';
  END IF;

  PERFORM pipeline_upsert('pipeline_replies', jsonb_build_array(jsonb_build_object(
    'id', v_reply, 'message_id', '<' || v_reply || '@x>', 'kind', 'interview', 'needs_review', true,
    'version', 1)));
  IF NOT EXISTS (SELECT 1 FROM pipeline_replies WHERE id = v_reply AND application_id = app_id::text
                 AND needs_review = false) THEN
    RAISE EXCEPTION 'DRYRUN FAIL: a version-1 re-send undid the link';
  END IF;

  failed := false;
  BEGIN PERFORM pipeline_link_reply(v_reply, other_id);
  EXCEPTION WHEN SQLSTATE 'P0001' THEN failed := true; END;
  IF NOT failed THEN RAISE EXCEPTION 'DRYRUN FAIL: relinked to another application'; END IF;

  failed := false;
  BEGIN PERFORM pipeline_link_reply('no-such-reply', app_id);
  EXCEPTION WHEN SQLSTATE 'P0002' THEN failed := true; END;
  IF NOT failed THEN RAISE EXCEPTION 'DRYRUN FAIL: unknown reply accepted'; END IF;

  failed := false;
  BEGIN PERFORM pipeline_link_reply(v_reply, -1);
  EXCEPTION WHEN SQLSTATE 'P0002' THEN failed := true; END;
  IF NOT failed THEN RAISE EXCEPTION 'DRYRUN FAIL: unknown application accepted'; END IF;

  IF has_function_privilege('anon', 'public.pipeline_link_reply(text, bigint)', 'EXECUTE')
     OR has_function_privilege('authenticated', 'public.pipeline_link_reply(text, bigint)', 'EXECUTE')
     OR NOT has_function_privilege('service_role', 'public.pipeline_link_reply(text, bigint)', 'EXECUTE') THEN
    RAISE EXCEPTION 'DRYRUN FAIL: pipeline_link_reply grants are wrong';
  END IF;
END $$;
