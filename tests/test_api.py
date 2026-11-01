import importlib, os
from fastapi.testclient import TestClient

def make(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.delenv("PROPERTYDATA_API_KEY", raising=False)
    import app; importlib.reload(app)
    return TestClient(app.app)

def test_auth_settings_scan_log(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    assert c.get("/api/me").json()["signed_in"] is False
    assert c.get("/api/deals").status_code == 401
    assert c.post("/api/login", json={"name": "Shanil", "password": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"name": "Shanil", "password": "secret"}).status_code == 200
    me = c.get("/api/me").json(); assert me["signed_in"] and me["name"] == "Shanil" and me["live"] is False
    s = c.get("/api/settings").json(); assert s["areas"][0]["postcode"] == "DN4"
    s["bb"]["target_margin"] = 0.12; s["areas"].append({"name": "", "postcode": "", "radius": 5})
    put = c.put("/api/settings", json={k: s[k] for k in ("areas", "lists", "bb", "rates", "budget")}).json()
    assert put["bb"]["target_margin"] == 0.12 and len(put["areas"]) == 2
    r = c.post("/api/scan").json(); assert r["listings"] > 5 and not r["stopped"]
    deals = c.get("/api/deals").json()["deals"]; d = deals[0]
    assert c.put(f"/api/deals/{d['id']}/log", json={"status": "Rejected", "reason": "Location / street",
              "notes": "busy road", "overrides": {"band": "heavy", "sqft": 900}}).json()["ok"]
    d2 = next(x for x in c.get("/api/deals").json()["deals"] if x["id"] == d["id"])
    assert d2["log"]["updated_by"] == "Shanil" and d2["evaluation"]["band"] == "heavy" and d2["evaluation"]["sqft"] == 900
    assert len(c.get("/api/scans").json()["scans"]) == 1
    assert c.get("/").status_code == 200 and c.get("/app.js").status_code == 200
    assert c.get("/../app.py").status_code == 404

def test_live_mode_hides_demo(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    c.post("/api/login", json={"name": "S", "password": "secret"}); c.post("/api/scan")
    assert c.get("/api/deals").json()["deals"]
    monkeypatch.setenv("PROPERTYDATA_API_KEY", "x")
    import app; importlib.reload(app); c2 = TestClient(app.app)
    c2.post("/api/login", json={"name": "S", "password": "secret"})
    assert c2.get("/api/deals").json()["deals"] == [] and c2.get("/api/me").json()["live"] is True
