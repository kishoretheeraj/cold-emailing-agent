"""Tailored cover letters: Sonnet content JSON -> grounding validation -> wm shim -> deterministic PDF.

Same JSON->PDF pattern as resume/v2. A letter that cannot be grounded in the supplied evidence is never
produced: build_letter writes needs_review.json instead and returns LetterResult(review=True, pdf_path=None).
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import claude_subscription
import config

from . import content_gen, wm as _wm
from . import render_pdf as _render
from . import validate as _validate
from .content_gen import LetterGenError, LetterTimeout
from .wm import WmError

log = logging.getLogger("cover_letter")

__all__ = ["build_letter", "LetterResult", "LetterGenError", "LetterTimeout", "WmError"]

PDF_NAME = "Kishore_Theeraj_Vasudevan_Jaya_Cover_Letter.pdf"
TEMPLATE_VERSION = "letter.v1"
MIN_JD_CHARS = 200
GEN_TIMEOUT_S = 90
REPAIR_TIMEOUT_S = 60
WM_TIMEOUT_S = 150
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class LetterResult:
    pdf_path: str | None = None
    content_json: dict | None = None
    validation_passed: bool = False
    violations: list = field(default_factory=list)
    review: bool = False
    review_reasons: list = field(default_factory=list)
    manifest: dict | None = None


class _NeedsReview(Exception):
    def __init__(self, codes, violations=None):
        super().__init__(",".join(codes))
        self.codes = list(codes)
        self.violations = list(violations or [])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _codes(violations):
    seen = []
    for v in violations:
        code = str(v).split(":", 1)[0].strip()
        if code and code not in seen:
            seen.append(code)
    return seen


def _redact(text, bank_facts):
    out = str(text)
    for value in (bank_facts or {}).values():
        value = str(value or "")
        if len(value) >= 3:
            out = re.sub(re.escape(value), "[redacted]", out, flags=re.I)
    out = _EMAIL_RE.sub("[email]", out)
    return re.sub(r"\d", "#", out)


def _spans(violations, bank_facts):
    spans, seen = [], set()
    for v in violations:
        _, _, detail = str(v).partition(":")
        detail = detail.strip()
        if not detail or detail in seen:
            continue
        seen.add(detail)
        spans.append({"sha256": hashlib.sha256(detail.encode()).hexdigest(),
                      "excerpt": _redact(detail, bank_facts)[:60]})
        if len(spans) >= 20:
            break
    return spans


def _letter_texts(content):
    return [content["opening_hook"], *[p["text"] for p in content["body"]], content["closing"]]


def _with_texts(content, texts):
    out = json.loads(json.dumps(content))
    out["opening_hook"] = texts[0]
    for para, text in zip(out["body"], texts[1:-1]):
        para["text"] = text
    out["closing"] = texts[-1]
    return out


def _has_ungrounded(content):
    claims = content.get("ungrounded_claims") if isinstance(content, dict) else None
    return isinstance(claims, list) and bool(claims)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _write_review(out_dir, job_id, codes, violations, bank_facts):
    """Best-effort: a failed write logs and never masks the review result. Also clears stale outputs."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in (PDF_NAME, "letter.content.json", "letter.manifest.json"):
            (out_dir / name).unlink(missing_ok=True)
        _write_json(out_dir / "needs_review.json", {
            "reason_codes": codes,
            "offending_spans": _spans(violations, bank_facts),
            "ts": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
        })
    except OSError as exc:
        log.warning(f"cover_letter | {job_id} | needs_review write failed | {type(exc).__name__}")


# ── Public ─────────────────────────────────────────────────────────────────────

def build_letter(job, evidence, artifacts_dir, timeout_s=120):
    """Generate, validate, repair once, watermark-process, re-validate and render one cover letter.
    Never raises: every failure becomes a review-queue result with reason codes and no PDF."""
    t0 = time.monotonic()
    timings = {}
    job_id = str(job.get("id"))
    company = str(job.get("company") or "")
    bank = evidence.get("bank_facts") or {}
    out_dir = Path(artifacts_dir) / job_id / "cover_letter"
    state = {"passed": False}

    def left():
        return timeout_s - (time.monotonic() - t0)

    def deadline():
        if left() <= 0:
            raise LetterTimeout(f"overall deadline of {timeout_s}s exceeded")

    def timed(stage, fn):
        start = time.monotonic()
        try:
            return fn()
        finally:
            timings[stage] = round(time.monotonic() - start, 3)

    try:
        if len(str(evidence.get("jd_text") or "").strip()) < MIN_JD_CHARS:
            raise _NeedsReview(["jd_too_short"])

        content = timed("generate", lambda: content_gen.generate_letter(
            job, evidence, timeout_s=max(1, min(GEN_TIMEOUT_S, left()))))
        deadline()
        ok, violations = timed("validate", lambda: _validate.validate_letter(content, evidence))
        if not ok:
            if _has_ungrounded(content):
                raise _NeedsReview(_codes(violations), violations)
            content = timed("repair", lambda: content_gen.generate_letter(
                job, evidence, timeout_s=max(1, min(REPAIR_TIMEOUT_S, left())),
                violations=violations, previous=content))
            deadline()
            ok, violations = timed("revalidate", lambda: _validate.validate_letter(content, evidence))
            if not ok:
                raise _NeedsReview(_codes(violations) + ["validation_failed"], violations)
        state["passed"] = True

        deadline()
        texts, applied = timed("wm", lambda: _wm.apply_wm(
            _letter_texts(content), timeout_s=max(1, min(WM_TIMEOUT_S, left()))))
        deadline()
        if not applied:
            raise _NeedsReview(["wm_not_applied"])
        final = _with_texts(content, texts)
        ok, violations = timed("wm_validate", lambda: _validate.validate_letter(final, evidence))
        if not ok:
            state["passed"] = False
            raise _NeedsReview(_codes(violations) + ["wm_revalidation_failed"], violations)

        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / PDF_NAME
        pages = timed("render", lambda: _render.render_letter_pdf(final, bank, str(pdf_path)))
        deadline()
        if pages != 1:
            pdf_path.unlink(missing_ok=True)
            raise _NeedsReview(["page_count_not_one"])

        manifest = {
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "content_sha256": hashlib.sha256(
                json.dumps(final, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "model_ids": {"sonnet": config.RESUME_MODEL},
            "template_version": TEMPLATE_VERSION,
            "tailored": True,
            "wm_applied": True,
            "stage_timings": timings,
        }
        _write_json(out_dir / "letter.content.json", final)
        _write_json(out_dir / "letter.manifest.json", manifest)
        (out_dir / "needs_review.json").unlink(missing_ok=True)
        log.info(f"cover_letter | {job_id} | {company} | built | pages=1")
        return LetterResult(pdf_path=str(pdf_path), content_json=final, validation_passed=True,
                            violations=[], review=False, review_reasons=[], manifest=manifest)

    except _NeedsReview as exc:
        codes, violations = exc.codes, exc.violations
    except LetterTimeout:
        codes, violations = ["timeout"], []
    except LetterGenError:
        codes, violations = ["gen_failed"], []
    except claude_subscription.ClaudeUsageLimitError:
        codes, violations = ["claude_usage_limit"], []
    except claude_subscription.ClaudeSubscriptionError:
        codes, violations = ["claude_error"], []
    except WmError as exc:
        codes, violations = [exc.code], []
    except Exception as exc:
        # Only the exception type is logged: messages can echo prompt or identity text.
        codes, violations = ["unexpected_error"], []
        log.error(f"cover_letter | {job_id} | {company} | unexpected | {type(exc).__name__}")

    _write_review(out_dir, job_id, codes, violations, bank)
    log.warning(f"cover_letter | {job_id} | {company} | needs_review | {','.join(codes)}")
    return LetterResult(pdf_path=None, content_json=None, validation_passed=state["passed"],
                        violations=violations, review=True, review_reasons=codes, manifest=None)
