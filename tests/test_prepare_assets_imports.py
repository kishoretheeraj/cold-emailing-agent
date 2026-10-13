"""R6: prepare_assets covers its lazy imports with the same handler as the build body.

playwright is stubbed because apply_pipeline imports it at module level; the stub and the module are
removed again afterwards so other test modules are unaffected.
"""
import builtins
import importlib
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def pipeline(monkeypatch):
    had = "apply_pipeline" in sys.modules
    if not had:
        monkeypatch.setitem(sys.modules, "playwright", sys.modules.get("playwright") or MagicMock())
        sync = sys.modules.get("playwright.sync_api") or MagicMock()
        monkeypatch.setitem(sys.modules, "playwright.sync_api", sync)
    mod = importlib.import_module("apply_pipeline")
    monkeypatch.setattr(mod.config, "APPLY_RESUME_V2", True, raising=False)
    monkeypatch.setattr(mod, "ev", lambda *a, **k: None)
    yield mod
    if not had:
        sys.modules.pop("apply_pipeline", None)


JOB = {"id": "job-1", "description": "x" * 400}


def _break_import(monkeypatch, exc):
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "resume.v2" and fromlist and "build" in fromlist:
            raise exc
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_import_error_maps_to_unavailable(pipeline, monkeypatch):
    _break_import(monkeypatch, ImportError("missing dependency"))
    with pytest.raises(pipeline.JobEnd) as exc:
        pipeline.prepare_assets(JOB, "attempt-1")
    assert exc.value.detail == "resume_build_failed:unavailable"


def test_non_import_error_during_import_maps_to_unexpected(pipeline, monkeypatch):
    _break_import(monkeypatch, RuntimeError("module blew up at import"))
    with pytest.raises(pipeline.JobEnd) as exc:
        pipeline.prepare_assets(JOB, "attempt-1")  # used to escape as a raw RuntimeError
    assert exc.value.detail == "resume_build_failed:unexpected"


def test_build_error_maps_to_its_stage(pipeline, monkeypatch):
    from resume.v2 import build as v2build

    def boom(*a, **k):
        raise v2build.AssetBuildError("wm_regate", "headline_ungrounded")

    monkeypatch.setattr(v2build, "build_assets", boom)
    with pytest.raises(pipeline.JobEnd) as exc:
        pipeline.prepare_assets(JOB, "attempt-1")
    assert exc.value.detail == "resume_build_failed:wm_regate"


def test_disabled_flag_returns_none(pipeline, monkeypatch):
    monkeypatch.setattr(pipeline.config, "APPLY_RESUME_V2", False, raising=False)
    assert pipeline.prepare_assets(JOB, "attempt-1") is None
