import { describe, it, expect, vi, beforeEach } from "vitest";
import { operatorCookie, stubOperatorLogin } from "@/lib/testing/operatorSession";

// Every mutating route checks the operator session itself, so a request that slipped past
// proxy.ts (a matcher change, a runtime skip) still cannot send mail, spend tokens, dispatch a
// workflow or write a row. Nothing downstream may even be constructed.
const { createClient, getGmailClient, anthropicCreate, warmData } = vi.hoisted(() => ({
  createClient: vi.fn(),
  getGmailClient: vi.fn(),
  anthropicCreate: vi.fn(),
  warmData: Object.fromEntries(
    [
      "loadApplication", "loadLinked", "loadCompanyContacts", "findByEmail", "loadPreferences",
      "insertContact", "linkContact", "unlinkContact", "knownPeopleByCompany", "linkedCounts",
      "companyApplicationIds", "perCompanyCap", "visaRows",
    ].map((name) => [name, vi.fn()])
  ),
}));

vi.mock("@supabase/supabase-js", () => ({ createClient }));
vi.mock("@/lib/gmail-server", () => ({ getGmailClient }));
vi.mock("@anthropic-ai/sdk", () => ({
  default: class FakeAnthropic {
    messages = { create: anthropicCreate };
  },
}));
vi.mock("@/lib/warmPathsData", () => ({ ...warmData, CONTACT: "" }));

import * as sendDraft from "./send-draft/route";
import * as trashMessage from "./trash-message/route";
import * as updateDraft from "./update-draft/route";
import * as triggerAgent from "./trigger-agent/route";
import * as agentConfig from "./agent-config/route";
import * as extract from "./extract/route";
import * as previewDraft from "./preview-draft/route";
import * as applications from "./applications/route";
import * as application from "./applications/[id]/route";
import * as submit from "./applications/[id]/submit/route";
import * as requeuePreview from "./applications/[id]/requeue-preview/route";
import * as resolveConfirmation from "./applications/[id]/resolve-confirmation/route";
import * as resetApproval from "./applications/[id]/reset-approval/route";
import * as takeoverContinue from "./applications/[id]/takeover-continue/route";
import * as people from "./applications/[id]/people/route";
import * as peopleLink from "./applications/[id]/people/link/route";
import * as peopleUnlink from "./applications/[id]/people/unlink/route";
import * as hold from "./applications/[id]/hold/route";

type Handler = (req: Request, ctx: { params: Promise<{ id: string }> }) => Promise<Response>;

const ROUTES: [string, string, Handler][] = [
  ["POST", "/api/send-draft", sendDraft.POST],
  ["POST", "/api/trash-message", trashMessage.POST],
  ["POST", "/api/update-draft", updateDraft.POST],
  ["POST", "/api/trigger-agent", triggerAgent.POST],
  ["POST", "/api/agent-config", agentConfig.POST],
  ["POST", "/api/extract", extract.POST],
  ["POST", "/api/preview-draft", previewDraft.POST],
  ["POST", "/api/applications", applications.POST],
  ["PATCH", "/api/applications/5", application.PATCH],
  ["POST", "/api/applications/5/submit", submit.POST],
  ["POST", "/api/applications/5/requeue-preview", requeuePreview.POST],
  ["POST", "/api/applications/5/resolve-confirmation", resolveConfirmation.POST],
  ["POST", "/api/applications/5/reset-approval", resetApproval.POST],
  ["POST", "/api/applications/5/takeover-continue", takeoverContinue.POST],
  ["POST", "/api/applications/5/people", people.POST],
  ["POST", "/api/applications/5/people/link", peopleLink.POST],
  ["POST", "/api/applications/5/people/unlink", peopleUnlink.POST],
  ["POST", "/api/applications/5/hold", hold.POST],
  ["DELETE", "/api/applications/5/hold", hold.DELETE],
];

function call(handler: Handler, method: string, path: string, cookie?: string) {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cookie) headers.cookie = cookie;
  const req = new Request(`http://localhost${path}`, {
    method,
    headers,
    body: JSON.stringify({ contact_id: "1", scope: "agent", text: "x", company: "A", role: "B" }),
  });
  return handler(req, { params: Promise.resolve({ id: "5" }) });
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  stubOperatorLogin();
  vi.stubEnv("GITHUB_DISPATCH_TOKEN", "test-token");
  vi.stubEnv("APPROVAL_SIGNING_KEY", "k".repeat(32));
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
});

function expectNothingDownstream() {
  expect(createClient).not.toHaveBeenCalled();
  expect(getGmailClient).not.toHaveBeenCalled();
  expect(anthropicCreate).not.toHaveBeenCalled();
  expect(fetchSpy).not.toHaveBeenCalled();
  for (const fn of Object.values(warmData)) expect(fn).not.toHaveBeenCalled();
}

describe("operator guard on mutating API routes", () => {
  it.each(ROUTES)("%s %s without a session returns 401", async (method, path, handler) => {
    const res = await call(handler, method, path);
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Sign in required" });
    expectNothingDownstream();
  });

  it.each(ROUTES)("%s %s with a forged session returns 401", async (method, path, handler) => {
    const res = await call(handler, method, path, `cm_session=9999999999.${"0".repeat(64)}`);
    expect(res.status).toBe(401);
    expectNothingDownstream();
  });

  it.each(ROUTES)("%s %s returns 401 when login is not configured", async (method, path, handler) => {
    const cookie = operatorCookie();
    vi.stubEnv("SESSION_SECRET", "");
    const res = await call(handler, method, path, cookie);
    expect(res.status).toBe(401);
    expectNothingDownstream();
  });

  it.each(ROUTES)("%s %s lets a valid session through the guard", async (method, path, handler) => {
    createClient.mockImplementation(() => {
      throw new Error("reached the database");
    });
    getGmailClient.mockImplementation(() => {
      throw new Error("reached Gmail");
    });
    const res = await call(handler, method, path, operatorCookie()).catch(() => null);
    expect(res?.status).not.toBe(401);
  });
});
