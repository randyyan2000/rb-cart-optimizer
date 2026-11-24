"""Seller listings + seller shipping rules.

Two sources:
  * LiveSource  - TCGplayer's own website endpoints (the ones tcgplayer.com's product pages call).
                  Undocumented and unofficial: they may change, and you should keep request volume low.
  * DemoSource  - synthetic sellers/listings derived from catalog prices, for trying the tool offline.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

from .catalog import CACHE_DIR, Catalog, Printing

LISTINGS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{pid}/listings"
SHIPPING_URL = "https://mpapi.tcgplayer.com/v2/seller/shippinginfo?countryCode=US"
LISTING_TTL = 3600
DIRECT_KEY = "__direct__"

CONDITION_RANK = {
    "any": 0,  # snapshot low prices carry no condition
    "mint": 0,
    "near mint": 0,
    "lightly played": 1,
    "slightly played": 1,  # CardTrader
    "moderately played": 2,
    "heavily played": 3,
    "played": 3,  # CardTrader
    "damaged": 4,
    "poor": 4,  # CardTrader
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (rb-cart-optimizer; personal use)",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.tcgplayer.com",
    "Referer": "https://www.tcgplayer.com/",
}


@dataclass
class Listing:
    listing_id: str
    product_id: int
    seller_key: str
    seller_name: str
    price: float
    qty: int
    condition: str
    printing: str  # "Normal" / "Foil"
    seller_id: int | None = None
    direct: bool = False
    fallback_shipping: float | None = None  # what the listing itself says shipping costs
    currency: str = "USD"
    via_zero: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class Shipping:
    """Seller shipping rule: `under` below `threshold`, `over` at/above it. threshold=None => no free-shipping tier."""
    under: float
    threshold: float | None = None
    over: float = 0.0
    known: bool = True  # False when we had to guess

    def to_dict(self):
        return asdict(self)


def condition_ok(cond: str, min_condition: str) -> bool:
    c = CONDITION_RANK.get(cond.lower().replace(" foil", "").strip(), 9)
    return c <= CONDITION_RANK.get(min_condition.lower(), 0)


# --------------------------------------------------------------------------- live


class LiveSource:
    name = "live"

    def __init__(self, delay: float = 0.4, max_per_product: int = 75):
        self.delay = delay
        self.max_per_product = max_per_product
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        (CACHE_DIR / "listings").mkdir(parents=True, exist_ok=True)

    def _cached(self, path: Path):
        if path.exists() and time.time() - path.stat().st_mtime < LISTING_TTL:
            return json.loads(path.read_text())
        return None

    def listings(self, pr: Printing, refresh: bool = False) -> list[Listing]:
        path = CACHE_DIR / "listings" / f"{pr.product_id}.json"
        raw = None if refresh else self._cached(path)
        if raw is None:
            raw = []
            frm = 0
            while frm < self.max_per_product:
                body = {
                    "filters": {
                        "term": {"sellerStatus": "Live", "channelId": 0, "language": ["English"]},
                        "range": {"quantity": {"gte": 1}},
                        "exclude": {"channelExclusion": 0},
                    },
                    "from": frm,
                    "size": 50,
                    "sort": {"field": "price+shipping", "order": "asc"},
                    "context": {"shippingCountry": "US", "cart": {}},
                    "aggregations": ["listingType"],
                }
                r = self.session.post(LISTINGS_URL.format(pid=pr.product_id), json=body, timeout=30)
                r.raise_for_status()
                block = r.json()["results"][0]
                page = block.get("results", [])
                raw += page
                frm += 50
                time.sleep(self.delay)
                if len(page) < 50 or frm >= block.get("totalResults", 0):
                    break
            path.write_text(json.dumps(raw))
        out = []
        for l in raw:
            if l.get("listingType") == "custom":  # photo listings; skip, often odd items
                continue
            direct = bool(l.get("directSeller") and l.get("directInventory", 0) > 0)
            out.append(
                Listing(
                    listing_id=str(l.get("listingId")),
                    product_id=pr.product_id,
                    seller_key=DIRECT_KEY if direct else str(l.get("sellerKey") or l.get("sellerId")),
                    seller_name="TCGplayer Direct" if direct else l.get("sellerName", "?"),
                    seller_id=l.get("sellerId"),
                    price=float(l["price"]),
                    qty=int(l.get("quantity", 0)),
                    condition=l.get("condition", "Unknown"),
                    printing=l.get("printing", "Normal"),
                    direct=direct,
                    fallback_shipping=l.get("sellerShippingPrice", l.get("shippingPrice")),
                )
            )
        return out

    def shipping(self, listings: list[Listing], defaults: dict) -> dict[str, Shipping]:
        """Look up each seller's US shipping rule (base price + free-shipping threshold)."""
        sellers = {l.seller_key: l for l in listings if l.seller_key != DIRECT_KEY}
        cache_path = CACHE_DIR / "shipping.json"
        cache = {}
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 24 * 3600:
            cache = json.loads(cache_path.read_text())
        todo = [l for k, l in sellers.items() if k not in cache and l.seller_id is not None]
        for i in range(0, len(todo), 50):
            chunk = todo[i : i + 50]
            try:
                r = self.session.post(
                    SHIPPING_URL,
                    json=[{"sellerId": l.seller_id, "largestShippingCategoryId": 1} for l in chunk],
                    timeout=30,
                )
                r.raise_for_status()
                for row in r.json().get("results", []):
                    for item in row if isinstance(row, list) else [row]:
                        rule = _parse_shipping_row(item)
                        key = str(item.get("sellerKey") or "")
                        if not key:
                            sid = item.get("sellerId")
                            key = next((l.seller_key for l in chunk if l.seller_id == sid), "")
                        if key and rule:
                            cache[key] = rule.to_dict()
            except Exception as e:  # endpoint shape changed / blocked -> fall back to defaults
                print(f"[shipping] lookup failed ({e}); using listing shipping + defaults")
                break
            time.sleep(self.delay)
        cache_path.write_text(json.dumps(cache))

        out: dict[str, Shipping] = {}
        for key, l in sellers.items():
            if key in cache:
                out[key] = Shipping(**cache[key])
            else:
                out[key] = Shipping(
                    under=float(l.fallback_shipping if l.fallback_shipping is not None else defaults["unknown_shipping"]),
                    threshold=defaults.get("unknown_threshold"),
                    known=False,
                )
        out[DIRECT_KEY] = Shipping(under=defaults["direct_shipping"], threshold=defaults["direct_threshold"])
        return out


def _parse_shipping_row(item: dict) -> Shipping | None:
    """Accepts a few plausible shapes of TCGplayer's seller shipping payload."""
    opts = item.get("sellerShippingOptions") or item.get("shippingOptions") or [item]
    best = None
    for o in opts:
        under = o.get("shippingPriceUnderThreshold", o.get("shippingPrice"))
        thr = o.get("thresholdPrice", o.get("freeShippingThreshold"))
        over = o.get("shippingPriceOverThreshold", 0.0 if thr else None)
        if under is None:
            continue
        s = Shipping(under=float(under), threshold=float(thr) if thr else None, over=float(over or 0.0))
        if best is None or s.under < best.under:
            best = s
    return best


# --------------------------------------------------------------------------- demo


class DemoSource:
    """Deterministic fake market: ~60 sellers with realistic-ish shipping rules and prices around market."""

    name = "demo"

    def __init__(self, seed: int = 7):
        rng = random.Random(seed)
        self.sellers = {}
        for i in range(60):
            under = rng.choice([0.99, 1.31, 1.31, 1.99, 2.49, 3.99, 4.99])
            thr = rng.choice([None, 5, 10, 10, 25, 35, 35, 50])
            self.sellers[f"s{i:02d}"] = (f"DemoSeller{i:02d}", Shipping(under=under, threshold=thr))

    def listings(self, pr: Printing, refresh: bool = False) -> list[Listing]:
        ref = Catalog.reference_price(pr) or 0.25
        h = int(hashlib.md5(str(pr.product_id).encode()).hexdigest(), 16)
        rng = random.Random(h)
        keys = rng.sample(sorted(self.sellers), k=rng.randint(8, 30))
        out = []
        for k in keys:
            for cond in rng.sample(["Near Mint", "Near Mint", "Lightly Played", "Moderately Played"], k=rng.randint(1, 2)):
                mult = {"Near Mint": 1.0, "Lightly Played": 0.9, "Moderately Played": 0.75}[cond]
                price = max(0.05, round(ref * mult * rng.uniform(0.8, 1.45), 2))
                out.append(Listing(f"{k}-{pr.product_id}-{cond[:2]}", pr.product_id, k, self.sellers[k][0],
                                   price, rng.choice([1, 1, 2, 3, 4, 8]), cond, "Normal"))
        if rng.random() < 0.5:
            out.append(Listing(f"d-{pr.product_id}", pr.product_id, DIRECT_KEY, "TCGplayer Direct",
                               round(ref * rng.uniform(1.0, 1.2), 2), 4, "Near Mint", "Normal", direct=True))
        return out

    def shipping(self, listings: list[Listing], defaults: dict) -> dict[str, Shipping]:
        out = {k: v[1] for k, v in self.sellers.items()}
        out[DIRECT_KEY] = Shipping(under=defaults["direct_shipping"], threshold=defaults["direct_threshold"])
        return out
