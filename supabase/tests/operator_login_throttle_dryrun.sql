-- Prepend BEGIN and migration 20261017000000; always rolls back.
-- operator_login_attempt: 5 per 15 minutes, 20 per day, old rows pruned, table closed to every role.
DO $$
DECLARE
  i INT;
  ok BOOLEAN;
BEGIN
  DELETE FROM operator_login_attempts;
  FOR i IN 1..5 LOOP
    IF operator_login_attempt() IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'DRYRUN FAIL: attempt % refused inside the budget', i;
    END IF;
  END LOOP;
  IF operator_login_attempt() IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'DRYRUN FAIL: a sixth attempt in 15 minutes was allowed';
  END IF;
  IF (SELECT count(*) FROM operator_login_attempts) <> 5 THEN
    RAISE EXCEPTION 'DRYRUN FAIL: a refused attempt was recorded';
  END IF;

  -- Older than 15 minutes but inside the day: the 15-minute budget frees up, the daily cap does not.
  UPDATE operator_login_attempts SET attempted_at = now() - interval '1 hour';
  IF operator_login_attempt() IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'DRYRUN FAIL: attempts an hour old still blocked the 15-minute budget';
  END IF;
  DELETE FROM operator_login_attempts;
  INSERT INTO operator_login_attempts (attempted_at) SELECT now() - interval '2 hours' FROM generate_series(1, 20);
  IF operator_login_attempt() IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'DRYRUN FAIL: the 20-a-day cap did not hold';
  END IF;

  -- Rows older than a day are pruned, so yesterday's attempts never count.
  UPDATE operator_login_attempts SET attempted_at = now() - interval '25 hours';
  -- Call and count in separate statements: a count in the same statement as the call reads the
  -- snapshot from before the function's own delete and insert.
  ok := operator_login_attempt();
  IF ok IS DISTINCT FROM true OR (SELECT count(*) FROM operator_login_attempts) <> 1 THEN
    RAISE EXCEPTION 'DRYRUN FAIL: day-old attempts were not pruned';
  END IF;

  IF has_table_privilege('anon', 'public.operator_login_attempts', 'SELECT')
     OR has_table_privilege('anon', 'public.operator_login_attempts', 'DELETE')
     OR has_table_privilege('authenticated', 'public.operator_login_attempts', 'INSERT')
     OR NOT has_function_privilege('anon', 'public.operator_login_attempt()', 'EXECUTE') THEN
    RAISE EXCEPTION 'DRYRUN FAIL: operator_login_attempts grants are wrong';
  END IF;
END $$;
