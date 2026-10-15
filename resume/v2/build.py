"""Resume v2 asset build: the full pre-browser pipeline for one job.

requirements (Haiku) -> rank (Python) -> evidence -> generate (Sonnet) -> validate -> repair ->
anti-watermark (external shim) -> headline grounding + re-validate -> render -> manifest. The cover letter
builds in parallel from the shared evidence package; its failure never fails the resume. Every stage has
its own timeout and the whole build has a hard deadline; each timeout or failure raises AssetBuildError,
and a failed build never leaves a PDF behind in the bundle.

Timeouts: stage timeouts bound how long build_assets WAITS for a stage (future.result(timeout)). A
timed-out thread may still be running, but its result is discarded. Publication is atomic with respect to
failure: every worker (resume render, cover letter) writes only inside an attempt-specific temp directory
that is removed in a finally block, and the main thread alone publishes into the real artifacts directory,
after the last deadline check (which comes after the letter wait). Nothing reaches the artifacts directory
on a failure or timeout, and a worker that outlives its timeout can only write into the removed temp tree.
The deadline is also checked before every stage is submitted. The total is capped at 360s regardless of
configuration.

Errors: AssetBuildError reasons carry the stage, the exception class name and fixed codes only, never an
exception message (those can hold names, phone numbers or resume text), and are raised without chaining.

Cache: key = (master sha, template version, prompt version, normalized JD sha, model ids). A hit reuses
the stored post-watermark content and only re-renders; a cached headline that claims seniority no source
role has is treated as a miss.
All model calls go through the Claude subscription (resume.v2.llm).
"""

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path

import config
from resume.v2 import cache, llm, ranker, renderer, tailor_v2, validator, wm_stage
from resume.v2 import evidence as evidence_mod
from resume.v2 import manifest as manifest_mod
from resume.v2 import requirements as requirements_mod

log = logging.getLogger("resume_v2")

PROMPT_VERSION = "resume.v2.prompt/1"
TEMPLATE_VERSION = "resume.v2.template/1"
STAGE_TIMEOUTS = {"requirements": 40, "rank": 15, "generate": 150, "validate": 10,
                  "repair": 60, "wm": 150, "render": 15}
TOTAL_TIMEOUT = min(getattr(config, "RESUME_V2_TOTAL_TIMEOUT_S", 360), 360)
LETTER_TIMEOUT = 120
MAX_REPAIRS = tailor_v2.MAX_REPAIRS
PDF_NAME = "Kishore_Theeraj_Vasudevan_Jaya_Resume.pdf"
# A seniority word in the final headline must appear in at least one SOURCE role (never the target title).
_SENIORITY = frozenset({"senior", "sr", "staff", "principal", "lead", "head", "director", "vp", "chief"})


class AssetBuildError(Exception):
    """A stage failed or timed out. .stage names it (a stage name, "timeout", "lease", "wm_regate", "input")."""

    def __init__(self, stage, reason, violations=None):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason = stage, reason
        self.violations = list(violations or [])


@dataclass
class AssetBundle:
    job_id: str
    resume_pdf: str | None = None
    resume_content: dict | None = None
    resume_manifest: dict | None = None
    letter: object | None = None  # cover_letter.LetterResult
    evidence: dict | None = None
    stage_timings: dict = field(default_factory=dict)
    cache_hit: bool = False
    letter_review_reasons: list = field(default_factory=list)


# ── Stage runner ───────────────────────────────────────────────────────────────

class _Stages:
    """Runs stage functions on a pool so a stage that hangs cannot outlive its timeout or the deadline."""

    def __init__(self, pool, deadline):
        self.pool, self.deadline = pool, deadline
        self._timings = {}
        self._lock = threading.Lock()

    def remaining(self):
        return self.deadline - time.monotonic()

    def check(self, where):
        if self.remaining() <= 0:
            raise AssetBuildError("timeout", f"total deadline of {TOTAL_TIMEOUT}s exceeded before {where}")

    def snapshot(self):
        with self._lock:
            return dict(self._timings)

    def submit(self, stage, fn, *args):
        self.check(stage)

        def run():
            t0 = time.monotonic()
            try:
                return fn(*args)
            finally:
                with self._lock:
                    self._timings[stage] = round(self._timings.get(stage, 0.0) + time.monotonic() - t0, 3)

        return self.pool.submit(run)

    def wait(self, stage, fut, limit=None):
        budget = min(STAGE_TIMEOUTS[stage] if limit is None else limit, self.remaining())
        if budget <= 0:
            fut.cancel()
            raise AssetBuildError("timeout", f"total deadline of {TOTAL_TIMEOUT}s exceeded during {stage}")
        try:
            return fut.result(timeout=budget)
        except FutureTimeout as exc:
            if not fut.done():
                fut.cancel()
                raise AssetBuildError("timeout", f"{stage} exceeded {budget:.0f}s") from None
            raise _stage_error(stage, exc) from None
        except AssetBuildError:
            raise
        except Exception as exc:
            raise _stage_error(stage, exc) from None

    def call(self, stage, fn, *args):
        return self.wait(stage, self.submit(stage, fn, *args))


def _stage_error(stage, exc):
    if isinstance(exc, wm_stage.WmStageError):
        return AssetBuildError(stage, f"WmStageError:{exc}")  # that module's messages are reason codes only
    return AssetBuildError(stage, f"{type(exc).__name__} raised")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _jd_sha(jd_text):
    return hashlib.sha256(" ".join((jd_text or "").split()).lower().encode("utf-8")).hexdigest()


def _model_ids():
    return {"haiku": llm.HAIKU_MODEL, "sonnet": config.RESUME_MODEL}


def _ranker_backend():
    return os.environ.get("RESUME_V2_RANKER_BACKEND", "auto")


def _safe_id(job):
    jid = str(job.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", jid) or ".." in jid:
        raise AssetBuildError("input", "job id is not path-safe")
    return jid


def _renew(renew_lease):
    if renew_lease is None:
        return
    try:
        renew_lease()
    except Exception as exc:
        raise AssetBuildError("lease", f"lease renewal failed: {type(exc).__name__}") from None


def _flatten(content):
    texts = [content["headline"], content["summary"]]
    for entry in content["experience"]:
        texts.extend(entry["bullets"])
    texts.extend(content["skills"])
    return texts


def _unflatten(content, texts):
    it = iter(texts)
    headline, summary = next(it), next(it)
    experience = [{"role": e["role"], "bullets": [next(it) for _ in e["bullets"]]}
                  for e in content["experience"]]
    skills = [next(it) for _ in content["skills"]]
    return {"headline": headline, "summary": summary, "experience": experience, "skills": skills}


def _headline_grounded(headline, candidate_profile, requirements=None):
    """True when every seniority word in the headline appears in a SOURCE role from the candidate profile.
    The target role title is deliberately not a source (requirements is accepted and ignored): a posting
    for a Senior role must not let a headline claim seniority the candidate's own roles lack."""
    roles = [str(b.get("role", "")).lower() for b in (candidate_profile or {}).get("bullets") or []
             if isinstance(b, dict)]
    source_words = set(re.findall(r"[a-z]+", " ".join(roles)))
    claimed = set(re.findall(r"[a-z]+", str(headline).lower())) & _SENIORITY
    return claimed <= source_words


def _load_hit(key, cache_root, profile):
    """(requirements, ranked, content) from a valid cache entry, else None."""
    try:
        payload = cache.load_cached(key, cache_root)
        if not payload:
            return None
        content, reqs, ranked = payload.get("content"), payload.get("requirements"), payload.get("ranked")
        if not (isinstance(content, dict) and isinstance(reqs, dict) and isinstance(ranked, list)):
            return None
        ok, _ = validator.validate_resume(content, profile)
        if not ok:
            return None
        if not _headline_grounded(content.get("headline", ""), profile, reqs):
            log.warning("resume_v2 | cache | entry ignored | headline_ungrounded")
            return None
        return reqs, ranked, content
    except Exception as exc:
        log.warning(f"resume_v2 | cache | unreadable entry ignored | {type(exc).__name__}")
        return None


def _write_bytes(path, data, mode=0o600):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── Cover letter (parallel, never fatal) ───────────────────────────────────────

def _load_letter_builder():
    from cover_letter import build_letter
    return build_letter


def _letter_task(job, ev, attempt_dir):
    try:
        build_letter = _load_letter_builder()
    except ImportError:
        return None, ["letter_unavailable"]
    try:
        result = build_letter(job, ev, attempt_dir)
    except Exception as exc:
        return None, [f"letter_error:{type(exc).__name__}"]
    return result, list(getattr(result, "review_reasons", None) or [])


def _collect_letter(fut, stages):
    if fut is None:
        return None, ["letter_disabled"]
    budget = max(0.0, min(LETTER_TIMEOUT, stages.remaining()))
    try:
        return fut.result(timeout=budget)
    except FutureTimeout:
        fut.cancel()
        return None, ["letter_timeout"]
    except Exception as exc:
        return None, [f"letter_error:{type(exc).__name__}"]


def _relocate_letter(letter, attempt_dir, root):
    """The letter result points into the attempt dir; return a copy pointing at the published location."""
    path = getattr(letter, "pdf_path", None)
    if letter is None or not isinstance(path, str):
        return letter
    try:
        rel = Path(path).relative_to(attempt_dir)
    except ValueError:
        return letter
    moved = copy.copy(letter)
    moved.pdf_path = str(Path(root) / rel)
    return moved


def _discard(paths, letter_dest):
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    if letter_dest is not None:
        shutil.rmtree(letter_dest, ignore_errors=True)


# ── Content stages ─────────────────────────────────────────────────────────────

def _validated_content(stages, reqs, ranked, profile):
    content = stages.call("generate", tailor_v2.generate_content, reqs, ranked)
    ok, viol = stages.call("validate", validator.validate_resume, content, profile)
    repairs = 0
    while not ok and repairs < MAX_REPAIRS:
        repairs += 1
        content = stages.call("repair", tailor_v2.repair_content, content, viol, ranked, reqs)
        ok, viol = stages.call("validate", validator.validate_resume, content, profile)
    if not ok:
        raise AssetBuildError("validate", f"{len(viol)} violations after {repairs} repairs", viol)
    return content


def _apply_wm(stages, content, profile, reqs):
    texts = _flatten(content)
    out, meta = stages.call("wm", wm_stage.run_anti_watermark, texts, max(1, STAGE_TIMEOUTS["wm"] - 5))
    if not (isinstance(meta, dict) and meta.get("wm_applied")) or len(out) != len(texts):
        raise AssetBuildError("wm", "wm_not_applied")
    shimmed = _unflatten(content, out)
    # The external stage rewrites text after validation: its headline may not claim seniority the sources lack.
    if not _headline_grounded(shimmed["headline"], profile, reqs):
        raise AssetBuildError("wm_regate", "headline_ungrounded", ["headline_ungrounded"])
    return shimmed


def _watermarked_content(stages, content, reqs, ranked, profile):
    content = _apply_wm(stages, content, profile, reqs)
    ok, viol = stages.call("validate", validator.validate_resume, content, profile)
    if ok:
        return content
    content = stages.call("repair", tailor_v2.repair_content, content, viol, ranked, reqs)
    ok, viol = stages.call("validate", validator.validate_resume, content, profile)
    if not ok:
        raise AssetBuildError("wm_regate", f"{len(viol)} violations after repair", viol)
    content = _apply_wm(stages, content, profile, reqs)  # repaired text came from the model: it goes through the stage too
    ok, viol = stages.call("validate", validator.validate_resume, content, profile)
    if not ok:
        raise AssetBuildError("wm_regate", f"{len(viol)} violations after re-watermark", viol)
    return content


def _render(stages, content, profile, attempt_dir):
    """Render inside the attempt dir; returns (final content, pdf bytes). Never touches the artifacts dir."""
    final = {**content, "name": profile.get("name", ""), "contact": profile.get("contact") or {},
             "education": tailor_v2.education_lines(profile.get("education"))}
    tmp = Path(attempt_dir) / "resume.tmp.pdf"
    pages = stages.call("render", renderer.render_pdf, final, str(tmp))
    if pages != 1:
        raise AssetBuildError("render", f"rendered {pages} pages, expected 1")
    try:
        data = tmp.read_bytes()
    except OSError:
        raise AssetBuildError("render", "render_output_missing") from None
    return final, data


# ── Orchestration ──────────────────────────────────────────────────────────────

def _build(job, job_id, jd_text, profile, bank_facts, root, cache_root, master_md_sha, renew_lease, stages,
           attempt_dir):
    bullets = profile.get("bullets") or []
    if not bullets:
        raise AssetBuildError("input", "candidate profile has no bullets")
    if not (jd_text or "").strip():
        raise AssetBuildError("input", "empty job description")

    key = cache.cache_key(master_md_sha, TEMPLATE_VERSION, PROMPT_VERSION, _jd_sha(jd_text), _model_ids())
    out_dir = root / job_id / "resume"
    hit = _load_hit(key, cache_root, profile)
    if hit:
        reqs, ranked, content = hit
    else:
        reqs = stages.call("requirements", requirements_mod.extract_requirements, jd_text)
        ranked = stages.call("rank", lambda: ranker.rank_bullets(reqs, bullets, backend=_ranker_backend()))
        _renew(renew_lease)

    ev = evidence_mod.build_evidence(job, jd_text, reqs, ranked, bank_facts)
    letter_fut = None
    if getattr(config, "APPLY_COVER_LETTER", True):
        # The letter writes its <job_id>/cover_letter tree under the attempt dir, never the real artifacts dir.
        letter_fut = stages.submit("letter", _letter_task, job, copy.deepcopy(ev), str(attempt_dir))

    if not hit:
        content = _validated_content(stages, reqs, ranked, profile)
        content = _watermarked_content(stages, content, reqs, ranked, profile)
        _renew(renew_lease)

    final, pdf_bytes = _render(stages, content, profile, attempt_dir)
    letter, reasons = _collect_letter(letter_fut, stages)
    # The letter wait may have consumed the remaining budget: nothing is published past the deadline.
    if stages.remaining() <= 0:
        raise AssetBuildError("timeout", "deadline_exceeded", ["deadline_exceeded"])

    pdf_path = out_dir / PDF_NAME
    published, letter_dest = [], None
    try:
        if not hit:
            try:
                cache.save_cached(key, {"content": content, "requirements": reqs, "ranked": ranked}, cache_root)
            except Exception as exc:
                log.warning(f"resume_v2 | cache | save failed | {type(exc).__name__}")

        out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        published.append(pdf_path)
        _write_bytes(pdf_path, pdf_bytes)
        published.append(out_dir / "jd.txt")
        cache.atomic_write_text(out_dir / "jd.txt", jd_text)
        published.append(out_dir / "resume.content.json")
        cache.atomic_write_json(out_dir / "resume.content.json", final)

        letter_src = Path(attempt_dir) / job_id / "cover_letter"
        if letter is not None and letter_src.is_dir():
            letter_dest = root / job_id / "cover_letter"
            letter_dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.rmtree(letter_dest, ignore_errors=True)  # replace, so no stale output from an older build survives
            shutil.copytree(letter_src, letter_dest, dirs_exist_ok=True)
            letter = _relocate_letter(letter, Path(attempt_dir), root)

        stages.check("manifest write")
        published.append(out_dir / "resume.manifest.json")
        manifest = manifest_mod.write_manifest(out_dir / "resume.manifest.json", {
            "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "content_sha256": hashlib.sha256(
                json.dumps(final, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "cache_key": key,
            "model_ids": _model_ids(),
            "template_version": TEMPLATE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "stage_timings": stages.snapshot(),
            "tailored": True,
            "wm_applied": True,
            "cache_hit": bool(hit),
        })
        stages.check("return")
    except BaseException:
        _discard(published, letter_dest)  # a failed build never leaves a PDF (or manifest) behind
        raise
    return AssetBundle(job_id=job_id, resume_pdf=str(pdf_path), resume_content=final, resume_manifest=manifest,
                       letter=letter, evidence=ev, cache_hit=bool(hit), letter_review_reasons=reasons)


def build_assets(job, jd_text, candidate_profile, bank_facts, artifacts_root,
                 cache_root=None, master_md_sha="", renew_lease=None):
    """Build the tailored resume (and start the cover letter) for one job. Returns an AssetBundle.
    Raises AssetBuildError on any failure or timeout; the resume is never partially returned.
    renew_lease is called after the requirements/rank stages and after the content stages; any exception
    from it means the claim is lost and raises AssetBuildError("lease")."""
    started = time.monotonic()
    job_id = _safe_id(job)
    attempt_dir = Path(tempfile.mkdtemp(prefix="resumev2-"))
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="resume_v2")
    stages = _Stages(pool, started + TOTAL_TIMEOUT)
    try:
        bundle = _build(job, job_id, jd_text, candidate_profile, bank_facts, Path(artifacts_root),
                        cache_root, master_md_sha, renew_lease, stages, attempt_dir)
    except AssetBuildError as exc:
        log.warning(f"resume_v2 | build | {job_id} | failed | stage={exc.stage} | {exc.reason[:120]}")
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(attempt_dir, ignore_errors=True)
    bundle.stage_timings = {**stages.snapshot(), "total": round(time.monotonic() - started, 3)}
    log.info(f"resume_v2 | build | {job_id} | done | cache_hit={int(bundle.cache_hit)} | "
             f"timings={json.dumps(bundle.stage_timings, sort_keys=True)}")
    return bundle
