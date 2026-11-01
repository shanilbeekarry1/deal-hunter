# Deal Hunter

Finds UK properties that look under value, values them against sold comparables,
prices the refurb, runs the buy-refurbish-sell feasibility and keeps a shared deal
log for you and your partner. Runs on Vercel.

```
PropertyData sourcing lists ─► same-type sold £/sq ft (Land Registry + EPC) ─► EPC floor area
          │                                                                         │
          └──► refurb band (lists + listing text) ─► feasibility ◄──────────────────┘
                                                      │
                   ranked shortlist: Go / Review / Pass, max offer, watch-outs
                                                      │
                   shared deal log: status, reject reason, notes, overrides
```

Without an API key it runs on invented demo listings.

## Put it on Vercel (about 10 minutes)

1. **GitHub:** put this folder in a *private* repo.
2. **Vercel:** vercel.com → *Add New → Project* → import the repo → *Deploy*. Vercel detects the FastAPI app; no settings needed.
3. **Database:** in the project, open *Storage → Create Database → Neon* (free tier, pick the London region) → connect it to the project. This adds `DATABASE_URL` for you. Without it, nothing is kept between visits.
4. **Settings → Environment Variables**, add:
   | Name | Value |
   |---|---|
   | `PROPERTYDATA_API_KEY` | your PropertyData key |
   | `APP_PASSWORD` | the password you and your partner will sign in with |
   | `APP_SECRET` | any long random string |
5. *Deployments → … → Redeploy* so the variables take effect.
6. Send your partner the link and the password. Each of you signs in with your own name, so the deal log shows who changed what.

**Vercel plan:** the free Hobby plan is meant for personal, non-commercial projects; check Vercel's terms and move to Pro if this becomes the business tool.

## First live scan

1. Sign in → *Buy box & areas* → set one area (district like `DN4`, sector like `DN4 0`, or full postcode) and a 3–5 mile radius.
2. *Check API key* should say "Key works".
3. Keep the credit budget at 50 and press *Run scan*. Scans run up to 4 minutes; if one stops early, run it again: cached lookups aren't paid for twice.
4. *Scan history* shows the raw API responses if anything looks empty.

### Credit use

| Call | When | Credits | Cached |
|---|---|---|---|
| `/sourced-properties` | once per area per scan | 1 per 10 results (100 = 10) | 12 hours |
| `/sold-prices-per-sqf` | once per postcode sector + property type | 1 | 14 days |
| `/floor-areas` | per listing with a house number but no floor area | 1 | 60 days |

## How the numbers work

- **As-is value** = floor area × lower-quartile £/sq ft of same-type sales in the postcode sector (last 24 months).
- **Value after refurb** = floor area × upper-quartile £/sq ft.
- **Floor area**: from the listing, else the latest EPC for that house, else estimated from bedrooms (lowers confidence).
- **Refurb** = floor area × band rate + contingency. Band comes from the sourcing list and words in the listing ("full refurbishment", "derelict"…). Change it per deal.
- **Stamp duty** = England rates with the 5% company surcharge on every band. Flags Welsh and Scottish postcodes.
- **Bridging**: interest rolled up, 2% arrangement fee, £500 valuation.
- **Max offer** = highest price that still hits your target margin.
- **Go** needs the target margin, High confidence, a profit in the worst case, and no sitting tenants.
- **Watch-outs** come from the listing text: tenants, auction terms, Modern Method of Auction fees, cash buyers only, non-standard construction, leasehold.

Every figure can be overridden per deal. Profits are before corporation tax; confirm tax and finance assumptions with your accountant and lender.

## Run it on your own computer

```
pip install -r requirements.txt
cp .env.example .env        # optional: add your key
uvicorn app:app --reload    # then open http://localhost:8000
```
(Locally it stores data in `dealhunter.db`. Export the variables from `.env` in your shell, or use `vercel dev`.)

## Code map

| File | What it does |
|---|---|
| `app.py` | Web API: sign-in, shared settings, scan, deals, deal log |
| `public/` | The web page (HTML, CSS, JS) |
| `dealhunter/propertydata.py` | PropertyData client, caching, credit budget, response parsing |
| `dealhunter/engine.py` | Valuation, refurb band, stamp duty, feasibility, verdicts, watch-outs |
| `dealhunter/scan.py` | Scan run (parallel, time-limited) |
| `dealhunter/db.py` | Postgres / SQLite storage |
| `dealhunter/config.py` | Defaults: areas, lists, buy box, refurb rates |
| `tests/` | Tests, including real PropertyData responses from Doncaster |

```
pip install pytest httpx && python -m pytest tests
```
