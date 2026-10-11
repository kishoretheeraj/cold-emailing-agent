"""Dashboard API for the 24/7 job application system.

Provides approval queue and system status for the web dashboard.

State lives in memory for the MVP.
TODO: back DashboardStore with db.py (job_applications / application_runs).
Set DASHBOARD_API_TOKEN to require a bearer token on approve/reject;
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

    def __init__(self, machine: StateMachine | None = None) -> None:
        """Create an empty store; pass machine to share a StateMachine."""
        self.machine = machine or StateMachine()
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

    def report_worker(self, name: str, status: str) -> None:
        """Set a worker's status string (called by the worker heartbeats)."""
        self.worker_status[name] = status

    def state_of(self, application_id: str) -> ApplicationState:
        """Current state of a recorded application."""
        return self.machine.get_state(application_id)


STORE = DashboardStore()


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


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Enforce the bearer token on mutating routes when DASHBOARD_API_TOKEN is set."""
    expected = os.environ.get("DASHBOARD_API_TOKEN")
    if not expected:
        return
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


@app.get("/api/queue")
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


def _transition(application_id: str, to_state: ApplicationState, reason: str) -> None:
    if application_id not in STORE.records:
        raise HTTPException(status_code=404, detail="unknown application")
    if STORE.state_of(application_id) is not S.AWAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="application is not awaiting approval")
    try:
        STORE.machine.transition(application_id, to_state, reason)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info(f"[DASHBOARD] {application_id} | {to_state.value}")


@app.post("/api/approve/{application_id}", dependencies=[Depends(require_token)])
def approve(application_id: str) -> dict:
    """Approve an application: AWAITING_APPROVAL -> APPLYING (the worker resumes it)."""
    _transition(application_id, S.APPLYING, "approved via dashboard")
    return {"approved": True}


@app.post("/api/reject/{application_id}", dependencies=[Depends(require_token)])
def reject(application_id: str, body: RejectBody) -> dict:
    """Reject an application: AWAITING_APPROVAL -> BLOCKED, reason kept in the audit log."""
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required")
    _transition(application_id, S.BLOCKED, f"rejected via dashboard: {body.reason.strip()}")
    return {"rejected": True}


@app.get("/api/status")
def status() -> dict:
    """Worker health, approval queue depth and Claude status."""
    depth = sum(1 for a in STORE.records if STORE.state_of(a) is S.AWAITING_APPROVAL)
    return {
        "workers": {w: STORE.worker_status.get(w, "unknown") for w in WORKERS},
        "queue_depth": depth,
        "claude_status": STORE.claude_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/metrics")
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
