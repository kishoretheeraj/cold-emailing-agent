import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { CADENCE, QUEUE_STAGES, STAGE_TO_LABEL, STAGE_TRANSITIONS } from "./cadence";
import * as constants from "./constants";
import * as types from "./types";

// Golden test: the TS mirrors of the Python agent's maps, read straight from the Python source
// so a one-sided edit fails here instead of mislabeling or misdating a send.
const REPO_ROOT = path.resolve(__dirname, "../../..");

function pythonSource(file: string): string {
  return fs.readFileSync(path.join(REPO_ROOT, file), "utf8");
}

type PyValue = string | number | null;

function pyLiteral(raw: string): PyValue {
  const v = raw.trim();
  if (v === "None") return null;
  if (/^-?\d+$/.test(v)) return Number(v);
  const str = /^"([^"]*)"$/.exec(v);
  if (str) return str[1];
  throw new Error(`unsupported Python literal: ${v}`);
}

function pyDict(source: string, name: string): Record<string, PyValue> {
  const block = new RegExp(`^${name}\\s*=\\s*\\{\\n([\\s\\S]*?)^\\}`, "m").exec(source);
  if (!block) throw new Error(`${name} dict not found`);
  const out: Record<string, PyValue> = {};
  for (const line of block[1].split("\n")) {
    const entry = /^\s*"([^"]+)"\s*:\s*("[^"]*"|[^,#\s]+)\s*,?\s*(#.*)?$/.exec(line);
    if (entry) out[entry[1]] = pyLiteral(entry[2]);
    else if (line.trim() && !line.trim().startsWith("#")) throw new Error(`unparsed line in ${name}: ${line}`);
  }
  return out;
}

function pyList(source: string, name: string): string[] {
  const block = new RegExp(`^${name}\\s*=\\s*\\[([\\s\\S]*?)\\]`, "m").exec(source);
  if (!block) throw new Error(`${name} list not found`);
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

const config = pythonSource("config.py");
const agent = pythonSource("agent.py");
const pyConstants = pythonSource("constants.py");

const FOLLOWUP_DAYS = pyDict(config, "FOLLOWUP_DAYS");
const NEXT_STAGE = pyDict(agent, "NEXT_STAGE");
const ACTION_LABEL = pyDict(agent, "ACTION_LABEL");
const DRAFTED_TO_SENT = pyDict(agent, "DRAFTED_TO_SENT");

describe("Python dict parser", () => {
  it("reads the maps it is about to compare", () => {
    expect(Object.keys(FOLLOWUP_DAYS).length).toBeGreaterThanOrEqual(8);
    expect(FOLLOWUP_DAYS.send_first_touch).toBe(5);
    expect(FOLLOWUP_DAYS.send_breakup).toBeNull();
    expect(NEXT_STAGE.send_first_touch).toBe("first_touch_drafted");
    expect(ACTION_LABEL.send_followup1).toBe("Cold Outreach/Follow-up #1");
  });
});

describe("cadence.ts mirrors config.py / agent.py", () => {
  it("covers the same actions in FOLLOWUP_DAYS, NEXT_STAGE and ACTION_LABEL", () => {
    const actions = Object.keys(NEXT_STAGE).sort();
    expect(Object.keys(FOLLOWUP_DAYS).sort()).toEqual(actions);
    expect(Object.keys(ACTION_LABEL).sort()).toEqual(actions);
  });

  it("STAGE_TRANSITIONS cadence days equal FOLLOWUP_DAYS for every action", () => {
    for (const [action, drafted] of Object.entries(NEXT_STAGE)) {
      const transition = STAGE_TRANSITIONS[drafted as string];
      expect(transition, `no STAGE_TRANSITIONS entry for ${drafted}`).toBeDefined();
      const days = transition.cadenceKey ? CADENCE[transition.cadenceKey] : null;
      expect(days, `${action} -> ${drafted}`).toBe(FOLLOWUP_DAYS[action]);
    }
  });

  it("STAGE_TO_LABEL equals ACTION_LABEL keyed by drafted stage", () => {
    const expected = Object.fromEntries(
      Object.entries(NEXT_STAGE).map(([action, drafted]) => [drafted, ACTION_LABEL[action]])
    );
    expect(STAGE_TO_LABEL).toEqual(expected);
  });

  it("STAGE_TRANSITIONS next stages equal DRAFTED_TO_SENT", () => {
    const next = Object.fromEntries(
      Object.entries(STAGE_TRANSITIONS).map(([drafted, t]) => [drafted, t.next])
    );
    expect(next).toEqual(DRAFTED_TO_SENT);
  });

  it("QUEUE_STAGES are exactly the drafted stages NEXT_STAGE produces", () => {
    expect([...QUEUE_STAGES].sort()).toEqual(Object.values(NEXT_STAGE).map(String).sort());
  });
});

describe("stage lists mirror constants.py", () => {
  const lists = ["OUTREACH_STAGES", "APPLIED_STAGES", "NETWORKING_STAGES", "REPLY_STAGES"] as const;

  it.each(lists)("constants.ts and types.ts %s match", (name) => {
    const py = pyList(pyConstants, name);
    expect([...constants[name]]).toEqual(py);
    expect([...types[name]]).toEqual(py);
  });

  it("DRAFTED_STAGES matches constants.py (all four tracks, reply included)", () => {
    const py = lists
      .flatMap((name) => pyList(pyConstants, name))
      .filter((s) => s.includes("drafted"));
    expect([...constants.DRAFTED_STAGES]).toEqual(py);
  });
});
