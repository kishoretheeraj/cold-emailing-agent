// @vitest-environment node
// The warm-path queries against the real local stack: Postgres 16 with every migration, PostgREST
// 12 with the 1000-row cap, the anon role's real grants and the contacts_link_guard trigger.
// Skipped unless STRESS_SUPABASE_URL is set (scripts/stress/up.sh brings the stack up):
//   STRESS_SUPABASE_URL=http://127.0.0.1:54331 PGHOST=/tmp/job-agent-stress PGPORT=54329 npx vitest run warmPathsData.stress
import { createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { beforeAll, describe, expect, it } from "vitest";
import {
  findByEmail,
  insertContact,
  knownPeopleByCompany,
  linkContact,
  linkedCounts,
  loadCompanyContacts,
  loadLinked,
  unlinkContact,
} from "./warmPathsData";
import { companyKey } from "./warmPaths";

const URL = process.env.STRESS_SUPABASE_URL;
const SECRET = process.env.STRESS_JWT_SECRET ?? "local-stress-secret-local-stress-secret-0123456789";
const RUN = `w${Date.now().toString(36)}`;

function jwt(role: string): string {
  const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const head = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ role, exp: Math.floor(Date.now() / 1000) + 3600 })}`;
  return `${head}.${createHmac("sha256", SECRET).update(head).digest("base64url")}`;
}

function sql(query: string): string {
  return execFileSync("psql", ["-d", "stress", "-Atqc", query], {
    env: { ...process.env, PGUSER: "postgres" }, encoding: "utf-8",
  }).trim();
}

function app(stage = "ready_to_submit", status = "ready_for_review"): number {
  return Number(sql(`INSERT INTO job_applications (company, role, stage, automation_status, apply_preview)
    VALUES ('${RUN} Acme, Inc.', 'Associate Product Manager', '${stage}', '${status}', '{"platform":"greenhouse"}')
    RETURNING id`).split("\n")[0]);
}

function person(n: number, extra: Record<string, unknown> = {}) {
  return { name: `Person ${n}`, email: `${RUN}.p${n}@acme.example`, company: `${RUN} Acme Inc`, mode: "applied",
    stage: "new", reply_status: "no_reply", ...extra };
}

describe.skipIf(!URL)("warm paths against PostgREST", () => {
  let db: SupabaseClient;

  beforeAll(() => {
    db = createClient(URL!, jwt("anon"), { auth: { persistSession: false } });
  });

  it("counts known people past the 1000-row cap", async () => {
    sql(`INSERT INTO contacts (name, email, company)
         SELECT 'Bulk ' || n, '${RUN}.bulk' || n || '@bulk.example', '${RUN} Bulkco ' || (n % 3)
         FROM generate_series(1, 1200) AS n`);
    const counts = await knownPeopleByCompany(db);
    expect(counts.get(companyKey(`${RUN} Bulkco 0`))).toBe(400);
    expect([0, 1, 2].reduce((sum, i) => sum + (counts.get(companyKey(`${RUN} Bulkco ${i}`)) ?? 0), 0)).toBe(1200);
  });

  it("finds same-company contacts by companyKey, not by substring", async () => {
    sql(`INSERT INTO contacts (name, email, company) VALUES
         ('Same', '${RUN}.same@acme.example', 'The ${RUN} Acme Company'),
         ('Other', '${RUN}.other@acme.example', '${RUN} Acmeco Labs'),
         ('Gone', '${RUN}.gone@acme.example', '${RUN} Acme')`);
    sql(`UPDATE contacts SET deleted_at = now() WHERE email = '${RUN}.gone@acme.example'`);
    const found = (await loadCompanyContacts(db, `${RUN} Acme, Inc.`)).map((c) => c.email);
    expect(found).toContain(`${RUN}.same@acme.example`);
    expect(found).not.toContain(`${RUN}.other@acme.example`);
    expect(found).not.toContain(`${RUN}.gone@acme.example`);
  });

  it("finds an email case-insensitively and treats _ literally", async () => {
    sql(`INSERT INTO contacts (name, email) VALUES ('U', '${RUN}.a_b@acme.example')`);
    expect((await findByEmail(db, `${RUN}.A_B@ACME.example`))?.name).toBe("U");
    expect(await findByEmail(db, `${RUN}.axb@acme.example`)).toBeNull();
  });

  it("links three people and refuses a fourth through the trigger", async () => {
    const id = app();
    for (const n of [1, 2, 3]) {
      const r = await insertContact(db, { ...person(n), job_application_id: id, relationship: "hiring_manager" });
      expect(r.ok).toBe(true);
    }
    const fourth = await insertContact(db, { ...person(4), job_application_id: id });
    expect(fourth).toMatchObject({ ok: false, conflict: true });
    if (!fourth.ok) expect(fourth.error).toContain("already has 3 people");
    expect((await loadLinked(db, id)).length).toBe(3);
    expect((await linkedCounts(db, [id])).get(id)).toBe(3);
  });

  it("lets only three of six concurrent links through", async () => {
    const id = app();
    const ids = [10, 11, 12, 13, 14, 15].map((n) =>
      Number(sql(`INSERT INTO contacts (name, email, company, mode) VALUES ('C${n}', '${RUN}.c${n}@acme.example', '${RUN} Acme', 'networking') RETURNING id`).split("\n")[0]));
    const results = await Promise.all(ids.map((cid) => linkContact(db, cid, id)));
    expect(results.filter((r) => r?.ok).length).toBe(3);
    expect(results.filter((r) => r && !r.ok && r.conflict).length).toBe(3);
    expect(sql(`SELECT count(*) FROM contacts WHERE job_application_id = ${id}`)).toBe("3");
  });

  it("refuses a mid-thread or outreach contact, and unlinks", async () => {
    const id = app();
    const mid = Number(sql(`INSERT INTO contacts (name, email, mode, stage) VALUES ('Mid', '${RUN}.mid@acme.example', 'networking', 'networking_sent') RETURNING id`).split("\n")[0]);
    const cold = Number(sql(`INSERT INTO contacts (name, email) VALUES ('Cold', '${RUN}.cold@acme.example') RETURNING id`).split("\n")[0]);
    expect(await linkContact(db, mid, id)).toMatchObject({ ok: false, conflict: true });
    expect(await linkContact(db, cold, id)).toMatchObject({ ok: false, conflict: true });
    const fresh = Number(sql(`INSERT INTO contacts (name, email, mode) VALUES ('Fresh', '${RUN}.fresh@acme.example', 'applied') RETURNING id`).split("\n")[0]);
    expect((await linkContact(db, fresh, id))?.ok).toBe(true);
    expect(await linkContact(db, fresh, id)).toBeNull();            // already linked: no row matches
    expect(await unlinkContact(db, fresh, id)).toBe(true);
    expect(await unlinkContact(db, fresh, id)).toBe(false);
  });

  it("holds only through the clamped RPC", async () => {
    const id = app();
    const direct = await db.from("job_applications").update({ referral_hold_until: "2099-01-01T00:00:00Z" }).eq("id", id);
    expect(direct.error).not.toBeNull();
    const { data, error } = await db.rpc("hold_for_referral", { p_id: id, p_days: 365 });
    expect(error).toBeNull();
    expect(Date.parse(String(data)) - Date.now()).toBeLessThanOrEqual(14 * 86_400_000 + 60_000);
    const closed = app("rejected", "idle");
    expect((await db.rpc("hold_for_referral", { p_id: closed, p_days: 5 })).error).not.toBeNull();
    expect((await db.rpc("release_referral_hold", { p_id: id })).error).toBeNull();
  });
});
