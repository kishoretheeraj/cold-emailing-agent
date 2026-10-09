-- Phase 3 (resume intelligence) -- Storage bucket for generated resume/cover-letter files.
-- Private (public=false) -- these are personal application documents, not shared links.
--
-- Every other table in this project runs with RLS disabled (see draft_history's migration
-- comment) since this repo authenticates with a single anon key and has no separate
-- service-role credential anywhere in the stack. Storage always enforces RLS-style policies on
-- storage.objects (there is no bucket-level "disable RLS" toggle), so these policies grant
-- the anon key read and insert on this one bucket -- scoped to bucket_id = 'resumes' only, not
-- every bucket. There is deliberately NO update policy: uploads are content-addressed and
-- immutable (db.upload_resume_file), so an approved PDF cannot be overwritten in place.

insert into storage.buckets (id, name, public)
values ('resumes', 'resumes', false)
on conflict (id) do nothing;

create policy "resumes bucket -- anon read" on storage.objects
  for select using (bucket_id = 'resumes');

create policy "resumes bucket -- anon write" on storage.objects
  for insert with check (bucket_id = 'resumes');
