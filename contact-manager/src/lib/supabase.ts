import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(url, key, {
  auth: { persistSession: false },
});

/**
 * Translates a Supabase insert error into a user-facing message.
 * On unique-constraint violations (code 23505), looks up whether the
 * conflicting contact is active or soft-deleted and returns a specific hint.
 */
export async function resolveInsertError(
  error: { code?: string; message: string },
  email: string
): Promise<string> {
  if (error.code !== "23505") return error.message;
  if (!email) return "A contact with this email is already in your list.";
  const { data } = await supabase
    .from("contacts")
    .select("deleted_at, name")
    .eq("email", email.trim())
    .limit(1);
  const row = (data as { deleted_at: string | null; name: string | null }[] | null)?.[0];
  if (row?.deleted_at) {
    const who = row.name ?? "A contact";
    return `${who} with this email was previously deleted. Restore them in the Supabase dashboard to re-add.`;
  }
  return "A contact with this email is already in your list.";
}
