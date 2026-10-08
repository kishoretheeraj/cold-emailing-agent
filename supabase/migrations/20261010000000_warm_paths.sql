-- Warm paths (docs/superpowers/specs/2026-10-08-warm-paths-design.md): link contacts to the job
-- application they are about, and let a ready preview wait for a referral.
--
-- contacts has RLS disabled and several anon-key writers (the forms, import, the people routes),
-- so the link rules live in a trigger, not in any one route:
--   * only an applied- or networking-mode contact that has not been emailed and has not replied
--     can be linked (linking a mid-thread contact would re-gate a thread about another job);
--   * at most 3 live (not soft-deleted) people per application (config.WARM_MAX_PEOPLE_PER_APPLICATION
--     and warmPaths.ts mirror the 3), checked under a row lock on the application so two concurrent
--     links cannot both see 2; restoring a soft-deleted linked contact is checked the same way;
--   * never onto a rejected or withdrawn application.
-- Unlinking (job_application_id -> NULL) is always allowed. Existing rows are untouched (NULL).
--
-- referral_hold_until is written only by hold_for_referral / release_referral_hold. It is outside
-- preview_revision_hash, so holding or releasing never changes what an approval is bound to.
-- No column grant: anon and authenticated (the anon key is public; email signup is open) cannot
-- set an arbitrary date. Held rows stay visible in the queue, so a hold can never hide a row.

ALTER TABLE contacts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS job_application_id BIGINT NULL
  REFERENCES job_applications(id) ON DELETE SET NULL;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS relationship TEXT NULL;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_relationship_check;
ALTER TABLE contacts ADD CONSTRAINT contacts_relationship_check CHECK (
  relationship IS NULL
  OR relationship IN ('hiring_manager', 'leader', 'recruiter', 'alum', 'team_member', 'other')
);
CREATE INDEX IF NOT EXISTS idx_contacts_job_application_id
  ON contacts (job_application_id) WHERE job_application_id IS NOT NULL;

COMMENT ON COLUMN contacts.job_application_id IS
  'The job application this person is about (warm paths). Applied-mode mail waits for the '
  'application to be submitted and stops when it closes. Set only at stage new / no_reply.';
COMMENT ON COLUMN contacts.relationship IS
  'Who this person is to the application: hiring_manager, leader, recruiter, alum, team_member, other.';

CREATE OR REPLACE FUNCTION contacts_link_guard()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_linking  BOOLEAN;
  v_restore  BOOLEAN;
  v_app      TEXT;
  v_linked   INTEGER;
BEGIN
  IF NEW.job_application_id IS NULL THEN
    RETURN NEW;
  END IF;

  v_linking := TG_OP = 'INSERT' OR NEW.job_application_id IS DISTINCT FROM OLD.job_application_id;
  v_restore := TG_OP = 'UPDATE' AND OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL;
  IF NOT v_linking AND NOT v_restore THEN
    RETURN NEW;
  END IF;
  IF NEW.deleted_at IS NOT NULL THEN
    RETURN NEW;
  END IF;

  IF v_linking THEN
    IF coalesce(NEW.mode, 'outreach') NOT IN ('applied', 'networking') THEN
      RAISE EXCEPTION 'warm_paths: only applied or networking contacts can be linked (mode %)', NEW.mode;
    END IF;
    IF coalesce(NEW.stage, 'new') <> 'new' OR coalesce(NEW.reply_status, 'no_reply') <> 'no_reply'
       OR (TG_OP = 'UPDATE' AND (coalesce(OLD.stage, 'new') <> 'new'
                                 OR coalesce(OLD.reply_status, 'no_reply') <> 'no_reply')) THEN
      RAISE EXCEPTION 'warm_paths: only a contact not yet emailed can be linked';
    END IF;
  END IF;

  SELECT stage INTO v_app FROM job_applications WHERE id = NEW.job_application_id FOR UPDATE;
  IF v_linking AND v_app IN ('rejected', 'withdrawn') THEN
    RAISE EXCEPTION 'warm_paths: application % is closed', NEW.job_application_id;
  END IF;

  SELECT count(*) INTO v_linked
  FROM contacts
  WHERE job_application_id = NEW.job_application_id
    AND deleted_at IS NULL
    AND id IS DISTINCT FROM NEW.id;
  IF v_linked >= 3 THEN
    RAISE EXCEPTION 'warm_paths: application % already has 3 people', NEW.job_application_id;
  END IF;

  RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION contacts_link_guard() FROM PUBLIC;

DROP TRIGGER IF EXISTS contacts_link_guard ON contacts;
CREATE TRIGGER contacts_link_guard
  BEFORE INSERT OR UPDATE OF job_application_id, deleted_at ON contacts
  FOR EACH ROW EXECUTE FUNCTION contacts_link_guard();

-- ── Referral hold ─────────────────────────────────────────────────────────────

ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS referral_hold_until TIMESTAMPTZ NULL;
REVOKE UPDATE (referral_hold_until), INSERT (referral_hold_until) ON job_applications FROM anon, authenticated;

COMMENT ON COLUMN job_applications.referral_hold_until IS
  'Waiting on a referral until this time (warm paths). Written only by hold_for_referral (1..14 days, '
  'ready_for_review and unapproved only) and release_referral_hold. Never part of preview_revision_hash.';

CREATE OR REPLACE FUNCTION hold_for_referral(p_id BIGINT, p_days INTEGER)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE v_until TIMESTAMPTZ;
BEGIN
  UPDATE job_applications
  SET referral_hold_until = now() + make_interval(days => least(greatest(coalesce(p_days, 10), 1), 14)),
      updated_at = now()
  WHERE id = p_id
    AND automation_status = 'ready_for_review'
    AND approved_at IS NULL
  RETURNING referral_hold_until INTO v_until;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'hold_for_referral: row % is not an unapproved preview', p_id;
  END IF;
  RETURN v_until;
END;
$$;

CREATE OR REPLACE FUNCTION release_referral_hold(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications SET referral_hold_until = NULL, updated_at = now() WHERE id = p_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'release_referral_hold: no row %', p_id;
  END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION hold_for_referral(BIGINT, INTEGER) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION release_referral_hold(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION hold_for_referral(BIGINT, INTEGER) TO anon, authenticated;
GRANT  EXECUTE ON FUNCTION release_referral_hold(BIGINT) TO anon, authenticated;
