# Every site: plan (2026-10-09)

Spec: docs/superpowers/specs/2026-10-09-every-site-design.md (advisor verdict: approve with changes; §7).

- [x] Measure the gap (Simplify feed, 3,100 postings: 41% on platforms with no filler).
- [ ] Live recon of real forms. Blocked in the cloud session (egress policy 403 on job hosts);
      `scripts/form_recon.py` runs on the Beelink (RUNBOOK section 12).
- [x] Spec + advisor review; blockers 1-5 resolved in the design before code.
- [x] Phase 1, one-page forms: `universal_filler.py` (contact_key, button_role, page_state, entry_control,
      allowed_host, SUBMIT_GUARD, View, application_target, final_control, contact_plan, file_targets),
      apply_agent preview/submit wiring, saved sessions with a domain filter, takeover for sign-in,
      config gating, Beelink units, tests (seven mutations caught).
- [ ] Watched preview + submit on the Beelink; grow APPLY_UNIVERSAL_PLATFORMS.
- [ ] Phase 2, multi-step: Next only with a visible step indicator showing a later step (or a recon-proven
      platform rule); ARMED + lease renewal before the first forward press in submit(); cross-step required
      gate; per-step heartbeat, deadline, yield to approved submits. Oracle/iCIMS email-code sign-in with
      ATS-only senders, no backward skew, one use per message.
- [ ] Separate specs after recon: LinkedIn external links (known-ATS URLs only), nav assist on the subscription.
