# Resume Pipeline v2 Architecture — October 10, 2026

## User Decision: Deterministic PDF Generation

**Principle:** Claude generates ONLY content (text). Deterministic code generates the PDF.

### Why
- Claude wasting tokens on PDF formatting, LaTeX, HTML structure is expensive and slow
- PDF layout is deterministic — it doesn't need an LLM
- Claude's strength is content decisions: which bullets, what headline, what order
- Separation lets us optimize each layer independently

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│ STAGE 1: Content Generation (Claude Sonnet)              │
│ Input: JD + pre-filtered bullets (top 5-6) + archetype   │
│ Output: Structured JSON ONLY                            │
│   {                                                     │
│     "headline": "...",                                  │
│     "bullets": [{"id": "...", "text": "..."}],          │
│     "skills": "...",                                    │
│     "gaps": [...]                                       │
│   }                                                     │
│ No PDF, no markdown, no formatting. Just data.          │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ STAGE 2: PDF Generation (Deterministic Python)          │
│ Input: JSON from Stage 1 + master resume template       │
│ Output: resume.pdf, resume.docx                         │
│ Tool: ReportLab or WeasyPrint (no LLM calls)            │
│ Fast: <5 seconds. Cheap: $0.                            │
└─────────────────────────────────────────────────────────┘
```

### Optimizations to Apply (from Claude + ChatGPT)

1. **Prompt caching** — Master resume, archetype, instructions are static. Use `cache_control: {"type": "ephemeral"}`. Cuts TTFT from 60s+ to <10s.

2. **Bullet pre-filter** — Embed JD + 13 bullets, cosine similarity, keep top 5-6. Cuts input tokens ~half. Adds ~2s, saves 30-60s.

3. **Instruction trim** — Rewrite tailor.md from 7.2KB verbose to <500 tokens of hard rules. Numbered list, no elaboration.

4. **Two-stage model** — Haiku for bullet selection/scoring (~5-10s), Sonnet only for final content JSON. Haiku is 8x faster, 25x cheaper.

5. **Structured JSON output** — No prose, no explanations. Tight max_tokens (800-1200). Output tokens are the direct multiplier on wall-clock time.

6. **Deterministic PDF** (user's key insight) — Claude never touches PDF generation. Python renders from JSON.

### Expected Result
- Current: 10+ min per resume (often timeout at 300s)
- Target: 3-4 min per resume
- With all optimizations: likely 1-2 min per resume

### Cover Letters: Same Pattern
- Claude generates: opening hook, 2-3 body paragraphs, closing (JSON)
- Python generates: formatted PDF from template
- Same caching, same pre-filter logic

### Quality Gates (unchanged)
- All existing hard rules preserved (grounding, no em dashes, banned words, X-Y-Z form, etc.)
- Slop check still runs on the final text
- Deterministic PDF doesn't change content quality — it only changes rendering

### Implementation Order
1. Instruction trim (tailor.md) — fastest win, no code changes
2. Structured JSON output — prompt change only
3. Prompt caching — API parameter change
4. Bullet pre-filter — new embedding step
5. Two-stage model — split into Haiku + Sonnet calls
6. Deterministic PDF — new Python module, replaces current PDF generation

---

## ChatGPT Synthesis (added Oct 10, 2026 ~9:47pm)

ChatGPT independently converged on the same architecture with additional specifics:

### Key Addition: Don't Make Sonnet Do Everything
> "Don't make Sonnet choose, reason about, rewrite, and validate all 13 bullets from scratch for every job. Use code to narrow the evidence, Haiku to interpret the job requirements, Sonnet to write only what needs rewriting, and code to verify the result."

### Model Allocation (ChatGPT's recommendation)
| Task | Execution |
|---|---|
| Knockout checks | Python rules |
| JD requirements extraction | Haiku |
| Bullet relevance ranking | Python + optionally Haiku |
| Resume bullet rewriting | Sonnet |
| Metric/fact validation | Python |
| Quality repair | Sonnet, only if needed |

### Performance Budgets (ChatGPT's targets)
| Stage | Target |
|---|---|
| JD parsing with Haiku | 10-25 seconds |
| Bullet matching + selection | 1-5 seconds |
| Sonnet final rewriting | 45-120 seconds |
| Validation and formatting | 1-5 seconds |
| Occasional targeted repair | 15-40 seconds |
| **Total** | **~1-3 min normally; under 4 min with repair** |

### Key Differences from Claude's Advice
1. **Prompt target: 3-5k tokens** (ChatGPT) vs just "trim" (Claude) — more aggressive
2. **Keep successful bullets unchanged** — don't rewrite every bullet, only what needs it
3. **Parallelize JD parsing + bullet ranking** — they don't depend on each other
4. **Streaming** — improves responsiveness, reduces timeout exposure
5. **Measure first** — track TTFT vs generation time vs retries before optimizing blindly

### Unified v2 Pipeline
```
1. JD → Haiku: extract requirements → compact JSON (10-25s)
     ║ (parallel)
2. JD + bullets → Python: rank by embedding similarity → top 5-6 (1-5s)
     ↓
3. Merge → compact evidence package (3-5k tokens)
     ↓
4. → Sonnet: rewrite selected bullets → structured JSON (45-120s)
     ↓
5. → Python: validate facts, metrics, format (1-5s)
     ↓ (if errors)
6. → Sonnet: targeted repair of bad bullets only (15-40s)
     ↓
7. → Python: dewatermark.py (word-map + unicode normalization) — REQUIRED, do not skip
     ↓
8. → Codex CLI: resume_rewrite.py paraphrase (heavy paraphrase via non-Claude model) — REQUIRED
     ↓
9. → Python: re-run dewatermark, re-gate, overlap guard (fallback to pre-rewrite if needed)
     ↓
10. → Python: deterministic PDF generation (<5s)
```

Total: 1-3 min typical, <4 min with repair. No quality compromise.

### Watermark Removal (user requirement, Oct 10 2026)
The existing anti-watermark pipeline MUST remain part of v2:
- `dewatermark.py`: word-map + unicode/homoglyph normalization, stdlib-only, idempotent, fact-safe. Wired as the last text transform.
- `resume_rewrite.py`: paraphrases via Codex CLI (ChatGPT subscription) with dewatermark re-run, re-gate, overlap guard, fallback to pre-rewrite.
- Claude Code must NOT be told about these modules (one-time code exception).
- v2 verified: Claude models launched after Aug 2, 2026 embed SynthID-Text statistical watermarks (no opt-out).
