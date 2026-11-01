from dealhunter.config import DEFAULT_BUY_BOX as BB
from dealhunter.engine import sdlt_company, evaluate, outside_england, feasibility
from dealhunter.propertydata import normalise_comps, normalise_listing, match_floor_area, postcode_sector, DemoData
from dealhunter.db import Store
from dealhunter.scan import run_scan, re_evaluate_all

def test_sdlt_company_rates():
    # 5% on first 125k + 7% on next 125k + 10% above 250k
    assert sdlt_company(100_000) == 5_000
    assert sdlt_company(160_000) == 6_250 + 2_450
    assert sdlt_company(300_000) == 6_250 + 8_750 + 5_000

def test_outside_england():
    assert outside_england("CF10 1AA") and outside_england("G1 1AA") and outside_england("EH1 1AA")
    assert not outside_england("GL1 1AA") and not outside_england("DN4 0RD") and not outside_england("LS1 1AA")

def test_feasibility_adds_up():
    f = feasibility(160_000, 900, 222, 266, "medium", BB)
    parts = f["price"]+f["tax"]+f["purchase_fees"]+f["refurb"]+f["interest"]+f["bridge_fees"]+f["holding"]+f["sale_costs"]
    assert abs(f["total_cost"]-parts) < 1e-6 and abs(f["sale"]-f["total_cost"]-f["profit"]) < 1e-6

def test_comps_from_raw_and_range():
    raw = {"average": 150, "points_analysed": 20, "raw_data": [{"price_per_sqf": v} for v in range(120, 180, 3)]}
    c = normalise_comps(raw); assert c["ok"] and c["p25"] < c["median"] < c["p75"]
    c2 = normalise_comps({"average": "£150", "80pc_range": [120, 190], "points_analysed": 25})
    assert c2["ok"] and 130 < c2["p25"] < 150 < c2["p75"] < 190
    assert not normalise_comps({})["ok"]

def test_listing_and_floor_area_parsing():
    l = normalise_listing({"id": "R1", "address": "61 Balby Road, Doncaster DN4 0RD", "price": "£84,000", "bedrooms": 3, "type_standardised": "Terraced house", "lists": "reduced-properties"})
    assert l["postcode"] == "DN4 0RD" and l["price"] == 84000 and l["lists"] == ["reduced-properties"]
    known = [{"address": "59 Balby Road", "square_feet": 850}, {"address": "61 Balby Road", "square_feet": 910}]
    assert match_floor_area(known, "61 Balby Road, Doncaster") == 910
    assert postcode_sector("dn40rd") == "DN4 0"

def test_overrides_change_result():
    l = {"id": "x", "address": "1 A Road", "postcode": "DN4 0RD", "price": 90000, "bedrooms": 3, "type_std": "Terraced house", "lists": ["unmodernised-properties"]}
    comps = {"ok": True, "n": 25, "median": 150, "p25": 130, "p75": 180, "spread": 0.1}
    a = evaluate(l, comps, None, BB); b = evaluate(l, comps, None, BB, {"band": "heavy", "sqft": 1000})
    assert a["sqft_source"] == "estimated" and b["sqft_source"] == "you" and b["base"]["refurb"] > a["base"]["refurb"]

def test_demo_scan_end_to_end(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/t.db")
    r = run_scan(DemoData(), s, [{"name": "Doncaster", "postcode": "DN1 1BG", "radius": 5}], ["reduced-properties", "unmodernised-properties"], BB)
    assert r["listings"] > 5
    rows = re_evaluate_all(s, BB)
    assert {x["evaluation"]["verdict"] for x in rows if x["evaluation"]["ok"]} <= {"Go", "Review", "Pass"}
    s.save_log(rows[0]["id"], "Rejected", "Structural issues", "", {}, "Shanil")
    assert s.get_log(rows[0]["id"])["reason"] == "Structural issues"
