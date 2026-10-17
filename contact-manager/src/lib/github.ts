// The agent repo whose workflows the app dispatches (Run Agent, GitHub-hosted submit).
export const GITHUB_REPO = "kishoretheeraj/cold-emailing-agent";

export function workflowDispatchUrl(workflowFile: string): string {
  return `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(workflowFile)}/dispatches`;
}
