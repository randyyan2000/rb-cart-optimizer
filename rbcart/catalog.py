"""Riftbound product catalog + daily prices from tcgcsv.com (a public mirror of TCGplayer's catalog)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import requests

CATEGORY_ID = 89  # Riftbound: League of Legends Trading Card Game
BASE = "https://tcgcsv.com/tcgplayer"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
CATALOG_FILE = CACHE_DIR / "catalog.json"
CATALOG_TTL = 24 * 3600

# Printings that are never tournament-playable copies of a card.
EXCLUDED_VARIANTS = ("oversized", "metal")
PAREN_RE = re.compile(r"\s*\([^)]*\)")


def base_name(name: str) -> str:
    return PAREN_RE.sub("", name).strip()


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", base_name(name).lower()).strip()


@dataclass
class Printing:
    product_id: int
    name: str
    set_name: str
    set_abbr: str
    url: str
    image: str
    variant: str  # text in parentheses, "" for the standard printing
    prices: dict = field(default_factory=dict)  # subtype ("Normal"/"Foil") -> {"market":..,"low":..}

    @property
    def is_standard(self) -> bool:
        return self.variant == ""

    def to_dict(self):
        d = asdict(self)
        d["is_standard"] = self.is_standard
        return d


class Catalog:
    def __init__(self, products: list[dict], prices: list[dict], groups: list[dict]):
        gmap = {g["groupId"]: g for g in groups}
        pmap: dict[int, dict] = {}
        for p in prices:
            pmap.setdefault(p["productId"], {})[p.get("subTypeName") or "Normal"] = {
                "market": p.get("marketPrice"),
                "low": p.get("lowPrice"),
                "direct_low": p.get("directLowPrice"),
            }
        self.by_name: dict[str, list[Printing]] = {}
        self.by_id: dict[int, Printing] = {}
        for p in products:
            # Only real cards have a card number; this skips boxes, decks, bundles.
            if not any(e.get("name") == "Number" for e in p.get("extendedData", [])):
                continue
            name = p["name"]
            variants = " ".join(PAREN_RE.findall(name)).strip()
            if any(v in variants.lower() for v in EXCLUDED_VARIANTS):
                continue
            g = gmap.get(p["groupId"], {})
            pr = Printing(
                product_id=p["productId"],
                name=name,
                set_name=g.get("name", ""),
                set_abbr=g.get("abbreviation") or "",
                url=p.get("url", ""),
                image=p.get("imageUrl", ""),
                variant=variants.replace("(", "").replace(")", ""),
                prices=pmap.get(p["productId"], {}),
            )
            self.by_name.setdefault(norm(name), []).append(pr)
            self.by_id[pr.product_id] = pr

    def lookup(self, name: str) -> list[Printing]:
        return list(self.by_name.get(norm(name), []))

    @staticmethod
    def reference_price(pr: Printing) -> float | None:
        """A sensible 'what this card is worth' price: cheapest market (or low) across subtypes."""
        vals = [v.get("market") or v.get("low") for v in pr.prices.values()]
        vals = [v for v in vals if v]
        return min(vals) if vals else None


def _get(url: str) -> list[dict]:
    r = requests.get(url, timeout=30, headers={"User-Agent": "rb-cart-optimizer"})
    r.raise_for_status()
    return r.json()["results"]


def load_catalog(refresh: bool = False) -> Catalog:
    CACHE_DIR.mkdir(exist_ok=True)
    if not refresh and CATALOG_FILE.exists() and time.time() - CATALOG_FILE.stat().st_mtime < CATALOG_TTL:
        raw = json.loads(CATALOG_FILE.read_text())
    else:
        groups = _get(f"{BASE}/{CATEGORY_ID}/groups")
        products, prices = [], []
        for g in groups:
            gid = g["groupId"]
            products += _get(f"{BASE}/{CATEGORY_ID}/{gid}/products")
            prices += _get(f"{BASE}/{CATEGORY_ID}/{gid}/prices")
        raw = {"groups": groups, "products": products, "prices": prices}
        CATALOG_FILE.write_text(json.dumps(raw))
    return Catalog(raw["products"], raw["prices"], raw["groups"])
