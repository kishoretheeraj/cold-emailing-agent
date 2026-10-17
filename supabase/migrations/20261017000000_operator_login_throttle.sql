-- Operator login lockout (2026-10-17). The dashboard login now accepts a short PIN, so guessing is
-- limited in the database: Vercel's function instances share no memory, so an in-process counter
-- would not hold.
--
-- operator_login_attempt() is called by POST /api/login BEFORE the PIN is compared. It records the
-- attempt and returns false (login refused, 429) once 5 attempts fall in the last 15 minutes or 20
-- in the last day. Successful logins count too; a session lasts 30 days, so that costs nothing in
-- practice. An advisory lock serializes callers, so a burst of parallel requests cannot all slip
-- past the count before any of them is recorded. At 20 a day, trying every 4-digit PIN takes
-- 500 days.
--
-- The table is closed to every role; the function is the only way in and is callable with the
-- public anon key (the login route uses it). It can only add attempts, never remove recent ones,
-- so holding the anon key never helps a guesser: the worst it can do is use up the budget, which
-- locks the operator out exactly as typing wrong PINs would. Limits are fixed here, never taken
-- from the caller.

CREATE TABLE IF NOT EXISTS operator_login_attempts (
  id BIGSERIAL PRIMARY KEY,
  attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS operator_login_attempts_at_idx ON operator_login_attempts (attempted_at);

ALTER TABLE operator_login_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON operator_login_attempts FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE operator_login_attempts_id_seq FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION operator_login_attempt()
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_recent INT;
  v_day INT;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('operator_login_attempt'));
  DELETE FROM operator_login_attempts WHERE attempted_at < now() - interval '1 day';
  SELECT count(*) FILTER (WHERE attempted_at > now() - interval '15 minutes'), count(*)
    INTO v_recent, v_day FROM operator_login_attempts;
  IF v_recent >= 5 OR v_day >= 20 THEN
    RETURN false;
  END IF;
  INSERT INTO operator_login_attempts DEFAULT VALUES;
  RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION operator_login_attempt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION operator_login_attempt() TO anon, authenticated, service_role;
