"""Default settings. Everything here can be changed in the app sidebar."""

# Areas to scan: a postcode district (DN4), sector (DN4 0) or full postcode, and a radius in miles.
DEFAULT_AREAS = [
    {"name": "Doncaster", "postcode": "DN4", "radius": 5},
    {"name": "Wakefield", "postcode": "WF1", "radius": 5},
]

# PropertyData sourcing lists that match the Deal Hunter criteria in the notebook.
# (price reduction, needs refurb, short lease, probate/vacant, long time on market, auction)
DEFAULT_LISTS = [
    "reduced-properties",
    "unmodernised-properties",
    "slow-to-sell-properties",
    "repossessed-properties",
    "auction-properties",
]
ALL_LISTS = [
    "reduced-properties", "unmodernised-properties", "slow-to-sell-properties",
    "repossessed-properties", "auction-properties", "short-lease-properties",
    "derelict-properties", "empty-homes", "poor-epc-score", "cheap-per-square-foot",
    "quick-sale-properties", "cash-buyers-only-properties", "back-on-market",
    "properties-with-no-chain",
]

DEFAULT_BUY_BOX = {
    "min_price": 50_000,
    "max_price": 250_000,
    "exclude_flats": True,
    "target_margin": 0.15,     # profit / value after refurb
    "sale_discount": 0.05,     # sell this far below value after refurb
    "months": 8,               # months from purchase to sale completion
    "bridge_rate": 0.0085,     # per month, rolled up
    "bridge_ltv": 0.75,
    "bridge_fee": 0.02,
    "contingency": 0.15,
    "holding_per_month": 350,  # council tax, utilities, insurance
    "purchase_fees": 2_400,    # legal, survey, searches
    "agent_fee": 0.012,        # + VAT
    "sale_legal": 1_200,
}

# Refurb cost per sq ft by band. Replace with your contractor's real rates.
REFURB_RATES = {"light": 15, "medium": 28, "heavy": 45}

# Credit guard: stop a scan before it spends more than this many API credits.
DEFAULT_CREDIT_BUDGET = 150

# How long cached API answers are reused (days).
CACHE_DAYS = {"listings": 0.5, "comps": 14, "floor_areas": 60}
