"""Valuation, refurb band, feasibility and verdict. Plain arithmetic, no AI."""
from __future__ import annotations

from .config import REFURB_RATES
from .propertydata import estimate_sqft

# England & NI residential SDLT bands from 1 April 2025, plus the 5% higher-rates
# surcharge a company pays on every band. Wales (LTT) and Scotland (LBTT) differ.
SDLT_BANDS = [(125_000, 0.0), (250_000, 0.02), (925_000, 0.05), (1_500_000, 0.10), (float("inf"), 0.12)]
SURCHARGE = 0.05
NON_ENGLAND_PREFIXES = ("CF", "SA", "NP", "LL", "LD", "AB", "DD", "DG", "EH", "FK", "G", "HS", "IV",
                        "KA", "KW", "KY", "ML", "PA", "PH", "TD", "ZE", "BT")


def sdlt_company(price: float) -> float:
    tax, lo = 0.0, 0.0
    for hi, rate in SDLT_BANDS:
        if price > lo:
            tax += (min(price, hi) - lo) * (rate + SURCHARGE)
        lo = hi
    return round(tax)


def outside_england(postcode: str) -> bool:
    area = "".join(ch for ch in (postcode or "").upper().split(" ")[0] if ch.isalpha())
    return area in NON_ENGLAND_PREFIXES


HEAVY_WORDS = ("derelict", "full refurbishment", "complete refurbishment", "structural", "fire damage",
               "uninhabitable", "shell", "full renovation")
MEDIUM_WORDS = ("modernisation", "modernization", "refurbishment", "renovation project", "in need of updating",
                "requires updating", "needs updating", "new heating", "no central heating", "unmodernised")


def refurb_band(listing: dict) -> str:
    lists = set(listing.get("lists", []))
    text = (listing.get("summary") or "").lower()
    if lists & {"derelict-properties", "empty-homes"} or any(w in text for w in HEAVY_WORDS):
        return "heavy"
    if lists & {"unmodernised-properties", "poor-epc-score", "repossessed-properties"} or any(w in text for w in MEDIUM_WORDS):
        return "medium"
    return "light"


def listing_flags(listing: dict) -> list[str]:
    """Watch-outs read from the listing itself."""
    text = (listing.get("summary") or "").lower()
    out = []
    if "tenant" in text and "vacant possession" not in text:
        out.append("Tenants in situ: you need vacant possession before you can refurbish and sell")
    elif "tenant" in text:
        out.append("Offered with tenants or vacant: insist on vacant possession")
    if "modern method of auction" in text:
        out.append("Modern Method of Auction: expect a non-refundable reservation fee (often 4.5%+ of price) on top")
    elif "auction" in text or "auction-properties" in listing.get("lists", []):
        out.append("Auction: completion usually due in 28 days. Funding must be in place before bidding")
    if "cash buyer" in text:
        out.append("Cash buyers only: often means it can't be mortgaged. Find out why")
    for w, msg in (("non-standard", "Non-standard construction mentioned: check mortgageability"),
                   ("subsidence", "Subsidence mentioned"), ("japanese knotweed", "Japanese knotweed mentioned"),
                   ("flying freehold", "Flying freehold mentioned"), ("leasehold", "Leasehold: check lease length and ground rent")):
        if w in text:
            out.append(msg)
    return out


def feasibility(price, sqft, asis_psf, renov_psf, band, bb, rates=REFURB_RATES,
                refurb_mult=1.0, value_mult=1.0, extra_months=0) -> dict:
    months = max(1, bb["months"] + extra_months)
    asis_value = sqft * asis_psf
    gdv = sqft * renov_psf * value_mult
    refurb = sqft * rates[band] * (1 + bb["contingency"]) * refurb_mult
    tax = sdlt_company(price)
    loan = price * bb["bridge_ltv"]
    interest = loan * bb["bridge_rate"] * months
    bridge_fees = loan * bb["bridge_fee"] + 500
    holding = bb["holding_per_month"] * months
    sale = gdv * (1 - bb["sale_discount"])
    sale_costs = sale * bb["agent_fee"] * 1.2 + bb["sale_legal"]
    total = price + tax + bb["purchase_fees"] + refurb + interest + bridge_fees + holding + sale_costs
    profit = sale - total
    cash_in = price - loan + tax + bb["purchase_fees"] + refurb + bridge_fees + holding
    return {
        "months": months, "asis_value": asis_value, "gdv": gdv, "bmv": 1 - price / asis_value if asis_value else 0,
        "price": price, "tax": tax, "purchase_fees": bb["purchase_fees"], "refurb": refurb, "loan": loan,
        "interest": interest, "bridge_fees": bridge_fees, "holding": holding, "sale": sale,
        "sale_costs": sale_costs, "total_cost": total, "profit": profit,
        "margin": profit / gdv if gdv else 0, "cash_in": cash_in,
        "roi": profit / cash_in if cash_in else 0,
        "annualised": (profit / cash_in) * 12 / months if cash_in else 0,
    }


def max_offer_for(sqft, asis_psf, renov_psf, band, bb, rates=REFURB_RATES) -> float:
    """Highest purchase price that still meets the target margin in the base case."""
    lo, hi = 0.0, sqft * renov_psf
    if feasibility(1_000, sqft, asis_psf, renov_psf, band, bb, rates)["margin"] < bb["target_margin"]:
        return 0.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if feasibility(mid, sqft, asis_psf, renov_psf, band, bb, rates)["margin"] >= bb["target_margin"]:
            lo = mid
        else:
            hi = mid
    return round(lo / 500) * 500 if lo % 500 == 0 else (lo // 500) * 500


def confidence(comps: dict, sqft_source: str, listing: dict, bmv: float) -> tuple[int, str, list[str]]:
    score, why = 100, []
    if not comps.get("ok"):
        return 5, "Low", ["No sold comparables returned for this postcode sector"]
    n = comps.get("n", 0)
    if n < 10:
        score -= 30; why.append(f"Only {n} sold comparables")
    elif n < 20:
        score -= 10; why.append(f"{n} sold comparables, on the thin side")
    sp = comps.get("spread", 0)
    if sp > 0.18:
        score -= 20; why.append(f"Comparable prices widely scattered (±{sp:.0%})")
    elif sp > 0.12:
        score -= 8; why.append(f"Some scatter in comparables (±{sp:.0%})")
    if not comps.get("same_type", True):
        score -= 10; why.append("Too few sales of this property type nearby: valued against all types")
    if sqft_source == "estimated":
        score -= 20; why.append("Floor area estimated from bedrooms. Check the floor plan")
    if listing.get("years_remaining") and listing["years_remaining"] < 85:
        score -= 25; why.append(f"Short lease ({int(listing['years_remaining'])} years): hard to mortgage, comps don't reflect it")
    if bmv > 0.35:
        score -= 15; why.append(f"{bmv:.0%} below value is unusual. Assume a reason until the survey says otherwise")
    score = max(5, score)
    return score, ("High" if score >= 75 else "Medium" if score >= 50 else "Low"), why


def verdict(margin: float, conf_label: str, target: float, worst_profit: float) -> str:
    if margin >= target and conf_label == "High" and worst_profit > 0:
        return "Go"
    if margin >= target or (margin >= target - 0.07 and conf_label != "Low"):
        return "Review"
    return "Pass"


def evaluate(listing: dict, comps: dict, sqft_found, bb: dict, overrides: dict | None = None,
             rates=REFURB_RATES) -> dict:
    o = overrides or {}
    if o.get("sqft"):
        sqft, sqft_source = float(o["sqft"]), "you"
    elif listing.get("sqft"):
        sqft, sqft_source = float(listing["sqft"]), "listing"
    elif sqft_found:
        sqft, sqft_source = float(sqft_found), "EPC register"
    else:
        sqft, sqft_source = float(estimate_sqft(listing.get("type_std"), listing.get("bedrooms"))), "estimated"

    if not comps.get("ok") and not (o.get("asis_psf") and o.get("renov_psf")):
        return {"ok": False, "reason": "No sold comparables", "sqft": sqft, "sqft_source": sqft_source}

    asis_psf = float(o.get("asis_psf") or comps["p25"])
    renov_psf = float(o.get("renov_psf") or comps["p75"])
    band = o.get("band") or refurb_band(listing)
    price = float(o.get("price") or listing["price"])

    base = feasibility(price, sqft, asis_psf, renov_psf, band, bb, rates)
    worst = feasibility(price, sqft, asis_psf, renov_psf, band, bb, rates, 1.25, 0.93, 3)
    best = feasibility(price, sqft, asis_psf, renov_psf, band, bb, rates, 0.9, 1.03, -2)
    score, label, why = confidence(comps, sqft_source, listing, base["bmv"])
    v = verdict(base["margin"], label, bb["target_margin"], worst["profit"])

    flags = []
    if base["bmv"] < 0.2:
        flags.append(f"Asking is {base['bmv']:.0%} below as-is value, short of the 20% rule")
    if worst["profit"] < 0:
        flags.append(f"Loses £{-worst['profit']:,.0f} in the worst case")
    flags += listing_flags(listing)
    if v == "Go" and any(f.startswith("Tenants in situ") for f in flags):
        v = "Review"  # can't flip it until the tenants have left
    if outside_england(listing.get("postcode", "")):
        flags.append("Outside England: stamp duty here uses England rates, so the tax line is wrong")

    max_offer = max_offer_for(sqft, asis_psf, renov_psf, band, bb, rates)

    return {"ok": True, "max_offer": max_offer, "sqft": sqft, "sqft_source": sqft_source, "asis_psf": asis_psf, "renov_psf": renov_psf,
            "band": band, "base": base, "worst": worst, "best": best, "conf_score": score, "conf": label,
            "conf_why": why, "verdict": v, "flags": flags, "comps": comps,
            "rank_score": base["margin"] * score / 100}
