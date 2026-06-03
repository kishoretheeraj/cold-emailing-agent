import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LabPreviewPanel } from "./LabPreviewPanel";
import type { WriterPreview, CriticPreview } from "./LabPreviewPanel";

const WRITER_PREVIEW: WriterPreview = {
  kind: "writer",
  body: "Hi Alice, I noticed you work on fintech. Let's connect.",
  subject: "Quick question",
};

const WRITER_PREVIEW_NO_SUBJECT: WriterPreview = {
  kind: "writer",
  body: "Hi Alice, following up on my last email.",
};

const CRITIC_PREVIEW: CriticPreview = {
  kind: "critic",
  score: 5,
  verdict: "FAIL",
  feedback: "Too generic. Add a specific hook.",
  killed_by: ["personalization"],
  failed_soft_criteria: ["specificity"],
  rewrite_required: true,
};

const BASE_PROPS = {
  sandboxPreview: null,
  savedPreview: null,
  sandboxLoading: false,
  savedLoading: false,
  contactSelected: true,
};

// ── Empty states ───────────────────────────────────────────────────────────────

describe("LabPreviewPanel — empty states", () => {
  it("shows 'no contact selected' when contactSelected=false", () => {
    render(<LabPreviewPanel {...BASE_PROPS} contactSelected={false} />);
    expect(screen.getByText(/no contact selected/i)).toBeInTheDocument();
  });

  it("shows 'no preview yet' when contactSelected=true but no preview", () => {
    render(<LabPreviewPanel {...BASE_PROPS} />);
    expect(screen.getByText(/no preview yet/i)).toBeInTheDocument();
  });
});

// ── Loading states ─────────────────────────────────────────────────────────────

describe("LabPreviewPanel — loading", () => {
  it("shows skeletons while sandboxLoading", () => {
    render(<LabPreviewPanel {...BASE_PROPS} sandboxLoading />);
    // Skeleton renders divs; EmptyState should not appear
    expect(screen.queryByText(/no preview yet/i)).not.toBeInTheDocument();
  });
});

// ── Single column (no compare) ────────────────────────────────────────────────

describe("LabPreviewPanel — single column writer", () => {
  it("renders subject and body for writer preview with subject", () => {
    render(<LabPreviewPanel {...BASE_PROPS} sandboxPreview={WRITER_PREVIEW} />);
    expect(screen.getByText("Quick question")).toBeInTheDocument();
    expect(screen.getByText(/Hi Alice, I noticed/)).toBeInTheDocument();
  });

  it("renders body only when no subject", () => {
    render(
      <LabPreviewPanel {...BASE_PROPS} sandboxPreview={WRITER_PREVIEW_NO_SUBJECT} />
    );
    expect(screen.getByText(/following up/i)).toBeInTheDocument();
    expect(screen.queryByText(/subject/i)).not.toBeInTheDocument();
  });
});

// ── Compare mode (two columns) ────────────────────────────────────────────────

describe("LabPreviewPanel — compare mode", () => {
  const savedPreview: WriterPreview = {
    kind: "writer",
    body: "Hi Alice, old version.",
    subject: "Old subject",
  };

  it("shows Saved and Sandbox labels when savedPreview is present", () => {
    render(
      <LabPreviewPanel
        {...BASE_PROPS}
        sandboxPreview={WRITER_PREVIEW}
        savedPreview={savedPreview}
      />
    );
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("Sandbox")).toBeInTheDocument();
  });

  it("renders both preview bodies in compare mode", () => {
    render(
      <LabPreviewPanel
        {...BASE_PROPS}
        sandboxPreview={WRITER_PREVIEW}
        savedPreview={savedPreview}
      />
    );
    expect(screen.getByText(/Hi Alice, I noticed/)).toBeInTheDocument();
    expect(screen.getByText(/Hi Alice, old version/)).toBeInTheDocument();
  });

  it("shows saved loading skeleton in left column when savedLoading", () => {
    render(
      <LabPreviewPanel
        {...BASE_PROPS}
        sandboxPreview={WRITER_PREVIEW}
        savedPreview={savedPreview}
        savedLoading
      />
    );
    // Saved column should be loading, sandbox column should show body
    expect(screen.getByText(/Hi Alice, I noticed/)).toBeInTheDocument();
  });
});

// ── Critic preview ─────────────────────────────────────────────────────────────

describe("LabPreviewPanel — critic preview", () => {
  it("renders score, verdict, and feedback", () => {
    render(<LabPreviewPanel {...BASE_PROPS} sandboxPreview={CRITIC_PREVIEW} />);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("Too generic. Add a specific hook.")).toBeInTheDocument();
  });

  it("renders killed_by badges", () => {
    render(<LabPreviewPanel {...BASE_PROPS} sandboxPreview={CRITIC_PREVIEW} />);
    expect(screen.getByText("personalization")).toBeInTheDocument();
  });

  it("renders failed_soft_criteria badges", () => {
    render(<LabPreviewPanel {...BASE_PROPS} sandboxPreview={CRITIC_PREVIEW} />);
    expect(screen.getByText("specificity")).toBeInTheDocument();
  });

  it("shows PASS verdict with emerald Badge for passing critic", () => {
    const passing: CriticPreview = {
      kind: "critic",
      score: 7,
      verdict: "PASS",
      feedback: "Great email.",
      killed_by: [],
      failed_soft_criteria: [],
      rewrite_required: false,
    };
    render(<LabPreviewPanel {...BASE_PROPS} sandboxPreview={passing} />);
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });
});
