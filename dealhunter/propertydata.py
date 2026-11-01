"""PropertyData API client (https://propertydata.co.uk/api).

- Caches every answer in the database so repeat scans don't spend credits.
- Counts credits and stops at a budget.
- Field names are read defensively: the docs don't publish full response
  schemas, so the first live scan stores raw responses for checking.
"""
from __future__ import annotations

import math
import random
import re
import threading
import time

import requests

from .config import CACHE_DAYS
from .db import Store

BASE = "https://api.propertydata.co.uk"


POSTCODE_FULL = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$")
POSTCODE_SECTOR = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]? \d$")
POSTCODE_DISTRICT = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$")
POSTCODE_AREA = re.compile(r"^[A-Z]{1,2}$")


def classify_area(text: str) -> tuple[str, str]:
    """('postcode', 'LS9') for a district, sector or full postcode; ('town', 'Leeds') otherwise.
    Raises ValueError with a helpful message for things PropertyData can't search."""
    t = " ".join(str(text or "").upper().split())
    if not t:
        raise ValueError("Enter a postcode (LS9, LS9 0 or LS9 7AB) or a town name.")
    if POSTCODE_FULL.match(t) or POSTCODE_SECTOR.match(t) or POSTCODE_DISTRICT.match(t):
        return "postcode", t
    if POSTCODE_AREA.match(t):
        raise ValueError(f"'{t}' is a whole postcode area, which is too broad to search. "
                         f"Use a district like {t}9, a sector like {t}9 0, or a town name.")
    if len(t) < 3:
        raise ValueError(f"'{text}' isn't a postcode or town PropertyData can search.")
    return "town", str(text).strip()


def geocode_town(name: str) -> str:
    """Town or village name -> 'lat,lng' using postcodes.io (free, no key)."""
    try:
        r = requests.get("https://api.postcodes.io/places", params={"q": name, "limit": 5}, timeout=15)
        results = (r.json() or {}).get("result") or []
    except (requests.RequestException, ValueError):
        raise ValueError(f"Couldn't look up '{name}'. Try a postcode district like LS9 instead.")
    rank = {"City": 0, "Town": 1, "Suburban Area": 2, "Village": 3}
    results.sort(key=lambda p: rank.get(p.get("local_type"), 9))
    if not results:
        raise ValueError(f"No UK place called '{name}'. Try a postcode district like LS9 instead.")
    p = results[0]
    return f"{p['latitude']},{p['longitude']}"


class BudgetExceeded(Exception):
    pass


class APIError(Exception):
    pass


TYPE_MAP = {  # type_standardised -> API type filter
    "flat": "flat",
    "terraced house": "terraced_house",
    "semi-detached house": "semi-detached_house",
    "detached house": "detached_house",
}


def postcode_sector(pc: str) -> str:
    pc = (pc or "").upper().replace(" ", "")
    if len(pc) < 5:
        return pc
    return f"{pc[:-3]} {pc[-3]}"


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    m = re.search(r"-?[\d,]+(\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


class PropertyData:
    def __init__(self, key: str, store: Store, budget: int):
        self.key = key
        self.store = store
        self.budget = budget
        self.credits_used = 0
        self.raw_samples: dict[str, object] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def _get(self, endpoint: str, params: dict, cost: int, cache_days: float):
        cache_key = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        cached = self.store.cache_get(cache_key, cache_days)
        if cached is not None:
            return cached
        with self._lock:  # reserve credits before calling (scans run in parallel threads)
            if self.credits_used + cost > self.budget:
                raise BudgetExceeded(f"Scan stopped at the credit budget ({self.budget}).")
            self.credits_used += cost
        try:
            return self._fetch(endpoint, params, cache_key)
        except Exception:
            with self._lock:
                self.credits_used -= cost
            raise

    def _fetch(self, endpoint: str, params: dict, cache_key: str):
        for attempt in range(3):
            try:
                r = requests.get(f"{BASE}/{endpoint}", params={**params, "key": self.key}, timeout=30)
            except requests.RequestException as ex:
                raise APIError(f"{endpoint}: could not reach PropertyData ({ex.__class__.__name__})")
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", "5")), 30))
                continue
            break
        try:
            body = r.json()
        except ValueError:
            raise APIError(f"{endpoint}: HTTP {r.status_code}, not JSON")
        if body.get("status") != "success":
            raise APIError(f"{endpoint}: {body.get('code', r.status_code)} {body.get('message', '')}".strip())
        self.raw_samples.setdefault(endpoint, body)
        self.store.cache_put(cache_key, body)
        return body

    def check_key(self) -> dict:
        """Account credit balance. Confirms the key works without spending credits."""
        r = requests.get(f"{BASE}/account/credits", params={"key": self.key}, timeout=30)
        try:
            return r.json()
        except ValueError:
            return {"status": "error", "message": f"HTTP {r.status_code}, not JSON"}

    # ------------------------------------------------------------- listings
    def sourced_properties(self, area: str, radius: int, lists: list[str], min_price: int,
                           max_price: int, exclude_flats: bool, results: int = 100) -> list[dict]:
        kind, value = classify_area(area)
        if kind == "town":
            loc = self.store.cache_get("geocode?" + value.lower(), 365)
            if loc is None:
                loc = geocode_town(value)
                self.store.cache_put("geocode?" + value.lower(), loc)
            where = {"location": loc}
        else:
            where = {"postcode": value}
        params = {"list": ",".join(lists[:5]), **where, "radius": radius,
                  "min_price": min_price, "max_price": max_price, "results": results, "exclude_sstc": 1}
        if exclude_flats:
            params["standardised_type"] = "!flat"
        body = self._get("sourced-properties", params, math.ceil(results / 10), CACHE_DAYS["listings"])
        return [normalise_listing(p) for p in body.get("properties", [])]

    # ---------------------------------------------------------------- comps
    def sold_psf(self, postcode: str, type_std: str) -> dict:
        params = {"postcode": postcode_sector(postcode), "max_age": 24, "points": 100, "class": "old_stock"}
        t = TYPE_MAP.get((type_std or "").lower())
        if t:
            params["type"] = t
        body = self._get("sold-prices-per-sqf", params, 1, CACHE_DAYS["comps"])
        return normalise_comps(body.get("data", body), type_std)

    # ---------------------------------------------------------- floor areas
    def floor_area(self, postcode: str, address: str):
        if not _house_key(address)[0]:
            return None  # no house number: can't match, don't spend a credit
        body = self._get("floor-areas", {"postcode": postcode.upper()}, 1, CACHE_DAYS["floor_areas"])
        return match_floor_area(body.get("known_floor_areas") or body.get("data", {}).get("known_floor_areas") or [], address)


# ---------------------------------------------------------------- parsing

def normalise_listing(p: dict) -> dict:
    addr = _first(p, "address", "display_address", "title", default="Address not given")
    pc = _first(p, "postcode", default="")
    if not pc:
        m = re.search(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", str(addr).upper())
        pc = m.group(0) if m else ""
    lists = p.get("lists") or []
    if isinstance(lists, (str, dict)):
        lists = [lists]
    list_ids = [str(x.get("id", "")) if isinstance(x, dict) else str(x) for x in lists]
    hist = [h for h in (p.get("price_history") or []) if isinstance(h, dict) and _num(h.get("price"))]
    orig = _num(hist[0]["price"]) if hist else None
    pid = str(_first(p, "id", "property_id", "url", default=addr))
    portal = {"R": "Rightmove", "Z": "Zoopla", "O": "OnTheMarket"}.get(pid[:1], "") if pid[1:2].isdigit() else ""
    return {
        "id": str(_first(p, "id", "property_id", "url", default=addr)),
        "address": addr,
        "precise_address": p.get("precise_address") or "",
        "postcode": pc,
        "price": _num(_first(p, "price", "asking_price")),
        "bedrooms": _num(_first(p, "bedrooms", "beds")),
        "type": _first(p, "type", default=""),
        "type_std": _first(p, "type_standardised", default=""),
        "sqft": _num(_first(p, "sqf", "square_feet", "internal_area", "floor_area")),
        "days_on_market": _num(_first(p, "days_on_market", "dom")),
        "months_on_market": _num(p.get("months_on_market")),
        "reduced_pct": _num(p.get("reduced_by")),   # API gives a percentage
        "orig_price": orig,
        "price_changes": max(0, len(hist) - 1),
        "days_since_price_change": _num(p.get("days_since_price_change")),
        "summary": p.get("summary") or "",
        "years_remaining": _num(p.get("years_remaining")),
        "highest_offer": _num(p.get("highest_offer")),
        "url": _first(p, "url", "listing_url", "link", default=""),
        "portal": _first(p, "portal", "source", default=portal),
        "distance": _num(_first(p, "distance_to", "distance")),
        "lat": _num(p.get("lat")), "lng": _num(p.get("lng")),
        "lists": list_ids,
    }


def _percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


COMP_TYPE = {"flat": "flat", "terraced house": "terraced_house",
             "semi-detached house": "semi-detached_house", "detached house": "detached_house"}


def normalise_comps(d: dict, type_std: str = "") -> dict:
    """Quartiles of sold £/sq ft. Uses only sales of the same property type
    when there are enough of them (sector data mixes all types)."""
    avg = _num(_first(d, "average", "avg"))
    r80 = d.get("80pc_range") or d.get("range_80pc") or []
    raw = d.get("raw_data") or []
    want = COMP_TYPE.get((type_std or "").lower())
    if want and sum(1 for r in raw if isinstance(r, dict) and r.get("type") == want) >= 12:
        raw = [r for r in raw if isinstance(r, dict) and r.get("type") == want]
        same_type = True
    else:
        same_type = not want
    psfs = []
    for row in raw:
        if isinstance(row, dict):
            v = _num(_first(row, "price_per_sqf", "psf", "price_per_square_foot"))
        elif isinstance(row, (list, tuple)) and row:
            v = _num(row[0])
        else:
            v = None
        if v:
            psfs.append(float(v))
    n = len(psfs) or int(_num(_first(d, "points_analysed", "points")) or 0)
    if len(psfs) >= 8:
        p25, med, p75 = (_percentile(psfs, q) for q in (0.25, 0.5, 0.75))
        spread = (p75 - p25) / (2 * med)
    elif avg and len(r80) == 2:
        lo, hi = _num(r80[0]), _num(r80[1])
        med = avg
        p25 = avg - 0.52 * (avg - lo)   # 80% band ≈ ±1.28σ; quartiles ≈ ±0.67σ
        p75 = avg + 0.52 * (hi - avg)
        spread = (p75 - p25) / (2 * med)
    elif avg:
        med, p25, p75, spread = avg, avg * 0.88, avg * 1.12, 0.12
    else:
        return {"ok": False, "n": 0}
    return {"ok": True, "n": n, "median": med, "p25": p25, "p75": p75, "spread": spread,
            "radius": _num(d.get("radius")), "same_type": same_type}


def _house_key(addr: str) -> tuple[str, str]:
    a = str(addr).lower()
    num = re.search(r"\b(flat\s*\w+|\d+[a-z]?)\b", a)
    street = re.search(r"\d+[a-z]?,?\s+([a-z]+(?:\s[a-z]+)?)", a)
    return (num.group(1) if num else "", street.group(1) if street else "")


def match_floor_area(known: list[dict], address: str):
    """Most recent EPC floor area for the same house number and street."""
    num, street = _house_key(address)
    if not num:
        return None
    known = sorted(known, key=lambda k: str(k.get("inspection_date", "")), reverse=True)
    for k in known:
        kn, ks = _house_key(k.get("address", ""))
        sq = _num(k.get("square_feet"))
        if sq and kn == num and (not street or not ks or street.split()[0] in ks):
            return sq
    return None


# ------------------------------------------------------------------ demo

def _stable(s: str) -> int:
    return sum((i + 1) * ord(c) for i, c in enumerate(s))


class DemoData:
    """Generates realistic-looking listings and comps without an API key.
    Clearly labelled as demo in the app. Deterministic per area."""

    STREETS = ["Balby Road", "Victoria Street", "Wellgate Close", "Harland Road", "Mount Pleasant",
               "Oakfield Terrace", "Cranbrook Avenue", "Station Road", "Park Lane", "Church Street",
               "Albert Road", "Highfield Road", "Queen Street", "Westfield Avenue", "Manor Road"]

    def __init__(self):
        self.credits_used = 0
        self.raw_samples = {}

    def sourced_properties(self, area, radius, lists, min_price, max_price, exclude_flats, results=100):
        kind, value = classify_area(area)
        postcode = value if kind == "postcode" else "DN4"
        rnd = random.Random(postcode)
        district = postcode.upper().split()[0]
        out = []
        for i in range(14):
            t = rnd.choice(["Terraced house", "Terraced house", "Semi-detached house", "Detached house"]
                           + ([] if exclude_flats else ["Flat"]))
            beds = {"Flat": rnd.choice([1, 2]), "Terraced house": rnd.choice([2, 3, 3]),
                    "Semi-detached house": 3, "Detached house": rnd.choice([3, 4])}[t]
            sqft = int(estimate_sqft(t, beds) * rnd.uniform(0.85, 1.15))
            base = 125 + (_stable(district) % 40)
            psf = base * rnd.uniform(0.68, 1.02)
            price = round(sqft * psf / 1000) * 1000
            if not (min_price <= price <= max_price):
                continue
            ls = rnd.sample(lists, k=min(len(lists), rnd.choice([1, 1, 2])))
            pc = f"{district} {rnd.randint(1, 9)}{rnd.choice('ABDEFGHJLNPQRSTUWXY')}{rnd.choice('ABDEFGHJLNPQRSTUWXY')}"
            out.append({
                "id": f"DEMO-{district}-{i}", "address": f"{rnd.randint(1, 140)} {rnd.choice(self.STREETS)}",
                "postcode": pc, "price": price, "bedrooms": beds, "type": t, "type_std": t,
                "sqft": sqft if rnd.random() < 0.5 else None,
                "days_on_market": rnd.randint(10, 180),
                "months_on_market": None,
                "reduced_pct": rnd.choice([5.0, 8.3, 12.5, 16.7]) if "reduced-properties" in ls else None,
                "orig_price": None, "price_changes": 0, "days_since_price_change": None, "summary": "",
                "precise_address": "",
                "years_remaining": None, "highest_offer": None, "url": "", "portal": "demo",
                "distance": round(rnd.uniform(0.2, radius), 1), "lat": None, "lng": None, "lists": ls,
                "_demo_true_sqft": sqft,
            })
        return out

    def sold_psf(self, postcode, type_std):
        rnd = random.Random(postcode_sector(postcode) + type_std)
        district = postcode.upper().split()[0]
        med = (125 + (_stable(district) % 40)) * {"Detached house": 1.1, "Flat": 1.15}.get(type_std, 1.0)
        spread = rnd.uniform(0.07, 0.2)
        return {"ok": True, "n": rnd.choice([6, 12, 18, 25, 32]), "median": med,
                "p25": med * (1 - spread), "p75": med * (1 + spread * 1.4), "spread": spread, "radius": 0.6}

    def floor_area(self, postcode, address):
        return None


def estimate_sqft(type_std: str, beds) -> int:
    beds = int(beds or 3)
    t = (type_std or "").lower()
    table = {"flat": {0: 400, 1: 500, 2: 680, 3: 850},
             "terraced house": {1: 550, 2: 720, 3: 880, 4: 1100, 5: 1300},
             "semi-detached house": {2: 760, 3: 940, 4: 1200, 5: 1450},
             "detached house": {2: 900, 3: 1150, 4: 1450, 5: 1800}}
    row = table.get(t, table["terraced house"])
    return row.get(beds, row[min(row, key=lambda b: abs(b - beds))])
