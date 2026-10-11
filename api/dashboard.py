"""Dashboard API for the 24/7 job application system.

Provides approval queue and system status for the web dashboard.

State is an in-memory read cache. Approve/reject are written through db.py (application_runs, plus a
withdrawn stage on reject) BEFORE the in-memory transition; a failed write answers 503 and changes
nothing. A dashboard approval is intent only: Submit still needs the signed approve_application RPC.
DASHBOARD_API_TOKEN is required: every route except /api/health fails closed (403) while it is unset
and answers 401 unless the request carries the matching bearer token.
DASHBOARD_ORIGINS (comma-separated) sets the allowed CORS origins.
"""
import hmac
import logging
import os
import threading
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ops.state_machine import ApplicationState, InvalidTransition, StateMachine

log = logging.getLogger(__name__)

__version__ = "0.1.0"

S = ApplicationState
WORKERS = ("discovery", "screening", "tailoring", "apply")

# Every application passes through these on its way to APPLYING.
_ENTRY_PATH = [
    S.DISCOVERED, S.NORMALIZED, S.SCREENED, S.ELIGIBLE,
    S.TAILOR_PENDING, S.RESUME_READY, S.APPLY_READY, S.APPLYING,
]


# ── Store ──────────────────────────────────────────────────────────────────────

class DashboardStore:
    """In-memory application records plus a StateMachine for transitions."""

    def __init__(self, machine: StateMachine | None = None, persister=None) -> None:
        """Create an empty store; pass machine to share a StateMachine, persister (save/load) to persist decisions."""
        self.machine = machine or StateMachine()
        self.persister = persister
        self.decisions: dict[str, tuple] = {}
        self.records: dict[str, dict] = {}
        self.worker_status: dict[str, str] = {}
        self.claude_status = "unknown"
        self._lock = threading.RLock()

    def add_application(
        self,
        application_id: str,
        job_title: str,
        company: str,
        ats_provider: str,
        resume_path: str = "",
        state: ApplicationState = S.AWAITING_APPROVAL,
        tailor_seconds: float | None = None,
    ) -> None:
        """Record an application and walk it through the state machine to state."""
        with self._lock:
            for step in _ENTRY_PATH:
                self.machine.transition(application_id, step, "seeded")
                if step is state:
                    break
            else:
                self.machine.transition(application_id, state, "seeded")
            self.records[application_id] = {
                "application_id": application_id,
                "job_title": job_title,
                "company": company,
                "ats_provider": ats_provider,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "resume_path": resume_path,
                "tailor_seconds": tailor_seconds,
            }
            self._apply_decision(application_id)

    def persist(self, application_id: str, decision: str, reason: str | None = None) -> None:
        """Write a decision through the persister (raises on failure) and remember it."""
        if self.persister is not None:
            self.persister.save(application_id, decision, reason)
        self.decisions[application_id] = (decision, reason)

    def load_decisions(self) -> None:
        """Reload persisted decisions (after a restart) and apply them to records awaiting approval."""
        if self.persister is None:
            return
        with self._lock:
            self.decisions = dict(self.persister.load())
            for application_id in list(self.records):
                self._apply_decision(application_id)

    def _apply_decision(self, application_id: str) -> None:
        decision = self.decisions.get(application_id)
        if not decision or self.machine.get_state(application_id) is not S.AWAITING_APPROVAL:
            return
        kind = decision[0]
        self.machine.transition(
            application_id, S.APPLYING if kind == "approved" else S.BLOCKED, f"restored persisted decision: {kind}")

    def report_worker(self, name: str, status: str) -> None:
        """Set a worker's status string (called by the worker heartbeats)."""
        self.worker_status[name] = status

    def state_of(self, application_id: str) -> ApplicationState:
        """Current state of a recorded application."""
        return self.machine.get_state(application_id)


class _DbPersister:
    """Writes dashboard decisions through db.py, imported lazily so importing this module needs no secrets."""

    def save(self, application_id, decision, reason=None):
        import db
        db.record_dashboard_decision(application_id, decision, reason)

    def load(self):
        import db
        return db.get_dashboard_decisions()


STORE = DashboardStore(persister=_DbPersister())


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Job Agent 24/7 API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.environ.get("DASHBOARD_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.on_event("startup")
def _restore_decisions() -> None:
    try:
        STORE.load_decisions()
    except Exception as exc:
        log.warning(f"[DASHBOARD] could not restore persisted decisions: {exc}")


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Fail-closed bearer token: 403 when DASHBOARD_API_TOKEN is unset, 401 on a missing or wrong token."""
    expected = os.environ.get("DASHBOARD_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=403, detail="dashboard auth is not configured")
    supplied = (authorization or "").encode()
    if not hmac.compare_digest(supplied, f"Bearer {expected}".encode()):
        raise HTTPException(status_code=401, detail="invalid or missing token")


class RejectBody(BaseModel):
    """Body of POST /api/reject."""

    reason: str = Field(min_length=1, max_length=500)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "version": __version__}


@app.get("/api/queue", dependencies=[Depends(require_token)])
def queue(state: str = Query(default="awaiting_approval")) -> list[dict]:
    """List applications currently in the given state (default awaiting_approval)."""
    try:
        wanted = ApplicationState(state.lower())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown state: {state}")
    items = [
        {k: rec[k] for k in (
            "application_id", "job_title", "company", "ats_provider", "created_at", "resume_path"
        )}
        for app_id, rec in STORE.records.items()
        if STORE.state_of(app_id) is wanted
    ]
    return sorted(items, key=lambda r: r["created_at"])


def _transition(application_id: str, to_state: ApplicationState, reason: str, decision: str, detail: str | None = None) -> None:
    if application_id not in STORE.records:
        raise HTTPException(status_code=404, detail="unknown application")
    if STORE.state_of(application_id) is not S.AWAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="application is not awaiting approval")
    try:
        STORE.persist(application_id, decision, detail)
    except Exception as exc:
        log.error(f"[DASHBOARD] {application_id} | persist failed: {exc}")
        raise HTTPException(status_code=503, detail="could not persist the decision; nothing changed")
    try:
        STORE.machine.transition(application_id, to_state, reason)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info(f"[DASHBOARD] {application_id} | {to_state.value}")


@app.post("/api/approve/{application_id}", dependencies=[Depends(require_token)])
def approve(application_id: str) -> dict:
    """Approve an application: AWAITING_APPROVAL -> APPLYING (the worker resumes it)."""
    _transition(application_id, S.APPLYING, "approved via dashboard", "approved")
    return {"approved": True}


@app.post("/api/reject/{application_id}", dependencies=[Depends(require_token)])
def reject(application_id: str, body: RejectBody) -> dict:
    """Reject an application: AWAITING_APPROVAL -> BLOCKED, reason kept in the audit log."""
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required")
    _transition(application_id, S.BLOCKED, f"rejected via dashboard: {body.reason.strip()}",
                "rejected", body.reason.strip())
    return {"rejected": True}


@app.get("/api/status", dependencies=[Depends(require_token)])
def status() -> dict:
    """Worker health, approval queue depth and Claude status."""
    depth = sum(1 for a in STORE.records if STORE.state_of(a) is S.AWAITING_APPROVAL)
    return {
        "workers": {w: STORE.worker_status.get(w, "unknown") for w in WORKERS},
        "queue_depth": depth,
        "claude_status": STORE.claude_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/metrics", dependencies=[Depends(require_token)])
def metrics() -> dict:
    """Submission statistics. success_rate and avg_tailor_seconds are null with no data."""
    today = datetime.now(timezone.utc).date().isoformat()
    submitted_today = submitted_total = failed = 0
    by_ats: dict[str, int] = {}
    tailor = []
    for app_id, rec in STORE.records.items():
        state = STORE.state_of(app_id)
        if rec.get("tailor_seconds") is not None:
            tailor.append(rec["tailor_seconds"])
        if state is S.SUBMITTED:
            submitted_total += 1
            by_ats[rec["ats_provider"]] = by_ats.get(rec["ats_provider"], 0) + 1
            if STORE.machine.get_history(app_id)[-1]["timestamp"][:10] == today:
                submitted_today += 1
        elif state in (S.BLOCKED, S.UNKNOWN_SUBMISSION):
            failed += 1
    attempts = submitted_total + failed
    return {
        "submitted_today": submitted_today,
        "submitted_total": submitted_total,
        "by_ats": by_ats,
        "success_rate": submitted_total / attempts if attempts else None,
        "avg_tailor_seconds": sum(tailor) / len(tailor) if tailor else None,
    }
