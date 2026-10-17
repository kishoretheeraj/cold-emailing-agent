import { describe, it, expect } from "vitest";
import { GITHUB_REPO, workflowDispatchUrl } from "./github";

describe("workflowDispatchUrl", () => {
  it("builds the workflow_dispatch endpoint for the agent repo", () => {
    expect(GITHUB_REPO).toBe("kishoretheeraj/cold-emailing-agent");
    expect(workflowDispatchUrl("daily_agent.yml")).toBe(
      "https://api.github.com/repos/kishoretheeraj/cold-emailing-agent/actions/workflows/daily_agent.yml/dispatches"
    );
  });

  it("encodes the workflow file name", () => {
    expect(workflowDispatchUrl("a b.yml")).toContain("/workflows/a%20b.yml/dispatches");
  });
});
