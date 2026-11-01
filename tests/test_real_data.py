"""Responses captured from the live PropertyData API (Doncaster DN4, 1 Nov 2026)."""
import json, pathlib
from dealhunter.config import DEFAULT_BUY_BOX as BB
from dealhunter.engine import evaluate, refurb_band
from dealhunter.propertydata import normalise_listing, normalise_comps, match_floor_area

F = pathlib.Path(__file__).parent / "fixtures"
load = lambda n: json.loads((F / n).read_text())

def listings():
    return {l["id"]: l for l in map(normalise_listing, load("sourced_dn4.json")["properties"])}

def test_listing_fields():
    l = listings()["R169212860"]
    assert l["lists"] == ["reduced-properties"]
    assert round(l["reduced_pct"]) == 22 and l["orig_price"] == 90000 and l["price_changes"] == 2
    assert l["portal"] == "Rightmove" and l["precise_address"].startswith("7, ALEXANDRA")

def test_floor_area_uses_precise_address_and_latest_epc():
    l = listings()["R169212860"]
    known = load("floor_areas_dn4_0nd.json")["known_floor_areas"]
    assert match_floor_area(known, l["precise_address"]) == 1055
    assert match_floor_area(known, l["address"]) is None

def test_comps_filtered_to_same_type():
    d = load("sold_psf_dn4_0.json")["data"]
    t, s = normalise_comps(d, "Terraced house"), normalise_comps(d, "Semi-detached house")
    assert t["same_type"] and t["n"] == 67 and s["n"] == 38
    assert t["median"] < s["median"]

def test_flags_and_bands_from_summary():
    L = listings()
    d = load("sold_psf_dn4_0.json")["data"]
    e = evaluate(L["R169212860"], normalise_comps(d, "Terraced house"), 1055, BB)
    assert any("Tenants in situ" in f for f in e["flags"])
    assert e["verdict"] == "Review" and e["base"]["profit"] > 15000  # strong numbers, capped by tenants
    mmoa = evaluate(L["R94350702"], normalise_comps(d, "Terraced house"), None, BB)
    assert refurb_band(L["R94350702"]) == "heavy"
    assert any("Modern Method of Auction" in f for f in mmoa["flags"])
    vac = evaluate(L["R89313834"], normalise_comps(d, "Terraced house"), None, BB)
    assert any("insist on vacant possession" in f for f in vac["flags"])
