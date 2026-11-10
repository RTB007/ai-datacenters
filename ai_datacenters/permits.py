"""Turns Grok's permit findings into one verdict, computed here rather than by the model.

Grok lists the permits it found (type + status per permit); its own roll-up proved
inconsistent (it called projects "partially permitted" with not a single key permit
found), so the verdict is derived from that list with fixed rules:

  key permits   zoning, building and power; plus air if an air permit exists at all
                (i.e. the site has on-site generation)
  Texas         counties have no zoning power outside city limits, so a missing zoning
                approval in Texas is not counted as a gap
  blocked       a key permit was denied (and none of that type granted)
  fully         every key permit granted (a granted permit under appeal still counts)
  partially     at least one permit granted (tax incentives are listed but are no permit)
  applied       no permit granted, something filed/pending
  none_found    nothing granted or filed
  not_applicable / unknown  as reported (multi-site entry / no usable answer)
"""
from __future__ import annotations

from typing import Any

KEY_TYPES = ("zoning", "building", "power")
GRANTED = ("granted", "challenged")
# Best status wins when a type has several permits.
_RANK = {"granted": 5, "challenged": 4, "pending": 3, "applied": 3, "denied": 2, "not_found": 1}


def best_by_type(parsed: dict[str, Any] | None) -> dict[str, str]:
    best: dict[str, str] = {}
    for p in (parsed or {}).get("permits") or []:
        t = (p.get("type") or "other").strip().lower()
        s = (p.get("status") or "not_found").strip().lower()
        if s == "applied":
            s = "pending"
        if _RANK.get(s, 0) > _RANK.get(best.get(t, ""), 0):
            best[t] = s
    return best


def verdict(parsed: dict[str, Any] | None, location: str | None) -> str:
    if not isinstance(parsed, dict):
        return "unknown"
    if parsed.get("verdict") == "not_applicable":
        return "not_applicable"
    best = best_by_type(parsed)
    if not best:
        return "unknown" if parsed.get("verdict") == "unknown" else "none_found"
    texas = "texas" in (location or "").lower()
    key = list(KEY_TYPES) + (["air"] if best.get("air") not in (None, "not_found") else [])

    def ok(t: str) -> bool:
        if best.get(t) in GRANTED:
            return True
        return t == "zoning" and texas and best.get(t) in (None, "not_found")

    if any(best.get(t) == "denied" for t in key):
        return "blocked"
    if all(ok(t) for t in key):
        return "fully_permitted"
    real = [s for t, s in best.items() if t != "incentives"]
    if any(s in GRANTED for s in real):
        return "partially_permitted"
    if any(s == "pending" for s in real):
        return "applied"
    return "none_found"


def facts(parsed: dict[str, Any] | None, location: str | None) -> dict[str, Any]:
    """Verdict plus the per-type statuses and open-issue count the charts use."""
    issues = [i for i in ((parsed or {}).get("open_issues") or []) if i] if isinstance(parsed, dict) else []
    return {
        "verdict": verdict(parsed, location),
        "types": best_by_type(parsed) if isinstance(parsed, dict) else {},
        "issues": len(issues),
    }
