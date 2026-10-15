-- Worker lifecycle RPCs become service_role-only (production-readiness audit 2026-10-14, F11/F12).
--
-- Why: the anon key is public (it ships to the browser as NEXT_PUBLIC_SUPABASE_ANON_KEY), and
-- worker_lease_id is SELECT-able by anon. So anyone holding the anon key could read a live lease
-- and call complete_preview with a crafted preview (which the operator might then approve), or
-- record_submission to mark a row applied that never was, or record_receipt_evidence (no lease at
-- all) with an invented message id. The lease is a concurrency token, never authorization.
--
-- After this migration only the workers can drive these transitions, using SUPABASE_SERVICE_ROLE_KEY
-- (db._worker_client). The contact-manager's own RPCs (approve_application, reset_approval,
-- requeue_preview, resolve_confirmation, hold_for_referral, release_referral_hold,
-- takeover_continue) are untouched and stay anon-callable.
--
-- Rollout order (the workers must hold the key first, or every prepare/submit/reconcile fails):
--   1. SUPABASE_SERVICE_ROLE_KEY in /etc/job-agent/base.env on the Beelink and in the
--      monitor / apply_agent_preview / apply_agent_submit workflow env (GitHub secret exists).
--   2. Deploy the code that routes these calls through the service-role client.
--   3. db_migrate.yml dryrun with supabase/tests/worker_rpcs_service_role_dryrun.sql, then push.
--
-- Supabase's default privileges grant EXECUTE on new public functions to anon, authenticated and
-- service_role directly (not only through PUBLIC), so each role is revoked by name. The 4-argument
-- record_submission overload was dropped by 20261008000000, so only the 5-argument one exists.

REVOKE EXECUTE ON FUNCTION claim_application(BIGINT, UUID, TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION claim_application(BIGINT, UUID, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION heartbeat_application(BIGINT, UUID) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION heartbeat_application(BIGINT, UUID) TO service_role;

REVOKE EXECUTE ON FUNCTION renew_submission_lease(BIGINT, UUID, TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION renew_submission_lease(BIGINT, UUID, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION complete_preview(BIGINT, UUID, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION complete_preview(BIGINT, UUID, JSONB, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION release_application(BIGINT, UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION release_application(BIGINT, UUID, TEXT, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION record_submission(BIGINT, UUID, TEXT, DATE, JSONB) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION record_submission(BIGINT, UUID, TEXT, DATE, JSONB) TO service_role;

REVOKE EXECUTE ON FUNCTION mark_application_unsupported(BIGINT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION mark_application_unsupported(BIGINT, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION recover_stale_leases(INT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION recover_stale_leases(INT) TO service_role;

REVOKE EXECUTE ON FUNCTION record_receipt_evidence(BIGINT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION record_receipt_evidence(BIGINT, JSONB) TO service_role;

REVOKE EXECUTE ON FUNCTION request_takeover(BIGINT, UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION request_takeover(BIGINT, UUID, TEXT, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION clear_takeover(BIGINT, UUID) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION clear_takeover(BIGINT, UUID) TO service_role;

REVOKE EXECUTE ON FUNCTION log_application_run(UUID, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, INT, INT, INT, INT, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION log_application_run(UUID, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, INT, INT, INT, INT, TEXT, JSONB) TO service_role;
