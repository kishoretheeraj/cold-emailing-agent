"""
Read-only reconnaissance of real application pages (spec 2026-10-09 §3.5). For each posting URL it
opens the page, presses only an entry control ("Apply", "Apply now", ...; never a third-party
sign-in), and records how the application is built: page state, field labels/kinds/required flags,
the contact key each field maps to, and the role of every visible button. It never types, never
uploads, never presses Next or Submit, and never records a field's value.

Run where job sites are reachable (the Beelink), as the jobagent user:

  python scripts/form_recon.py --urls urls.txt --out recon.json
  python scripts/form_recon.py --from-feed 5 --out recon.json   # 5 postings per platform from Simplify

The JSON is a structure report for writing site adapters; it holds no personal data.
"""

import argparse
import collections
import json
import random
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import job_identity  # noqa: E402
import universal_filler  # noqa: E402

_BUTTONS_JS = r"""() => Array.from(document.querySelectorAll(
    'button, a[href], input[type=submit], input[type=button], [role=button]'))
  .filter((el) => !!(el.offsetParent || el.getClientRects().length))
  .map((el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim())
  .filter((t) => t && t.length <= 60)"""


def summarize(url, state, inventory, buttons, frames):
    """The structure report for one page: never a value, only labels, kinds and roles."""
    fields = [{"label": (f.get("label") or "")[:80], "kind": f.get("kind"), "required": bool(f.get("required")),
               "contact": universal_filler.contact_key(f), "options": len(f.get("options") or [])}
              for f in (inventory or [])]
    on_form = state == "form"
    roles = collections.Counter(universal_filler.button_role(b, on_form=on_form) or "other" for b in buttons)
    return {"host": urlparse(url).hostname, "state": state, "fields": fields,
            "required": sum(1 for f in fields if f["required"]),
            "buttons": sorted({b for b in buttons if universal_filler.button_role(b, on_form=on_form)})[:20],
            "button_roles": dict(roles), "frames": frames}


def _snapshot(page):
    import apply_agent
    inventory = apply_agent._form_inventory(page) or []
    try:
        buttons = page.evaluate(_BUTTONS_JS) or []
    except Exception:
        buttons = []
    frames = sorted({urlparse(f.url).hostname for f in page.frames if f.url.startswith("http")}
                    - {urlparse(page.url).hostname})
    return summarize(page.url, universal_filler.page_state(page), inventory, buttons, frames)


def recon(url, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    report = {"platform": job_identity.identify(url)["platform"]}
    try:
        page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        page.wait_for_timeout(4_000)
        report["landing"] = _snapshot(page)
        entry = universal_filler.entry_control(page)
        if entry is not None:
            pages_before = len(context.pages)
            entry.click(timeout=8_000)
            page.wait_for_timeout(5_000)
            target = context.pages[-1] if len(context.pages) > pages_before else page
            report["after_entry"] = _snapshot(target)
    except Exception as exc:
        report["error"] = str(exc)[:200]
    finally:
        context.close()
    return report


def _feed_sample(per_platform):
    with urllib.request.urlopen(config.SOURCING_SIMPLIFY_URL, timeout=60) as resp:
        rows = [r for r in json.load(resp) if r.get("active") and r.get("is_visible", True)]
    random.shuffle(rows)
    picked, counts = [], collections.Counter()
    for row in rows:
        platform = job_identity.identify(row.get("url", ""))["platform"]
        if platform in ("greenhouse", "ashby", "lever", "workday"):
            continue
        key = platform if platform != "generic" else ".".join((urlparse(row["url"]).hostname or "").split(".")[-2:])
        if counts[key] < per_platform:
            counts[key] += 1
            picked.append(row["url"])
    return picked


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--urls", help="file with one posting URL per line")
    parser.add_argument("--from-feed", type=int, default=0, help="sample N postings per platform from Simplify")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    urls = [u.strip() for u in Path(args.urls).read_text().splitlines() if u.strip()] if args.urls else []
    if args.from_feed:
        urls += _feed_sample(args.from_feed)
    from playwright.sync_api import sync_playwright
    reports = []
    with sync_playwright() as p:
        kwargs = {"headless": config.APPLY_BROWSER_HEADLESS}
        if config.APPLY_BROWSER_CHANNEL:
            kwargs["channel"] = config.APPLY_BROWSER_CHANNEL
        if config.APPLY_BROWSER_EXECUTABLE:
            kwargs["executable_path"] = config.APPLY_BROWSER_EXECUTABLE
        browser = p.chromium.launch(**kwargs)
        for url in urls:
            reports.append({"url": url, **recon(url, browser)})
            print(f"{len(reports)}/{len(urls)} {reports[-1]['platform']}", flush=True)
        browser.close()
    Path(args.out).write_text(json.dumps(reports, indent=1))


if __name__ == "__main__":
    main()
