"""Structural validation for resume/data/*.json -- these are content files, not code, so the
tests check shape and the metric-conflict invariant rather than business logic."""

import json
from pathlib import Path

import resume_lint

_DATA_DIR = Path(__file__).resolve().parent.parent / "resume" / "data"


def _load(name):
    with open(_DATA_DIR / name) as f:
        return json.load(f)


def test_all_data_files_are_valid_json():
    for name in ("master.json", "metrics.json", "jargon.json", "projects.json",
                 "skills.json", "moments.json"):
        _load(name)  # raises if malformed


def test_metrics_have_required_fields():
    metrics = _load("metrics.json")
    assert len(metrics) > 0
    for m in metrics:
        assert set(m.keys()) >= {"id", "role", "text", "resolved", "conflicting_values"}
        assert isinstance(m["conflicting_values"], list)


def test_no_unresolved_metric_conflicts_remain():
    # The three metrics flagged resolved: null at seed time (2026-08-29) --
    # protium_vendor_cost_eliminated, protium_build_vs_buy_horizon,
    # product_analyst_ux_business_loss_prevented -- were resolved by the user the same day
    # ($20K/year, 3-year, $10K/month respectively). This test documents that resolution and
    # guards against a future edit silently reintroducing an unresolved conflict.
    metrics = _load("metrics.json")
    unresolved = [m for m in metrics if m["resolved"] is None]
    assert unresolved == []


def test_jargon_is_a_flat_banned_to_allowed_mapping():
    jargon = _load("jargon.json")
    assert len(jargon) >= 15
    for banned, allowed in jargon.items():
        assert isinstance(banned, str) and isinstance(allowed, str)


def test_projects_matrix_covers_every_role_type():
    projects = _load("projects.json")
    expected_role_types = {
        "ai_automation_ml", "pricing_strategy", "operations_manufacturing_cpg",
        "pure_data_analytics", "finance_investment_adjacent", "hardware_semiconductor_ip",
        "product_management_generalist", "consulting", "no_jd_generalist",
    }
    assert set(projects.keys()) == expected_role_types


def test_moments_bank_has_insight_and_used_for_fields():
    moments = _load("moments.json")
    assert len(moments) >= 8
    for m in moments:
        assert set(m.keys()) >= {"moment", "insight", "used_for"}


def test_skills_has_spine_pool_and_banned():
    skills = _load("skills.json")
    assert set(skills.keys()) == {"spine", "swap_pool", "banned", "flagged_unbacked"}
    assert "Tableau" in skills["banned"]


def test_no_metric_text_contains_known_jargon():
    # Regression test: a live --build run (job 41, 2026-08-29) discovered that
    # product_analyst_ux_emi_success's raw bullet text contained "NACH"/"e-mandate" verbatim
    # from the corpus spec, tripping resume_lint.check_jargon on every build that includes it.
    # This check would have caught it before the live run.
    metrics = _load("metrics.json")
    jargon = _load("jargon.json")
    for m in metrics:
        violations = resume_lint.check_jargon(m["text"], jargon)
        assert violations == [], f"{m['id']}: {violations}"
