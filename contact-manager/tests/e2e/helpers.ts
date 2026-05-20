/**
 * Shared Playwright helpers for e2e tests.
 * Uses page.route() to intercept Supabase REST calls and serve fixture data
 * instead of hitting the real database.
 */
import type { Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const FIXTURES_DIR = path.join(__dirname, "fixtures");

type Contact = {
  id: string;
  created_at: string;
  deleted_at: string | null;
  name: string | null;
  company: string | null;
  stage: string | null;
  tier: number | null;
  mode: string | null;
  dartmouth: boolean | null;
  message_id: string | null;
  [key: string]: unknown;
};

function loadContacts(): Contact[] {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURES_DIR, "contacts.json"), "utf-8")
  ) as Contact[];
}

/**
 * Install Supabase route interceptors on the page.
 * Call once per test (after beforeEach reset).
 */
export async function mockSupabase(page: Page) {
  // Start with a fresh copy of the fixture for each test
  let contacts: Contact[] = loadContacts();

  // Helper: apply URL query filters and return matching rows
  function applyFilters(url: string): Contact[] {
    let rows = contacts.filter((c) => c.deleted_at === null);
    const urlObj = new URL(url);
    const params = urlObj.searchParams;

    // or=name.ilike.*term*,company.ilike.*term*
    const or = params.get("or");
    if (or) {
      const m = /name\.ilike\.\%(.+?)\%/.exec(or);
      if (m) {
        const term = m[1]
          .toLowerCase()
          .replace(/\\%/g, "%")
          .replace(/\\_/g, "_");
        rows = rows.filter(
          (c) =>
            String(c.name ?? "").toLowerCase().includes(term) ||
            String(c.company ?? "").toLowerCase().includes(term)
        );
      }
    }

    // tier=in.(1,2,3)
    const tier = params.get("tier");
    if (tier) {
      const m = /in\.\((.+?)\)/.exec(tier);
      if (m) {
        const tiers = m[1].split(",").map(Number);
        rows = rows.filter((c) => tiers.includes(c.tier ?? -1));
      }
    }

    // mode=in.(outreach,applied)
    const mode = params.get("mode");
    if (mode) {
      const m = /in\.\((.+?)\)/.exec(mode);
      if (m) {
        const modes = m[1].split(",");
        rows = rows.filter((c) => modes.includes(String(c.mode ?? "")));
      }
    }

    // stage=in.(new,first_touch_drafted)
    const stage = params.get("stage");
    if (stage) {
      const m = /in\.\((.+?)\)/.exec(stage);
      if (m) {
        const stages = m[1].split(",");
        rows = rows.filter((c) => stages.includes(String(c.stage ?? "")));
      }
    }

    // id=eq.123 (single-row fetch by primary key)
    const idParam = params.get("id");
    if (idParam?.startsWith("eq.")) {
      const targetId = idParam.slice(3);
      rows = rows.filter((c) => String(c.id) === targetId);
    }

    // dartmouth=eq.true
    if (params.get("dartmouth") === "eq.true") {
      rows = rows.filter((c) => c.dartmouth === true);
    }

    // created_at=lt.ISO
    const createdAt = params.get("created_at");
    if (createdAt?.startsWith("lt.")) {
      const cursor = createdAt.slice(3);
      rows = rows.filter((c) => (c.created_at ?? "") < cursor);
    }

    // Sort descending by created_at
    rows = [...rows].sort((a, b) =>
      (b.created_at ?? "") < (a.created_at ?? "") ? -1 : 1
    );

    // Limit via range header or limit= query param
    const limitParam = params.get("limit");
    const limit = limitParam ? parseInt(limitParam) : 30;
    return rows.slice(0, limit);
  }

  // Intercept GET /rest/v1/contacts
  await page.route(/\/rest\/v1\/contacts(\?.*)?$/, async (route, request) => {
    if (request.method() === "GET") {
      const rows = applyFilters(request.url());
      // .single() sets Accept: application/vnd.pgrst.object+json.
      // PostgREST returns a plain JSON object, not an array, for this header.
      const acceptHeader = (request.headers() as Record<string, string>)["accept"] ?? "";
      if (acceptHeader.includes("application/vnd.pgrst.object+json")) {
        const row = rows[0];
        await route.fulfill({
          status: row ? 200 : 404,
          contentType: "application/json",
          body: row ? JSON.stringify(row) : JSON.stringify({ error: "Not found" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(rows),
        headers: {
          "Content-Range": `0-${rows.length - 1}/${contacts.length}`,
        },
      });
    } else if (request.method() === "PATCH") {
      // Handle soft delete and stage/tier updates
      const url = new URL(request.url());
      const idParam = url.searchParams.get("id");
      const id = idParam?.replace("eq.", "");
      const body = JSON.parse(request.postData() ?? "{}") as Partial<Contact>;

      if (id) {
        const idx = contacts.findIndex((c) => c.id === id);
        if (idx !== -1) {
          contacts[idx] = { ...contacts[idx], ...body };
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(contacts[idx]),
          });
          return;
        }
      }
      await route.fulfill({ status: 404, body: JSON.stringify({ error: "not found" }) });
    } else {
      await route.continue();
    }
  });

  // Intercept GET /rest/v1/prompts
  await page.route(/\/rest\/v1\/prompts(\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      const prompts = JSON.parse(
        fs.readFileSync(path.join(FIXTURES_DIR, "prompts.json"), "utf-8")
      ) as unknown[];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(prompts),
      });
    } else {
      await route.continue();
    }
  });

  // Intercept GET /rest/v1/email_messages
  await page.route(/\/rest\/v1\/email_messages(\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    } else {
      await route.continue();
    }
  });

  // Intercept GET /rest/v1/agent_events
  await page.route(/\/rest\/v1\/agent_events(\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
        headers: { "Content-Range": "0-0/0" },
      });
    } else {
      await route.continue();
    }
  });
}
