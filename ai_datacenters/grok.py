"""Thin wrapper around xAI's OpenAI-compatible /v1/chat/completions endpoint
with top-level `search_parameters` for live web search.

Two operations, both expecting strict JSON back:
  discover_projects(n)            seeds the project table on first run
  check_project(project_meta)     fetches "what's new since last check" for one project
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
        f'    "notes": "<1-2 sentence context: what makes this notable, partners, total planned scale>"\n'
        f'  }}\n'
        f']}}\n\n'
        f"Be conservative with claimed_mw — use the announced/contracted figure, "
        f"not speculative future expansions. Distinguish IT load from total facility power "
        f"if the source does (prefer total facility MW).{avoid}"
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
        f"LAST CHECKED: {last_checked}\n\n"
        f"Use live web search to find news {since_clause} about: construction progress, "
        f"capacity changes, milestones (groundbreaking, topping out, power-on, operational date), "
        f"delays, partner/customer changes, or capex updates.\n\n"
        f"Return ONLY this JSON shape:\n"
        f'{{\n'
        f'  "new_status": "<concise one-line current status>",\n'
        f'  "summary": "<2-4 sentences of what is materially new since last check>",\n'
        f'  "milestones": [{{"date": "YYYY-MM-DD", "event": "<short>"}}],\n'
        f'  "no_news": <true if nothing material has changed since last check>\n'
        f'}}\n\n'
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
