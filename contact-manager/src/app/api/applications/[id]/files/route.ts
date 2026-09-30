export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

const SIGNED_URL_EXPIRY_SECONDS = 300;

// I11: a missing file ref and a transient signing failure are different situations and must
// never be conflated -- "no file exists" is a normal, expected state; "couldn't sign it right
// now" means a resume may genuinely exist but the sheet would otherwise wrongly claim it
// doesn't, on the one screen whose entire purpose is reviewing that resume before a real
// submission. This discriminated result is the internal representation; toFileFields() below
// maps it onto the JSON response's existing resume_url/cover_letter_url shape plus a new
// _error flag, so every other task's `/files` mocks (which only ever set the url fields) keep
// working unchanged -- they just implicitly mean "no error".
type SignResult = { status: "ok"; url: string } | { status: "missing" } | { status: "error" };

async function signIfPresent(
  supabase: ReturnType<typeof getClient>,
  path: string | null
): Promise<SignResult> {
  if (!path) return { status: "missing" };
  const { data, error } = await supabase.storage
    .from("resumes")
    .createSignedUrl(path, SIGNED_URL_EXPIRY_SECONDS);
  if (error || !data) return { status: "error" };
  return { status: "ok", url: data.signedUrl };
}

function toFileFields(result: SignResult) {
  return {
    url: result.status === "ok" ? result.url : null,
    error: result.status === "error",
  };
}

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .select("resume_file_ref, cover_letter_file_ref")
      .eq("id", Number(id))
      .single();
    if (error) throw error;

    const [resume, coverLetter] = await Promise.all([
      signIfPresent(supabase, data?.resume_file_ref ?? null),
      signIfPresent(supabase, data?.cover_letter_file_ref ?? null),
    ]);
    const resumeFields = toFileFields(resume);
    const coverLetterFields = toFileFields(coverLetter);

    return Response.json({
      resume_url: resumeFields.url,
      resume_error: resumeFields.error,
      cover_letter_url: coverLetterFields.url,
      cover_letter_error: coverLetterFields.error,
    });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
