"""Thin wrapper around xAI's OpenAI-compatible /v1/chat/completions endpoint
with top-level `search_parameters` for live web search.

Three operations, all expecting strict JSON back:
  discover_projects(n)            seeds the project table on first run
  check_project(project_meta)     fetches "what's new since last check" for one project
  check_permits(project_meta)     finds which permits/approvals the project has
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger("ai_datacenters.grok")

# Pricing per 1M tokens (USD), grok-4.3 as of 2026-06.
PRICE_IN_PER_1M = 1.25
PRICE_OUT_PER_1M = 2.50


class GrokError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise GrokError(
            "XAI_API_KEY env var not set. Get a key at https://console.x.ai/."
        )
    return key


def _call(
    cfg: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    response_format_json: bool = True,
) -> dict[str, Any]:
    """POST /v1/responses with web_search tool. Returns dict with text, citations, usage.

    xAI deprecated the old top-level search_parameters knob on /chat/completions in
    June 2026; live web search is now a tool in the Responses API.
    See https://docs.x.ai/docs/guides/tools/overview.
    """
    body: dict[str, Any] = {
        "model": cfg["grok"]["model"],
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [{"type": "web_search"}],
        "stream": False,
    }
    if response_format_json:
        body["text"] = {"format": {"type": "json_object"}}

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    url = cfg["grok"]["base_url"].rstrip("/") + "/responses"
    timeout = cfg["grok"]["request_timeout_seconds"]

    log.info("calling grok model=%s endpoint=/v1/responses tool=web_search", body["model"])
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise GrokError(f"network error calling xAI: {e}") from e
    if r.status_code != 200:
        raise GrokError(f"xAI returned {r.status_code}: {r.text[:500]}")
    data = r.json()

    text = ""
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in data.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                text = c.get("text") or text
                for a in c.get("annotations") or []:
                    if a.get("type") == "url_citation":
                        u = a.get("url")
                        if u and u not in seen_urls:
                            seen_urls.add(u)
                            cit = {"url": u}
                            if a.get("title"):
                                cit["title"] = a["title"]
                            citations.append(cit)
    if not text:
        raise GrokError(f"unexpected response shape: {json.dumps(data)[:500]}")

    usage = data.get("usage") or {}
    tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens")
    tokens_out = usage.get("output_tokens") or usage.get("completion_tokens")
    cost = None
    if tokens_in is not None and tokens_out is not None:
        cost = (tokens_in * PRICE_IN_PER_1M + tokens_out * PRICE_OUT_PER_1M) / 1_000_000

    return {
        "text": text,
        "citations": citations,
        "model": data.get("model", body["model"]),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
    }


def _extract_json(text: str) -> Any:
    """Tolerant JSON extraction: strip code fences if present, then parse."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Greedy brace/bracket match as a fallback.
    m = re.search(r"(\{.*\}|\[.*\])", s, flags=re.DOTALL)
    if not m:
        raise GrokError(f"no JSON in model output: {text[:300]!r}")
    return json.loads(m.group(0))


# ---------- discover ----------

DISCOVER_SYSTEM = (
    "You are a research assistant specializing in AI datacenter infrastructure. "
    "You use live web search to find current, factual information. "
    "You return strict JSON only, never prose."
)


def _discover_user_prompt(n: int, existing_slugs: list[str]) -> str:
    avoid = (
        f"\n\nThese slugs are already tracked; do NOT return them again: {existing_slugs}"
        if existing_slugs
        else ""
    )
    return (
        f"Find the {n} largest AI datacenters by claimed/announced power capacity (MW) "
        f"that are currently being built, recently opened, or publicly announced for "
        f"the 2024-2027 timeframe. Use live web search.\n\n"
        f"Prioritize: hyperscalers (Microsoft, Google, Amazon/AWS, Meta), xAI (Colossus), "
        f"Anthropic, OpenAI/Stargate, and major neoclouds (CoreWeave, Crusoe, Oracle, "
        f"Nebius, IREN, Applied Digital, Lambda).\n\n"
        f"Return ONLY a JSON object of this exact shape, nothing else:\n"
        f'{{"projects": [\n'
        f'  {{\n'
        f'    "slug": "<short-kebab-case-id, e.g. stargate-abilene>",\n'
        f'    "name": "<canonical project name>",\n'
        f'    "owner": "<owning company or consortium>",\n'
        f'    "location": "<city, state/region, country>",\n'
        f'    "claimed_mw": <number — total announced capacity in MW, or null>,\n'
        f'    "status": "<one-line current status, e.g. \'Under construction; phase 1 of 4\'>",\n'
        f'    "first_announcement_date": "<YYYY-MM-DD of the first public announcement, or null if unknown>",\n'
        f'    "live_date": "<YYYY-MM-DD when the datacenter went/goes operational, or null if not live yet>",\n'
        f'    "notes": "<1-2 sentence context: what makes this notable, partners, total planned scale>"\n'
        f'  }}\n'
        f']}}\n\n'
        f"Be conservative with claimed_mw — use the announced/contracted figure, "
        f"not speculative future expansions. Distinguish IT load from total facility power "
        f"if the source does (prefer total facility MW). "
        f"For first_announcement_date: use the earliest credible public announcement "
        f"(press release, SEC filing, or first major media coverage). "
        f"For live_date: only set this if the project is genuinely operational "
        f"(at least phase 1 powered on and accepting workloads); leave null otherwise.{avoid}"
    )


def discover_projects(
    cfg: dict[str, Any], existing_slugs: list[str] | None = None
) -> dict[str, Any]:
    """Returns {projects: [...], text, citations, model, tokens_*, cost_usd, prompt}."""
    n = cfg["discover"]["target_count"]
    user = _discover_user_prompt(n, existing_slugs or [])
    result = _call(cfg, DISCOVER_SYSTEM, user, response_format_json=True)
    parsed = _extract_json(result["text"])
    projects = parsed["projects"] if isinstance(parsed, dict) and "projects" in parsed else parsed
    if not isinstance(projects, list):
        raise GrokError(f"discover did not return a list: {type(projects).__name__}")
    result["projects"] = projects
    result["prompt"] = f"SYSTEM:\n{DISCOVER_SYSTEM}\n\nUSER:\n{user}"
    return result


# ---------- per-project check ----------

CHECK_SYSTEM = (
    "You are a research assistant tracking AI datacenter construction progress. "
    "You use live web search to find updates about a specific named project. "
    "You return strict JSON only, never prose."
)


def _check_user_prompt(today_iso: str, project: dict[str, Any]) -> str:
    last_checked = project.get("last_checked_at") or "never (first check)"
    since_clause = (
        f"since {last_checked[:10]}"
        if last_checked != "never (first check)"
        else "in the last 6 months"
    )
    return (
        f"DATE: {today_iso}\n"
        f"PROJECT: {project['name']}\n"
        f"OWNER: {project.get('owner') or 'unknown'}\n"
        f"LOCATION: {project.get('location') or 'unknown'}\n"
        f"CLAIMED CAPACITY: "
        f"{project.get('claimed_mw') or 'unknown'} MW\n"
        f"LAST KNOWN STATUS: {project.get('last_known_status') or 'unknown'}\n"
        f"FIRST ANNOUNCEMENT (known): {project.get('first_announcement_date') or 'unknown'}\n"
        f"LIVE DATE (known): {project.get('live_date') or 'not yet live'}\n"
        f"LAST CHECKED: {last_checked}\n\n"
        f"Use live web search to find news {since_clause} about: construction progress, "
        f"capacity changes, milestones (groundbreaking, topping out, power-on, operational date), "
        f"delays, partner/customer changes, or capex updates.\n\n"
        f"Return ONLY this JSON shape:\n"
        f'{{\n'
        f'  "new_status": "<concise one-line current status>",\n'
        f'  "summary": "<2-4 sentences of what is materially new since last check>",\n'
        f'  "milestones": [{{"date": "YYYY-MM-DD", "event": "<short>"}}],\n'
        f'  "first_announcement_date": "<YYYY-MM-DD if known and not already filled above, else null>",\n'
        f'  "live_date": "<YYYY-MM-DD if and only if the project is now operational, else null>",\n'
        f'  "no_news": <true if nothing material has changed since last check>\n'
        f'}}\n\n'
        f"Rules: only set live_date if at least phase 1 is genuinely powered on and accepting workloads "
        f"(opening ceremony, press release confirming 'operational', or trade press confirming live customer workloads). "
        f"For first_announcement_date: backfill if currently unknown — use the earliest credible "
        f"public announcement (press release, SEC filing, first major media coverage). "
        f"If you cannot find any reliable update, set no_news=true and explain briefly in summary."
    )


def check_project(cfg: dict[str, Any], project: dict[str, Any], today_iso: str) -> dict[str, Any]:
    user = _check_user_prompt(today_iso, project)
    result = _call(cfg, CHECK_SYSTEM, user, response_format_json=True)
    try:
        parsed = _extract_json(result["text"])
    except GrokError:
        parsed = None
    result["parsed"] = parsed
    result["prompt"] = f"SYSTEM:\n{CHECK_SYSTEM}\n\nUSER:\n{user}"
    return result


# ---------- per-project permit check ----------

PERMIT_VERDICTS = (
    "fully_permitted",      # all key approvals for (at least) the current phase are granted
    "partially_permitted",  # some key approvals granted, others pending or not found
    "applied",              # applications filed / hearings scheduled, nothing granted yet
    "none_found",           # no permit activity found at all
    "blocked",              # denied, moratorium, or halted by court/appeal
    "not_applicable",       # not a single site (portfolio / "various locations")
    "unknown",
)

PERMIT_SYSTEM = (
    "You are a research assistant who checks the regulatory status of datacenter projects. "
    "You use live web search to find permits and approvals in public records, local news, "
    "county/city meeting minutes, state agency databases, utility filings and company releases. "
    "You never guess: a permit you cannot source is 'not_found'. "
    "You return strict JSON only, never prose."
)


def _permit_user_prompt(today_iso: str, project: dict[str, Any]) -> str:
    return (
        f"DATE: {today_iso}\n"
        f"PROJECT: {project['name']}\n"
        f"OWNER: {project.get('owner') or 'unknown'}\n"
        f"LOCATION: {project.get('location') or 'unknown'}\n"
        f"CLAIMED CAPACITY: {project.get('claimed_mw') or 'unknown'} MW\n"
        f"LAST KNOWN STATUS: {project.get('last_known_status') or 'unknown'}\n\n"
        f"Use live web search to establish whether this datacenter has the permits and "
        f"approvals it needs to be built and powered. First pin down the actual site "
        f"(county, municipality, parcel/campus name). Then look for each of these:\n"
        f"  - zoning: rezoning, land-use, conditional/special-use permit, site plan approval\n"
        f"  - building: building/construction/grading permits\n"
        f"  - air: air-quality permits (on-site gas turbines, backup generators)\n"
        f"  - water: water supply/withdrawal, wastewater or stormwater permits\n"
        f"  - power: utility service or grid interconnection agreement, regulator approval "
        f"(e.g. state PUC, ERCOT, PJM), on-site generation approval\n"
        f"  - environmental: environmental review/impact assessment (NEPA, state equivalents; "
        f"outside the US the national equivalent)\n"
        f"  - incentives: tax abatement / development agreement (not a permit, but list it)\n"
        f"Also note open issues: lawsuits, appeals, moratoria, denied applications, "
        f"organised local opposition.\n\n"
        f"Return ONLY this JSON shape:\n"
        f'{{\n'
        f'  "site": "<the specific site you identified, or null if you could not pin one down>",\n'
        f'  "verdict": "<one of: {", ".join(PERMIT_VERDICTS)}>",\n'
        f'  "summary": "<2-4 sentences: what is approved, what is still missing or contested>",\n'
        f'  "permits": [\n'
        f'    {{"type": "<zoning|building|air|water|power|environmental|incentives|other>",\n'
        f'     "description": "<what exactly, e.g. \'Rezoning of 1,400 acres to heavy industrial\'>",\n'
        f'     "authority": "<who decides, e.g. \'Shackelford County Commissioners Court\'>",\n'
        f'     "status": "<granted|pending|denied|challenged|not_found>",\n'
        f'     "date": "<YYYY-MM-DD of the decision or filing, or null>",\n'
        f'     "source_url": "<URL of the source, or null>"}}\n'
        f'  ],\n'
        f'  "open_issues": ["<short description of each lawsuit/appeal/moratorium/opposition>"]\n'
        f'}}\n\n'
        f"Verdict rules: fully_permitted only if zoning/land use AND building AND power are "
        f"granted for at least the current phase (and air, if it runs on-site generation). "
        f"partially_permitted if some of those are granted. applied if applications are filed "
        f"but none granted. blocked if a key approval was denied or work is halted by a court, "
        f"appeal or moratorium. none_found if you find no permit activity at all. "
        f"not_applicable if this entry is a portfolio of several sites rather than one site. "
        f"unknown if the sources are too thin to judge. Only list permits you found evidence "
        f"for, plus not_found entries for the key types (zoning, building, power) you looked "
        f"for but could not find."
    )


def check_permits(cfg: dict[str, Any], project: dict[str, Any], today_iso: str) -> dict[str, Any]:
    user = _permit_user_prompt(today_iso, project)
    result = _call(cfg, PERMIT_SYSTEM, user, response_format_json=True)
    try:
        parsed = _extract_json(result["text"])
    except GrokError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("verdict") not in PERMIT_VERDICTS:
        parsed["verdict"] = "unknown"
    result["parsed"] = parsed
    result["prompt"] = f"SYSTEM:\n{PERMIT_SYSTEM}\n\nUSER:\n{user}"
    return result
