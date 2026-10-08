import { createHmac } from "node:crypto";

// Mirrors approval_signature.py. The key is server-only: an approval signed with it proves the
// operator's logged-in session approved the row, which the anon key alone cannot.
export const APPROVAL_KEY_MIN_LENGTH = 32;

export function approvalSigningKey(): string | null {
  const key = process.env.APPROVAL_SIGNING_KEY;
  return key && key.length >= APPROVAL_KEY_MIN_LENGTH ? key : null;
}

export function signApproval(key: string, id: number, revisionHash: string, signedAtMs: number): string {
  const message = `approval:v1:${id}:${revisionHash}:${signedAtMs}`;
  return createHmac("sha256", key).update(message).digest("hex");
}
