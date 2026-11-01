"""One scan: fetch listings for each area, enrich in parallel, evaluate, store.

Built to finish inside a 5-minute serverless limit: lookups run 8 at a time and
the scan stops early at TIME_LIMIT. Everything fetched is cached, so running the
scan again carries on where it stopped without paying twice.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import REFURB_RATES
from .db import Store
from .engine import evaluate
from .propertydata import APIError, BudgetExceeded

TIME_LIMIT = 240  # seconds
WORKERS = 8


def _enrich(client, l):
    comps, sqft_found, err = {"ok": False, "n": 0}, None, None
    try:
        if l.get("postcode"):
            comps = client.sold_psf(l["postcode"], l.get("type_std"))
            if not l.get("sqft"):
                sqft_found = client.floor_area(l["postcode"], l.get("precise_address") or l["address"])
    except BudgetExceeded:
        raise
    except APIError as e:
        err = f"{l['address']}: {e}"
    return l, comps, sqft_found, err


def run_scan(client, store: Store, areas: list[dict], lists: list[str], bb: dict,
             rates=REFURB_RATES, progress=None, time_limit: float = TIME_LIMIT) -> dict:
    t0 = time.time()
    seen, errors, stopped = 0, [], ""
    logs = store.all_logs()
    found: list[tuple[str, dict]] = []
    try:
        for ai, area in enumerate(areas):
            if progress:
                progress(ai / max(1, len(areas)) * 0.2, f"Fetching listings near {area['name']}")
            try:
                for l in client.sourced_properties(area["postcode"], int(area.get("radius") or 5), lists,
                                                   bb["min_price"], bb["max_price"], bb["exclude_flats"]):
                    if l.get("price"):
                        found.append((area["name"], l))
            except (APIError, ValueError) as e:
                errors.append(f"{area['name'] or area['postcode']}: {e}")
    except BudgetExceeded as e:
        stopped = str(e)

    area_of = {l["id"]: a for a, l in found}
    if found and not stopped:
        with ThreadPoolExecutor(WORKERS) as pool:
            futs = [pool.submit(_enrich, client, l) for _, l in found]
            for i, f in enumerate(as_completed(futs)):
                try:
                    l, comps, sqft_found, err = f.result()
                except BudgetExceeded as e:
                    stopped = str(e)
                    for other in futs:
                        other.cancel()
                    continue
                if err:
                    errors.append(err)
                l["_comps"], l["_sqft_found"] = comps, sqft_found
                ev = evaluate(l, comps, sqft_found, bb, logs.get(l["id"], {}).get("overrides"), rates)
                store.upsert_listing(l["id"], area_of[l["id"]], l, ev)
                seen += 1
                if progress:
                    progress(0.2 + 0.8 * (i + 1) / len(futs), f"Valued {l['address']}")
                if time.time() - t0 > time_limit and not stopped:
                    stopped = "Stopped early to stay inside the time limit. Run the scan again to finish (no credits are re-spent)."
                    for other in futs:
                        other.cancel()

    mode = "demo" if client.__class__.__name__ == "DemoData" else "live"
    store.log_scan(mode, client.credits_used, seen, "; ".join(([stopped] if stopped else []) + errors[:5]))
    return {"listings": seen, "found": len(found), "credits": client.credits_used, "errors": errors,
            "stopped": stopped, "seconds": round(time.time() - t0, 1),
            "raw_samples": getattr(client, "raw_samples", {})}


def re_evaluate_all(store: Store, bb: dict, rates=REFURB_RATES) -> list[dict]:
    """Re-run the numbers on stored listings with the current buy box. No API calls."""
    logs = store.all_logs()
    blank = {"status": "New", "reason": "", "notes": "", "overrides": {}, "updated_by": "", "updated_at": ""}
    out = []
    for row in store.all_listings():
        l = row["data"]
        ev = evaluate(l, l.get("_comps", {"ok": False}), l.get("_sqft_found"), bb,
                      logs.get(row["id"], {}).get("overrides"), rates)
        out.append({**row, "evaluation": ev, "log": logs.get(row["id"], blank)})
    return out
