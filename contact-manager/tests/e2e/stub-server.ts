/**
 * Express stub server that mimics Supabase REST at localhost:54321.
 * Serves fixture data for Playwright e2e tests.
 * Loaded via playwright.config.ts globalSetup.
 */
import express from "express";
import type { Server } from "node:http";
import fs from "node:fs";
import path from "node:path";

const FIXTURES_DIR = path.join(__dirname, "fixtures");

function loadFixture<T>(name: string): T {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURES_DIR, `${name}.json`), "utf-8")
  ) as T;
}

type Contact = {
  id: string;
  created_at: string;
  deleted_at: string | null;
  stage: string | null;
  tier: number | null;
  mode: string | null;
  dartmouth: boolean | null;
  message_id: string | null;
  [key: string]: unknown;
};

let contacts: Contact[] = loadFixture<Contact[]>("contacts");
const prompts = loadFixture<unknown[]>("prompts");

function resetFixtures() {
  contacts = loadFixture<Contact[]>("contacts");
}

export function startStubServer(): Promise<Server> {
  const app = express();
  app.use(express.json());

  // CORS for all requests (Supabase client sends origin headers)
  app.use((_req, res, next) => {
    res.header("Access-Control-Allow-Origin", "*");
    res.header(
      "Access-Control-Allow-Headers",
      "Content-Type, Authorization, apikey, Prefer, Range"
    );
    res.header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS");
    next();
  });
  app.options("/{*path}", (_req, res) => res.sendStatus(200));

  // Reset endpoint — called by Playwright beforeEach
  app.post("/__reset", (_req, res) => {
    resetFixtures();
    res.json({ ok: true });
  });

  // GET /rest/v1/contacts
  app.get("/rest/v1/contacts", (req, res) => {
    let rows = contacts.filter((c) => c.deleted_at === null);

    // Search filter: ?or=(name.ilike.*term*,company.ilike.*term*)
    const orParam = req.query["or"] as string | undefined;
    if (orParam) {
      const match = /name\.ilike\.%(.+?)%/.exec(orParam);
      const term = match?.[1]?.toLowerCase().replace(/\\%/g, "%").replace(/\\_/g, "_");
      if (term) {
        rows = rows.filter(
          (c) =>
            String(c.name ?? "").toLowerCase().includes(term) ||
            String(c.company ?? "").toLowerCase().includes(term)
        );
      }
    }

    // Tier filter: ?tier=in.(1,2)
    const tierParam = req.query["tier"] as string | undefined;
    if (tierParam) {
      const match = /in\.\((.+?)\)/.exec(tierParam);
      if (match) {
        const tiers = match[1].split(",").map(Number);
        rows = rows.filter((c) => tiers.includes(c.tier ?? -1));
      }
    }

    // Mode filter: ?mode=in.(outreach,applied)
    const modeParam = req.query["mode"] as string | undefined;
    if (modeParam) {
      const match = /in\.\((.+?)\)/.exec(modeParam);
      if (match) {
        const modes = match[1].split(",");
        rows = rows.filter((c) => modes.includes(String(c.mode ?? "")));
      }
    }

    // Stage filter: ?stage=in.(new,closed)
    const stageParam = req.query["stage"] as string | undefined;
    if (stageParam) {
      const match = /in\.\((.+?)\)/.exec(stageParam);
      if (match) {
        const stages = match[1].split(",");
        rows = rows.filter((c) => stages.includes(String(c.stage ?? "")));
      }
    }

    // Dartmouth filter: ?dartmouth=eq.true
    if (req.query["dartmouth"] === "eq.true") {
      rows = rows.filter((c) => c.dartmouth === true);
    }

    // Cursor: ?created_at=lt.ISO
    const createdAtParam = req.query["created_at"] as string | undefined;
    if (createdAtParam?.startsWith("lt.")) {
      const cursor = createdAtParam.slice(3);
      rows = rows.filter((c) => (c.created_at ?? "") < cursor);
    }

    // Sort descending by created_at
    rows = [...rows].sort((a, b) =>
      (b.created_at ?? "") < (a.created_at ?? "") ? -1 : 1
    );

    // Limit: Prefer: count=exact
    const limitMatch = /limit=(\d+)/.exec(req.url);
    const limit = limitMatch ? parseInt(limitMatch[1]) : 30;
    rows = rows.slice(0, limit);

    res.json(rows);
  });

  // PATCH /rest/v1/contacts — handles soft delete and stage/tier updates
  app.patch("/rest/v1/contacts", (req, res) => {
    const idParam = req.query["id"] as string | undefined;
    const id = idParam?.replace("eq.", "");
    if (!id) return res.status(400).json({ error: "missing id" });

    const idx = contacts.findIndex((c) => c.id === id);
    if (idx === -1) return res.status(404).json({ error: "not found" });

    contacts[idx] = { ...contacts[idx], ...req.body };
    res.json(contacts[idx]);
  });

  // GET /rest/v1/prompts
  app.get("/rest/v1/prompts", (_req, res) => {
    res.json(prompts);
  });

  return new Promise((resolve) => {
    const server = app.listen(54321, () => resolve(server));
  });
}
