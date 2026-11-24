"""Additional listing sources: TCGplayer daily snapshot (tcgcsv) and CardTrader's official API."""
from __future__ import annotations

import json
import os
import time

import requests

from .catalog import CACHE_DIR, Printing, norm
from .listings import DIRECT_KEY, Listing, Shipping

# --------------------------------------------------------------------------- snapshot


class SnapshotSource:
    """Estimate from TCGplayer's daily price snapshot (via tcgcsv). No per-seller data exists there, so:
      * each printing's 'low' price is modelled as its own small seller (assumed to charge `snapshot_shipping`,
        no free-shipping tier) -- a pessimistic 'every card from a different store' view;
      * each printing's 'direct low' price is modelled as TCGplayer Direct (one package, free over the minimum).
    The optimizer then picks the cheaper mix. Good for a ballpark; real carts need live listings."""

    name = "snapshot"

    def listings(self, pr: Printing, refresh: bool = False) -> list[Listing]:
        out = []
        for sub, p in pr.prices.items():
            if p.get("low"):
                out.append(Listing(f"low-{pr.product_id}-{sub}", pr.product_id, f"low-{pr.product_id}-{sub}",
                                   f"Lowest TCGplayer listing ({pr.name})", float(p["low"]), 99, "Any", sub))
            if p.get("direct_low"):
                out.append(Listing(f"dl-{pr.product_id}-{sub}", pr.product_id, DIRECT_KEY, "TCGplayer Direct",
                                   float(p["direct_low"]), 99, "Near Mint", sub, direct=True))
        return out

    def shipping(self, listings: list[Listing], defaults: dict) -> dict[str, Shipping]:
        out = {l.seller_key: Shipping(under=float(defaults["snapshot_shipping"]), known=False)
               for l in listings if l.seller_key != DIRECT_KEY}
        out[DIRECT_KEY] = Shipping(under=defaults["direct_shipping"], threshold=defaults["direct_threshold"])
        return out


# --------------------------------------------------------------------------- CardTrader

CT_BASE = "https://api.cardtrader.com/api/v2"
CT_GAME_RIFTBOUND = 22
CT_ZERO_KEY = "__ctzero__"


class CardTraderSource:
    """CardTrader's official API (token from the CARDTRADER_TOKEN environment variable).

    CardTrader exposes no per-seller shipping prices in its API. Two modes:
      * CardTrader Zero only (default): listings from sellers that ship via CardTrader's hub are pooled into
        one package with the shipping cost you enter in Settings;
      * all sellers: every seller is its own package at the per-seller shipping estimate from Settings.
    Prices are in your CardTrader account's currency."""

    name = "cardtrader"

    def __init__(self, delay: float = 0.25, zero_only: bool = True):
        token = os.environ.get("CARDTRADER_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Set the CARDTRADER_TOKEN environment variable (CardTrader > Settings > API) "
                               "before starting the app.")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self.delay = delay
        self.zero_only = zero_only
        self.currencies: set[str] = set()
        self._index = None

    def _get(self, path: str, **params):
        r = self.s.get(f"{CT_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()

    def index(self) -> dict:
        """tcgplayer product id -> [blueprint ids], plus name -> [blueprint ids] fallback. Cached 24h."""
        if self._index is not None:
            return self._index
        path = CACHE_DIR / "cardtrader_blueprints.json"
        if path.exists() and time.time() - path.stat().st_mtime < 24 * 3600:
            self._index = json.loads(path.read_text())
            return self._index
        by_tcg, by_name = {}, {}
        for exp in self._get("/expansions"):
            if exp.get("game_id") != CT_GAME_RIFTBOUND:
                continue
            for bp in self._get("/blueprints/export", expansion_id=exp["id"]):
                if bp.get("tcg_player_id"):
                    by_tcg.setdefault(str(bp["tcg_player_id"]), []).append(bp["id"])
                by_name.setdefault(norm(bp.get("name", "")), []).append(bp["id"])
        self._index = {"by_tcg": by_tcg, "by_name": by_name}
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(self._index))
        return self._index

    def listings(self, pr: Printing, refresh: bool = False) -> list[Listing]:
        idx = self.index()
        bps = idx["by_tcg"].get(str(pr.product_id))
        if not bps and pr.is_standard:  # no TCGplayer link on CardTrader's side: fall back to the name
            bps = idx["by_name"].get(norm(pr.name), [])
        out = []
        for bp in bps or []:
            cache = CACHE_DIR / "listings" / f"ct-{bp}.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < 3600:
                data = json.loads(cache.read_text())
            else:
                data = self._get("/marketplace/products", blueprint_id=bp)
                cache.write_text(json.dumps(data))
            out += [l for l in (parse_ct_product(p, pr.product_id, self.zero_only) for p in data.get(str(bp), [])) if l]
            self.currencies |= {l.currency for l in out}
        return out

    def shipping(self, listings: list[Listing], defaults: dict) -> dict[str, Shipping]:
        out = {l.seller_key: Shipping(under=float(defaults["ct_seller_shipping"]), known=False)
               for l in listings if l.seller_key != CT_ZERO_KEY}
        out[CT_ZERO_KEY] = Shipping(under=float(defaults["ct_zero_shipping"]),
                                    threshold=defaults.get("ct_zero_threshold"), known=False)
        return out


def parse_ct_product(p: dict, product_id: int, zero_only: bool) -> Listing | None:
    props = p.get("properties_hash") or {}
    user = p.get("user") or {}
    if p.get("graded") or p.get("on_vacation") or props.get("altered") or props.get("signed"):
        return None
    lang = (props.get("riftbound_language") or "en").lower()
    if lang not in ("en", "english"):
        return None
    zero = bool(user.get("can_sell_via_hub"))
    if zero_only and not zero:
        return None
    price = p.get("price") or {}
    cents = price.get("cents", p.get("price_cents"))
    if cents is None:
        return None
    l = Listing(
        listing_id=f"ct-{p.get('id')}",
        product_id=product_id,
        seller_key=CT_ZERO_KEY if zero_only else f"ct-{user.get('username')}",
        seller_name="CardTrader Zero" if zero_only else f"{user.get('username')} ({user.get('country_code', '?')})",
        price=cents / 100,
        qty=int(p.get("quantity") or 0),
        condition=props.get("condition") or "Unknown",
        printing="Foil" if props.get("riftbound_foil") else "Normal",
    )
    l.currency = price.get("currency") or p.get("price_currency") or "?"
    l.via_zero = zero
    return l
