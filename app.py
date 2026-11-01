"""Deal Hunter web API (FastAPI). Vercel runs this as one Python function;
the front end in public/ is served from Vercel's CDN.

Local:  pip install -r requirements.txt && uvicorn app:app --reload
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from dealhunter.config import (ALL_LISTS, DEFAULT_AREAS, DEFAULT_BUY_BOX, DEFAULT_CREDIT_BUDGET,
                               DEFAULT_LISTS, REFURB_RATES)
from dealhunter.db import Store
from dealhunter.engine import refurb_band
from dealhunter.propertydata import DemoData, PropertyData
from dealhunter.scan import re_evaluate_all, run_scan

app = FastAPI(title="Deal Hunter")
API_KEY = os.environ.get("PROPERTYDATA_API_KEY", "")
PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET = (os.environ.get("APP_SECRET") or PASSWORD or "local-dev").encode()
COOKIE = "dh_session"
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


# ------------------------------------------------------------------ auth
def _sign(name: str, exp: int) -> str:
    msg = f"{name}|{exp}"
    return msg + "|" + hmac.new(SECRET, msg.encode(), hashlib.sha256).hexdigest()


def user(request: Request) -> str:
    if not PASSWORD:
        return request.cookies.get("dh_name") or "you"
    tok = request.cookies.get(COOKIE, "")
    try:
        name, exp, sig = tok.rsplit("|", 2)
        ok = hmac.compare_digest(_sign(name, int(exp)), tok) and int(exp) > time.time()
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(401, "Sign in first.")
    return name


class Login(BaseModel):
    password: str = ""
    name: str


@app.post("/api/login")
def login(body: Login, response: Response):
    name = body.name.strip()[:40]
    if not name:
        raise HTTPException(400, "Enter your name.")
    if PASSWORD and not hmac.compare_digest(body.password, PASSWORD):
        raise HTTPException(401, "Wrong password.")
    exp = int(time.time()) + 30 * 86400
    secure = bool(os.environ.get("VERCEL"))
    response.set_cookie(COOKIE, _sign(name, exp), max_age=30 * 86400, httponly=True, samesite="lax", secure=secure)
    response.set_cookie("dh_name", name, max_age=30 * 86400, samesite="lax", secure=secure)
    return {"name": name}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    try:
        name = user(request)
    except HTTPException:
        return {"signed_in": False, "password_required": bool(PASSWORD)}
    return {"signed_in": True, "name": name, "password_required": bool(PASSWORD),
            "live": bool(API_KEY), "persistent": store().persistent}


# ------------------------------------------------------------------ settings
DEFAULTS = {"areas": DEFAULT_AREAS, "lists": DEFAULT_LISTS, "bb": DEFAULT_BUY_BOX,
            "rates": REFURB_RATES, "budget": DEFAULT_CREDIT_BUDGET}


def current_settings() -> dict:
    s = store().get_setting("shared", {}) or {}
    out = {k: s.get(k, v) for k, v in DEFAULTS.items()}
    out["bb"] = {**DEFAULT_BUY_BOX, **out["bb"]}
    out["rates"] = {**REFURB_RATES, **out["rates"]}
    return out


@app.get("/api/settings")
def get_settings(who: str = Depends(user)):
    return {**current_settings(), "all_lists": ALL_LISTS}


class Settings(BaseModel):
    areas: list[dict] | None = None
    lists: list[str] | None = None
    bb: dict | None = None
    rates: dict | None = None
    budget: int | None = None


@app.put("/api/settings")
def put_settings(body: Settings, who: str = Depends(user)):
    s = current_settings()
    for k, v in body.model_dump(exclude_none=True).items():
        s[k] = v
    try:
        s["bb"] = {k: type(v)(s["bb"].get(k, v)) for k, v in DEFAULT_BUY_BOX.items()}
        s["rates"] = {k: float(s["rates"].get(k, v)) for k, v in REFURB_RATES.items()}
        s["budget"] = max(10, min(2000, int(s["budget"])))
        s["areas"] = [{"name": str(a.get("name") or a.get("postcode")).strip()[:40],
                       "postcode": str(a.get("postcode")).strip().upper()[:10],
                       "radius": max(1, min(40, int(a.get("radius") or 5)))}
                      for a in s["areas"] if str(a.get("postcode") or "").strip()]
    except (TypeError, ValueError):
        raise HTTPException(400, "Some settings aren't numbers.")
    s["lists"] = [x for x in s["lists"] if x in ALL_LISTS][:5]
    s["areas"] = [a for a in s["areas"] if str(a.get("postcode", "")).strip()]
    store().put_setting("shared", s, who)
    return s


# ------------------------------------------------------------------ deals
def _row(r: dict) -> dict:
    d = {k: v for k, v in r["data"].items() if not k.startswith("_demo")}
    return {"id": r["id"], "area": r["area"], "first_seen": r["first_seen"], "last_seen": r["last_seen"],
            "data": d, "evaluation": r["evaluation"], "log": r["log"], "default_band": refurb_band(d)}


@app.get("/api/deals")
def deals(who: str = Depends(user)):
    s = current_settings()
    rows = re_evaluate_all(store(), s["bb"], s["rates"])
    if API_KEY:  # live mode: never show invented demo listings
        rows = [r for r in rows if not str(r["id"]).startswith("DEMO-")]
    return {"deals": [_row(r) for r in rows], "settings": s}


class Log(BaseModel):
    status: str = "New"
    reason: str = ""
    notes: str = ""
    overrides: dict = {}


@app.put("/api/deals/{deal_id}/log")
def save_log(deal_id: str, body: Log, who: str = Depends(user)):
    o = {k: v for k, v in body.overrides.items() if v not in (None, "", 0)}
    store().save_log(deal_id, body.status, body.reason if body.status == "Rejected" else "", body.notes[:4000], o, who)
    return {"ok": True}


# ------------------------------------------------------------------ scan
@app.post("/api/scan")
def scan(who: str = Depends(user)):
    s = current_settings()
    if not s["areas"]:
        raise HTTPException(400, "Add at least one area.")
    if not s["lists"]:
        raise HTTPException(400, "Pick at least one sourcing list.")
    if API_KEY:
        store().clear_demo_listings()
    client = PropertyData(API_KEY, store(), int(s["budget"])) if API_KEY else DemoData()
    res = run_scan(client, store(), s["areas"], s["lists"], s["bb"], s["rates"])
    if res["raw_samples"]:
        store().put_setting("raw_samples", {k: v for k, v in res["raw_samples"].items()}, who)
    res.pop("raw_samples", None)
    return res


@app.get("/api/scans")
def scans(who: str = Depends(user)):
    return {"scans": store().last_scans(20), "raw_samples": store().get_setting("raw_samples", {})}


@app.post("/api/check-key")
def check_key(who: str = Depends(user)):
    if not API_KEY:
        return {"status": "error", "message": "No PROPERTYDATA_API_KEY set: running in demo mode."}
    try:
        return PropertyData(API_KEY, store(), 0).check_key()
    except Exception as ex:
        return {"status": "error", "message": str(ex)}


@app.post("/api/clear-listings")
def clear(who: str = Depends(user)):
    store().clear_listings()
    return {"ok": True}


@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# Local only: serve the front end. On Vercel, public/ is served by the CDN.
if not os.environ.get("VERCEL"):
    PUBLIC = Path(__file__).parent / "public"

    @app.get("/")
    def index():
        return FileResponse(PUBLIC / "index.html")

    @app.get("/{name}")
    def asset(name: str):
        f = PUBLIC / name
        if f.is_file() and f.resolve().parent == PUBLIC.resolve():
            return FileResponse(f)
        raise HTTPException(404, "Not found")
