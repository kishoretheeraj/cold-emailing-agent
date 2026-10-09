"""
Zero-token, no-auth public job sources, normalized to one shape (spec 2026-10-08 fifty-a-day §3.3):

  {company, title, url, location, posted_at (ISO 8601 or None), description, sponsorship, source}

  fetch_simplify(categories)   SimplifyJobs/New-Grad-Positions' listings.json (active, visible rows)
  fetch_board(board)           one company board: Greenhouse, Ashby or Lever (the whole board), or
                               Workday (CXS search per term) -> (jobs, 'ok' | 'missing' | 'error')
  add_detail(job)              a posting's description from its per-posting endpoint (Workday and
                               Greenhouse lists carry none; Workday also gives the start date)
  board_from_url(url, company) the board a posting URL belongs to, so every Simplify posting teaches
                               the sourcer one more company board to sweep

Never raises: a failed source returns nothing and says so. stdlib only, like ats.py. Endpoint and
field shapes follow the public APIs and Career-Ops' providers (github.com/santifer/career-ops,
MIT): Ashby folds secondary locations and treats workplaceType as authoritative over isRemote;
Lever merges categories.allLocations; Workday's list gives only a relative "Posted N Days Ago"
("30+ Days Ago" is no date at all) and a site-relative externalPath.
"""

import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import config
import job_identity

log = logging.getLogger(__name__)

_USER_AGENT = "job-agent/1.0 (personal job search)"
# Block tags separate words; inline tags (<b>, <a>, <span>) sit inside them.
_BLOCK_TAG_RE = re.compile(r"</?(?:p|br|div|li|ul|ol|h[1-6]|tr|td|th|table|section|article)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _http_json(url, body=None):
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = body.encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=config.SOURCING_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _text(value):
    if not isinstance(value, str) or not value:
        return ""
    stripped = _TAG_RE.sub("", _BLOCK_TAG_RE.sub(" ", html.unescape(value)))
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()


def _iso(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
    return None


def _job(company, title, url, location="", posted_at=None, description="", sponsorship=None, source=""):
    title = title.strip() if isinstance(title, str) else ""
    url = url.strip() if isinstance(url, str) else ""
    company = company.strip() if isinstance(company, str) else ""
    if not (title and url and company):
        return None
    return {"company": company, "title": title, "url": url,
            "location": location.strip() if isinstance(location, str) else "",
            "posted_at": posted_at, "description": (description or "")[:config.SOURCING_MAX_DESCRIPTION_CHARS],
            "sponsorship": sponsorship, "source": source}


def _join(parts, sep=" · "):
    seen = []
    for part in parts:
        if isinstance(part, str) and part.strip() and part.strip() not in seen:
            seen.append(part.strip())
    return sep.join(seen)


# ── Simplify ───────────────────────────────────────────────────────────────────

def fetch_simplify(categories):
    """Active, visible rows of the Simplify new-grad feed in `categories`. [] on any failure."""
    wanted = {c.lower() for c in categories or []}
    try:
        payload = _http_json(config.SOURCING_SIMPLIFY_URL)
    except Exception as exc:
        log.warning(f"[SOURCING] | simplify | fetch failed: {exc}")
        return []
    if not isinstance(payload, list):
        log.warning("[SOURCING] | simplify | unexpected payload shape")
        return []
    jobs = []
    for row in payload:
        if not isinstance(row, dict) or not row.get("active") or not row.get("is_visible"):
            continue
        if wanted and str(row.get("category", "")).lower() not in wanted:
            continue
        locations = row.get("locations") if isinstance(row.get("locations"), list) else []
        job = _job(row.get("company_name"), row.get("title"), row.get("url"),
                   _join(locations, " | "), _iso(row.get("date_posted")),
                   sponsorship=row.get("sponsorship"), source="simplify")
        if job:
            job["category"] = row.get("category")
            jobs.append(job)
    return jobs


# ── Board registry ─────────────────────────────────────────────────────────────

def board_from_url(url, company):
    """{platform, board, company} for the company board a posting URL lives on, or None."""
    ident = job_identity.identify(url)
    platform, tenant = ident["platform"], ident["tenant"]
    if platform in ("greenhouse", "ashby", "lever") and tenant:
        return {"platform": platform, "board": tenant.lower() if platform != "ashby" else tenant, "company": company}
    if platform == "workday" and tenant:
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return None
        host = parts.hostname.lower()
        segs = [s for s in parts.path.split("/") if s and not re.match(r"^[a-z]{2}-[a-z]{2}$", s, re.I)]
        if segs[:1] == ["recruiting"]:
            segs = segs[2:]
        if segs:
            return {"platform": "workday", "board": f"{host}/{tenant}/{segs[0]}", "company": company}
    return None


# ── Greenhouse, Ashby, Lever ───────────────────────────────────────────────────

def _greenhouse(payload, company, board):
    # The list is read without content (a large board with every description is megabytes); the
    # description of a posting that survives the cheap filters comes from add_detail.
    jobs = []
    for item in (payload or {}).get("jobs") or []:
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        job = _job(company, item.get("title"), item.get("absolute_url"),
                   location.get("name") if isinstance(location, dict) else location,
                   _iso(item.get("first_published") or item.get("updated_at")),
                   _text(item.get("content")), source="greenhouse")
        if job:
            if str(item.get("id", "")).isdigit():
                job["detail"] = {"board": board, "id": str(item["id"])}
            jobs.append(job)
    return jobs


def _ashby_location(item):
    parts = [item.get("location")]
    for entry in [item] + [s for s in item.get("secondaryLocations") or [] if isinstance(s, dict)]:
        if entry is not item:
            parts.append(entry.get("location"))
        address = (entry.get("address") or {}).get("postalAddress") or {}
        parts += [address.get("addressLocality"), address.get("addressCountry")]
    workplace = str(item.get("workplaceType") or "").strip().lower()
    remote = workplace == "remote" if workplace else item.get("isRemote") is True
    location = _join(parts)
    if remote and "remote" not in location.lower():
        location = _join([location, "Remote"])
    return location


def _ashby(payload, company, board=None):
    jobs = []
    for item in (payload or {}).get("jobs") or []:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        description = item.get("descriptionPlain") or _text(item.get("descriptionHtml"))
        job = _job(company, item.get("title"), item.get("jobUrl"), _ashby_location(item),
                   _iso(item.get("publishedAt")), _WS_RE.sub(" ", description or "").strip(), source="ashby")
        if job:
            jobs.append(job)
    return jobs


def _lever(payload, company, board=None):
    jobs = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        location = _join([categories.get("location")] + list(categories.get("allLocations") or []))
        description = item.get("descriptionPlain") or _text(item.get("description"))
        job = _job(company, item.get("text"), item.get("hostedUrl"), location, _iso(item.get("createdAt")),
                   _WS_RE.sub(" ", description or "").strip(), source="lever")
        if job:
            jobs.append(job)
    return jobs


_BOARD_URLS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{board}/jobs", _greenhouse),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=false", _ashby),
    "lever": ("https://api.lever.co/v0/postings/{board}?mode=json", _lever),
}
_SAFE_BOARD = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


# ── Workday ────────────────────────────────────────────────────────────────────

def _workday_posted_on(label, now=None):
    now = now or datetime.now(timezone.utc)
    text = str(label or "")
    if re.search(r"posted\s+today", text, re.I):
        return now.isoformat()
    if re.search(r"posted\s+yesterday", text, re.I):
        return (now - timedelta(days=1)).isoformat()
    match = re.search(r"posted\s+(\d+)(\+?)\s*day", text, re.I)
    if not match or match.group(2):
        return None
    return (now - timedelta(days=int(match.group(1)))).isoformat()


def _workday_endpoints(board):
    host, tenant, site = board.split("/", 2)
    origin = f"https://{host}"
    public = f"{origin}/recruiting/{tenant}/{site}" if host.endswith("myworkdaysite.com") else f"{origin}/{site}"
    return f"{origin}/wday/cxs/{tenant}/{site}", public


def _workday(board, company, search_terms, now):
    cxs, public = _workday_endpoints(board)
    jobs, seen = [], set()
    page_size = 20
    for term in search_terms or [""]:
        for page in range(config.SOURCING_WORKDAY_MAX_PAGES):
            body = json.dumps({"appliedFacets": {}, "limit": page_size, "offset": page * page_size, "searchText": term})
            payload = _http_json(f"{cxs}/jobs", body=body)
            postings = payload.get("jobPostings") if isinstance(payload, dict) else None
            if not isinstance(postings, list):
                break
            for item in postings:
                path = item.get("externalPath") if isinstance(item, dict) else None
                if not path or path in seen:
                    continue
                seen.add(path)
                job = _job(company, item.get("title"), public + path, item.get("locationsText") or "",
                           _workday_posted_on(item.get("postedOn"), now), source="workday")
                if job:
                    jobs.append(job)
            total = payload.get("total") if isinstance(payload.get("total"), int) else 0
            if len(postings) < page_size or (page + 1) * page_size >= total:
                break
            time.sleep(config.SOURCING_WORKDAY_PAGE_DELAY_SECONDS)
    return jobs


def _workday_detail(job):
    match = re.match(r"^(https://[^/]+)/(?:recruiting/([^/]+)/)?([^/]+)(/job/.+)$", job.get("url") or "")
    if not match:
        return
    origin, tenant, site, path = match.groups()
    if not tenant:
        tenant = job_identity.identify(job["url"])["tenant"]
    info = (_http_json(f"{origin}/wday/cxs/{tenant}/{site}{path}") or {}).get("jobPostingInfo") or {}
    description = _text(info.get("jobDescription"))
    if description:
        job["description"] = description[:config.SOURCING_MAX_DESCRIPTION_CHARS]
    start = info.get("startDate")
    if isinstance(start, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", start.strip()):
        job["posted_at"] = _iso(start.strip())


def _greenhouse_detail(job):
    ref = job.get("detail") or {}
    if not (_SAFE_BOARD.match(str(ref.get("board", ""))) and str(ref.get("id", "")).isdigit()):
        return
    item = _http_json(f"https://boards-api.greenhouse.io/v1/boards/{ref['board']}/jobs/{ref['id']}") or {}
    description = _text(item.get("content"))
    if description:
        job["description"] = description[:config.SOURCING_MAX_DESCRIPTION_CHARS]


def needs_detail(job):
    """True when a posting's description must be fetched separately (Workday, Greenhouse lists)."""
    return not job.get("description") and job.get("source") in ("workday", "greenhouse")


def add_detail(job):
    """Fill a posting's description (and, for Workday, its start date) from the per-posting
    endpoint. Best-effort: a failure leaves the job unchanged."""
    try:
        if job.get("source") == "workday":
            _workday_detail(job)
        elif job.get("source") == "greenhouse":
            _greenhouse_detail(job)
    except Exception as exc:
        log.info(f"[SOURCING] | detail | {job.get('company')} | {type(exc).__name__}: {exc}")
    return job


# ── One board ──────────────────────────────────────────────────────────────────

def fetch_board(board, search_terms=None, now=None):
    """Every posting on one company board, as (jobs, status). 'missing' means the board answered
    that it does not exist (404/410/422); 'error' is anything else that failed."""
    platform, name = board.get("platform"), str(board.get("board") or "")
    company = board.get("company") or name
    try:
        if platform in _BOARD_URLS:
            if not _SAFE_BOARD.match(name):
                return [], "error"
            template, parse = _BOARD_URLS[platform]
            return parse(_http_json(template.format(board=name)), company, name), "ok"
        if platform == "workday" and name.count("/") >= 2:
            return _workday(name, company, search_terms, now), "ok"
        return [], "error"
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410, 422):
            return [], "missing"
        log.warning(f"[SOURCING] | {platform}:{name} | http {exc.code}")
        return [], "error"
    except Exception as exc:
        log.warning(f"[SOURCING] | {platform}:{name} | {type(exc).__name__}: {exc}")
        return [], "error"

