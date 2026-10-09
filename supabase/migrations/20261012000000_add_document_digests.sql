-- Content-bound documents: the preview revision hash now covers the SHA-256 of the resume and
-- cover-letter bytes, not just their storage paths. Uploads are content-addressed and immutable
-- (db.upload_resume_file), and the submit worker re-hashes what it downloads and refuses on
-- mismatch. Rows built before this migration have NULL digests and are refused at submit
-- (fail closed) until rebuilt.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS resume_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS cover_letter_sha256 TEXT;

-- New columns have no anon privilege unless granted (see 20260925000000); changing a digest only
-- changes preview_revision_hash, which invalidates any approval.
GRANT UPDATE (resume_sha256, cover_letter_sha256) ON job_applications TO anon;
GRANT INSERT (resume_sha256, cover_letter_sha256) ON job_applications TO anon;
GRANT UPDATE (resume_sha256, cover_letter_sha256) ON job_applications TO authenticated;
GRANT INSERT (resume_sha256, cover_letter_sha256) ON job_applications TO authenticated;

-- Uploads are immutable now; drop the policy on databases where the bucket migration already ran.
DROP POLICY IF EXISTS "resumes bucket -- anon update" ON storage.objects;

CREATE OR REPLACE FUNCTION job_application_preview_revision_hash(p_preview JSONB, p_resume TEXT, p_cover TEXT, p_docs_version TEXT, p_company TEXT, p_role TEXT, p_job_url TEXT, p_resume_sha256 TEXT, p_cover_sha256 TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN p_preview IS NULL THEN NULL
    ELSE encode(sha256(convert_to(jsonb_build_object(
      'preview', p_preview,
      'resume', coalesce(p_resume, ''),
      'cover', coalesce(p_cover, ''),
      'docs_version', coalesce(p_docs_version, ''),
      'company', coalesce(p_company, ''),
      'role', coalesce(p_role, ''),
      'job_url', coalesce(p_job_url, ''),
      'resume_sha256', coalesce(p_resume_sha256, ''),
      'cover_sha256', coalesce(p_cover_sha256, '')
    )::text, 'UTF8')), 'hex')
  END
$$;

CREATE OR REPLACE FUNCTION set_job_application_preview_revision_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.preview_revision_hash := job_application_preview_revision_hash(NEW.apply_preview, NEW.resume_file_ref, NEW.cover_letter_file_ref, NEW.documents_version, NEW.company, NEW.role, NEW.job_url, NEW.resume_sha256, NEW.cover_letter_sha256);
  RETURN NEW;
END;
$$;

-- The trigger function no longer references the 7-arg version; drop it so no overload lingers.
DROP FUNCTION IF EXISTS job_application_preview_revision_hash(JSONB, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT);
