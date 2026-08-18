import json
import logging
from datetime import datetime, timezone

import config
import db
from emailer import _call_claude

log = logging.getLogger(__name__)

# ── Tavily client (lazy singleton) ─────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is None:
        if not config.TAVILY_API_KEY:
            raise RuntimeError("TAVILY_API_KEY is not set")
        from tavily import TavilyClient
        _client = TavilyClient(api_key=config.TAVILY_API_KEY)
    return _client


# ── Cache key construction ─────────────────────────────────────────────────────

def _cache_key(name, company):
    return f"{name.strip().lower()}|{company.strip().lower()}"


# ── Query generation ───────────────────────────────────────────────────────────

def _generate_queries(contact, sender_profile, prompts):
    name = contact.get("name") or "unknown"
    company = contact.get("company") or "unknown"

    tpl = prompts.get("research_query_prompt", config.RESEARCH_QUERY_DEFAULT)

    try:
        formatted = tpl.format(
            sender_profile=sender_profile,
            name=name,
            company=company,
            role=contact.get("role") or "unknown",
            detail=contact.get("detail") or "unknown",
            notes=contact.get("notes") or "unknown",
            dartmouth=contact.get("dartmouth") if contact.get("dartmouth") is not None else "unknown",
            tier=contact.get("tier") or "unknown",
        )
    except Exception as exc:
        log.warning(f"[RESEARCH-Q] | {name} | {company} | template format error: {exc}")
        return []

    try:
        raw = _call_claude(formatted, model=config.RESEARCH_QUERY_MODEL, max_tokens=300,
                           system=sender_profile)
    except Exception as exc:
        log.warning(f"[RESEARCH-Q] | {name} | {company} | _call_claude error: {exc}")
        return []

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except Exception as exc:
        log.warning(f"[RESEARCH-Q] | {name} | {company} | JSON parse error: {exc}")
        return []

    if not isinstance(parsed, list):
        log.warning(f"[RESEARCH-Q] | {name} | {company} | result not a list: {type(parsed)}")
        return []

    queries = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        item = item[:config.RESEARCH_MAX_QUERY_LEN]
        if item:
            queries.append(item)

    queries = queries[:config.RESEARCH_MAX_QUERIES]

    log.info(f"[RESEARCH-Q] | {name} | {company} | queries={len(queries)} | source=model")
    return queries


# ── Tavily execution ───────────────────────────────────────────────────────────

def _run_tavily(queries, contact):
    name = contact.get("name") or ""
    company = contact.get("company") or ""

    if not queries:
        return []

    try:
        client = _get_client()
    except RuntimeError as exc:
        log.warning(f"[RESEARCH-T] | {name} | {company} | client init failed: {exc}")
        return []

    results = []
    for q in queries:
        try:
            resp = client.search(
                query=q,
                search_depth="basic",
                max_results=config.RESEARCH_TAVILY_RESULTS_PER_QUERY,
                include_answer=True,
                include_raw_content=True,
            )
            if resp and (resp.get("results") or resp.get("answer")):
                results.append({"query": q, "result": resp})
        except Exception as exc:
            log.warning(f"[RESEARCH-T] | {name} | {company} | query failed: {q!r} | {exc}")

    log.info(
        f"[RESEARCH-T] | {name} | {company} | "
        f"executed={len(queries)} | non_empty={len(results)}"
    )
    return results


# ── Hardcoded fallback ─────────────────────────────────────────────────────────

def _run_hardcoded_fallback(contact):
    name = contact.get("name") or ""
    company = contact.get("company") or ""

    if not company:
        return []

    q = config.RESEARCH_HARDCODED_FALLBACK_QUERY.format(company=company)

    try:
        client = _get_client()
    except RuntimeError as exc:
        log.warning(f"[RESEARCH-F] | {name} | {company} | client init failed: {exc}")
        return []

    results = []
    try:
        resp = client.search(
            query=q,
            search_depth="basic",
            max_results=config.RESEARCH_TAVILY_RESULTS_PER_QUERY,
            include_answer=True,
            include_raw_content=True,
        )
        if resp and (resp.get("results") or resp.get("answer")):
            results.append({"query": q, "result": resp})
    except Exception as exc:
        log.warning(f"[RESEARCH-F] | {name} | {company} | fallback search failed: {exc}")

    log.info(
        f"[RESEARCH-F] | {name} | {company} | "
        f"fallback_fired | found={bool(results)}"
    )
    return results


# ── Brief curation ─────────────────────────────────────────────────────────────

def _curate_brief(contact, raw_results, prompts):
    name = contact.get("name") or ""
    company = contact.get("company") or ""

    if not raw_results:
        return ""

    parts = []
    for item in raw_results:
        q = item.get("query", "")
        resp = item.get("result", {})
        answer = resp.get("answer") or "none"
        parts.append(f"Query: {q}")
        parts.append(f"Answer: {answer}")
        parts.append("Top results:")
        for r in (resp.get("results") or []):
            title = r.get("title", "")
            content = (r.get("content") or "")[:300]
            url = r.get("url", "")
            domain = url.split("/")[2] if "/" in url and url.count("/") >= 2 else url
            raw_content = (r.get("raw_content") or "")[:500]
            excerpt = f"  Full text excerpt: {raw_content}" if raw_content else ""
            parts.append(f"- {title}: {content}  (source: {domain}){excerpt}")
        parts.append("")

    formatted = "\n".join(parts)
    if len(formatted) > 6000:
        formatted = formatted[:6000]

    tpl = prompts.get("research_curate_prompt", config.RESEARCH_CURATE_DEFAULT)

    try:
        formatted_prompt = tpl.format(
            name=contact.get("name") or "unknown",
            company=contact.get("company") or "unknown",
            role=contact.get("role") or "unknown",
            detail=contact.get("detail") or "unknown",
            raw_results=formatted,
        )
    except Exception as exc:
        log.warning(f"[RESEARCH-C] | {name} | {company} | template format error: {exc}")
        return ""

    try:
        raw = _call_claude(formatted_prompt, model=config.RESEARCH_CURATE_MODEL,
                           max_tokens=config.RESEARCH_CURATE_MAX_TOKENS)
    except Exception as exc:
        log.warning(f"[RESEARCH-C] | {name} | {company} | _call_claude error: {exc}")
        return ""

    output = raw.strip()
    brief = "" if output == "NO_RELIABLE_BRIEF" else output

    log.info(
        f"[RESEARCH-C] | {name} | {company} | "
        f"brief_len={len(brief)} | reliable={bool(brief)}"
    )
    return brief


# ── Public entry point ─────────────────────────────────────────────────────────

def get_research_brief(contact, sender_profile, prompts):
    """
    Returns a string research brief for the contact suitable for
    injection into the email-writer prompt. Caches by
    (name, company) tuple for RESEARCH_CACHE_TTL_DAYS.

    Returns "" if:
      - TAVILY_API_KEY is unset
      - contact has no name or no company
      - all Tavily searches returned empty
      - the curator returns NO_RELIABLE_BRIEF
      - any unexpected error occurs in the pipeline

    Never raises. The caller can always proceed without a brief.
    """
    name = contact.get("name") or ""
    company = contact.get("company") or ""
    queries = []
    raw_results = []

    try:
        if not config.TAVILY_API_KEY:
            return ""
        if not name or not company:
            return ""

        key = _cache_key(name, company)

        cached = db.get_research_cache(key)
        if cached:
            cached_at = cached["cached_at"]
            if isinstance(cached_at, str):
                cached_at = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - cached_at).days
            if age_days < config.RESEARCH_CACHE_TTL_DAYS:
                log.info(
                    f"[RESEARCH] | {name} | {company} | "
                    f"cache_hit | age={age_days}d"
                )
                db.log_agent_event(
                    "research",
                    contact_id=contact.get("id"),
                    contact_name=name,
                    status="success",
                    metadata={
                        "cache_hit": True,
                        "cache_age_days": age_days,
                        "brief_reliable": bool(cached["brief_text"]),
                        "brief_length": len(cached["brief_text"]),
                    },
                )
                return cached["brief_text"]

        queries = _generate_queries(contact, sender_profile, prompts)
        raw_results = _run_tavily(queries, contact) if queries else []

        if not raw_results:
            raw_results = _run_hardcoded_fallback(contact)

        brief_text = _curate_brief(contact, raw_results, prompts)
        brief_reliable = bool(brief_text)

        db.set_research_cache(
            key, name, company,
            brief_text,
            {"raw_results": raw_results, "queries": queries},
            queries_generated=len(queries),
            brief_reliable=brief_reliable,
        )

        log.info(
            f"[RESEARCH] | {name} | {company} | "
            f"path=fresh | "
            f"queries={len(queries)} | results={len(raw_results)} | "
            f"brief_len={len(brief_text)}"
        )

        db.log_agent_event(
            "research",
            contact_id=contact.get("id"),
            contact_name=name,
            status="success",
            metadata={
                "cache_hit": False,
                "queries_generated": len(queries),
                "tavily_results": len(raw_results),
                "brief_reliable": brief_reliable,
                "brief_length": len(brief_text),
            },
        )

        return brief_text

    except Exception as exc:
        log.warning(f"[RESEARCH] | {name} | {company} | unexpected error: {exc}")
        return ""
