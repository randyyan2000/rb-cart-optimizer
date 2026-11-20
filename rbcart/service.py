"""Glue: deck + owned + selections -> demands -> listings -> optimized cart."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .catalog import Catalog, load_catalog, norm
from .deck import is_rune_section, parse_deck
from .listings import DemoSource, LiveSource, condition_ok
from .optimize import Demand, Options, greedy, optimize

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state.json"
DEFAULT_DECK = ROOT / "deck.txt"

DEFAULT_SETTINGS = {
    "source": "live",
    "min_condition": "Lightly Played",
    "allow_foil": False,
    "allow_variants": False,  # alternate art / overnumbered / promo printings
    "max_sellers": None,
    "seller_penalty": 0.0,
    "extra_copies": 0,
    "direct_threshold": 50.0,
    "direct_shipping": 3.99,
    "unknown_shipping": 1.99,
    "unknown_threshold": None,
}


def load_state() -> dict:
    state = {"deck_text": DEFAULT_DECK.read_text() if DEFAULT_DECK.exists() else "", "owned": {}, "modes": {},
             "settings": dict(DEFAULT_SETTINGS)}
    if STATE_FILE.exists():
        saved = json.loads(STATE_FILE.read_text())
        state.update({k: v for k, v in saved.items() if k != "settings"})
        state["settings"].update(saved.get("settings", {}))
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def default_mode(section: str) -> str:
    return "skip" if is_rune_section(section) else "buy"


def entry_key(section: str, name: str) -> str:
    return f"{section}|{norm(name)}"


def resolve(deck_text: str, catalog: Catalog) -> list[dict]:
    rows = []
    for e in parse_deck(deck_text):
        printings = catalog.lookup(e.name)
        std = [p for p in printings if p.is_standard] or printings
        prices = [Catalog.reference_price(p) for p in std]
        prices = [p for p in prices if p]
        rows.append({
            **e.to_dict(),
            "key": entry_key(e.section, e.name),
            "card": norm(e.name),
            "default_mode": default_mode(e.section),
            "matched": bool(printings),
            "printings": [p.to_dict() for p in printings],
            "market": min(prices) if prices else None,
        })
    return rows


def compute_needs(rows: list[dict], owned: dict, modes: dict) -> dict[str, dict]:
    """Per card: how many to buy, how many are optional filler, after subtracting owned copies."""
    agg = defaultdict(lambda: {"need": 0, "filler": 0, "name": "", "market": None})
    for r in rows:
        mode = modes.get(r["key"], r["default_mode"])
        a = agg[r["card"]]
        a["name"] = a["name"] or r["name"]
        a["market"] = r["market"]
        if mode == "buy":
            a["need"] += r["qty"]
        elif mode == "filler":
            a["filler"] += r["qty"]
    for card, a in agg.items():
        have = int(owned.get(card, 0) or 0)
        left = max(0, have - a["need"])
        a["owned"] = have
        a["buy"] = max(0, a["need"] - have)
        a["filler_buy"] = max(0, a["filler"] - left)
    return dict(agg)


def run(deck_text: str, owned: dict, modes: dict, settings: dict, refresh: bool = False) -> dict:
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    catalog = load_catalog()
    rows = resolve(deck_text, catalog)
    needs = compute_needs(rows, owned, modes)
    source = DemoSource() if s["source"] == "demo" else LiveSource()

    warnings = []
    demands: list[Demand] = []
    product_info = {}
    for card, a in needs.items():
        for mode, qty in (("need", a["buy"]), ("filler", a["filler_buy"])):
            if qty <= 0:
                continue
            printings = catalog.lookup(a["name"])
            if not printings:
                warnings.append(f"No TCGplayer product found for '{a['name']}'")
                continue
            if not s["allow_variants"]:
                printings = [p for p in printings if p.is_standard] or printings
            ls = []
            foil_only = {p.product_id for p in printings if p.prices and "Normal" not in p.prices}
            for p in printings:
                product_info[p.product_id] = {"name": p.name, "set": p.set_abbr, "url": p.url}
                try:
                    ls += source.listings(p, refresh=refresh)
                except Exception as e:
                    warnings.append(f"Listings fetch failed for {p.name} ({p.product_id}): {e}")
            ls = [l for l in ls if condition_ok(l.condition, s["min_condition"])
                  and (s["allow_foil"] or l.printing.lower() == "normal" or l.product_id in foil_only)]
            value = float(a["market"] or 0) if mode == "filler" else 0.0
            demands.append(Demand(key=f"{card}:{mode}", name=a["name"], qty=qty, mode=mode, value=value, listings=ls))

    all_listings = [l for d in demands for l in d.listings]
    shipping = source.shipping(all_listings, s) if all_listings else {}

    opts = Options(
        max_sellers=int(s["max_sellers"]) if s.get("max_sellers") else None,
        seller_penalty=float(s.get("seller_penalty") or 0),
        extra_copies=int(s.get("extra_copies") or 0),
    )
    base = greedy(demands, shipping)
    best = optimize(demands, shipping, opts)

    for res in (base, best):
        for sel in res["sellers"]:
            for it in sel["items"]:
                it.update({"set": product_info.get(it["product_id"], {}).get("set", ""),
                           "url": product_info.get(it["product_id"], {}).get("url", ""),
                           "product_name": product_info.get(it["product_id"], {}).get("name", it["card"])})
        if any(not sel["shipping_known"] for sel in res["sellers"]):
            res["shipping_note"] = "Some sellers' shipping rules couldn't be fetched; their shipping is estimated."

    lines = defaultdict(int)
    for sel in best["sellers"]:
        for it in sel["items"]:
            lines[(it["card"], it["set"])] += it["qty"]
    best["mass_entry"] = "\n".join(f"{q} {c}" + (f" [{st}]" if st else "") for (c, st), q in sorted(lines.items()))

    return {"rows": rows, "needs": needs, "baseline": base, "optimized": best, "warnings": warnings,
            "source": source.name}
