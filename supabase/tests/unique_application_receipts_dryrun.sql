-- Prepend BEGIN and migration 20261004000002; always rolls back.
DO $$
DECLARE
  first_id BIGINT;
  second_id BIGINT;
  evidence JSONB := jsonb_build_object('message_id', '<review-' || gen_random_uuid() || '@receipt>', 'source', 'gmail_receipt');
  bad JSONB;
BEGIN
  INSERT INTO job_applications (company, role, stage, automation_status, submit_attempted_at)
  VALUES ('Receipt review fixture', 'Product Manager', 'ready_to_submit', 'needs_confirmation', now())
  RETURNING id INTO first_id;
  INSERT INTO job_applications (company, role, stage, automation_status, submit_attempted_at)
  VALUES ('Receipt review fixture', 'Product Manager', 'ready_to_submit', 'needs_confirmation', now())
  RETURNING id INTO second_id;

  -- The first row leaves the pending queue. A later scan of the second row must still lose.
  IF record_receipt_evidence(first_id, evidence) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'DRYRUN FAIL: first receipt was not recorded';
  END IF;
  IF record_receipt_evidence(second_id, evidence) IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'DRYRUN FAIL: receipt reused across applications';
  END IF;
  IF record_receipt_evidence(second_id, evidence || jsonb_build_object('message_id', ' ' || (evidence->>'message_id') || ' ')) IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'DRYRUN FAIL: whitespace bypassed receipt uniqueness';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM job_applications WHERE id = second_id
                 AND automation_status = 'needs_confirmation' AND submission_evidence IS NULL) THEN
    RAISE EXCEPTION 'DRYRUN FAIL: rejected receipt changed second row';
  END IF;
  FOREACH bad IN ARRAY ARRAY['{}'::jsonb, '{"message_id":null}'::jsonb,
    '{"message_id":" "}'::jsonb, '{"message_id":123}'::jsonb] LOOP
    BEGIN
      PERFORM record_receipt_evidence(second_id, bad);
      RAISE EXCEPTION 'DRYRUN FAIL: invalid message_id accepted' USING ERRCODE = 'check_violation';
    EXCEPTION WHEN raise_exception THEN NULL;
    END;
  END LOOP;
  IF record_receipt_evidence(second_id, jsonb_build_object('message_id', '<review-' || gen_random_uuid() || '@receipt>')) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'DRYRUN FAIL: distinct receipt should work';
  END IF;
END $$;
SELECT 'RECEIPT DRYRUN OK';
ROLLBACK;
