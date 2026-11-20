"""Exact multi-seller cart optimization (mixed-integer program) with free-shipping thresholds."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pulp

from .listings import Listing, Shipping

SHORT_PENALTY = 1000.0  # cost per copy we could not source at all


@dataclass
class Demand:
    key: str
    name: str
    qty: int
    mode: str = "need"  # "need": must buy qty; "filler": buy 0..qty only if it lowers the net total
    value: float = 0.0  # filler only: what one copy is worth to you (default: market price)
    listings: list[Listing] = field(default_factory=list)


@dataclass
class Options:
    max_sellers: int | None = None
    seller_penalty: float = 0.0  # "hassle cost" per extra package
    extra_copies: int = 0  # allow up to N spare copies of a needed card if it crosses a threshold
    per_card_candidates: int = 25
    time_limit: int = 60


def shipping_cost(rule: Shipping, subtotal: float) -> float:
    if subtotal <= 0:
        return 0.0
    if rule.threshold is not None and subtotal >= rule.threshold - 1e-9:
        return rule.over
    return rule.under


def prune(demands: list[Demand], opts: Options) -> None:
    """Keep the cheapest N listings per card plus every listing from sellers that stock several of our cards
    (those are the sellers where consolidating to hit a free-shipping threshold can pay off)."""
    seller_cards = defaultdict(set)
    for d in demands:
        for l in d.listings:
            seller_cards[l.seller_key].add(d.key)
    for d in demands:
        ls = sorted(d.listings, key=lambda l: l.price)
        keep = ls[: opts.per_card_candidates]
        keep_ids = {id(l) for l in keep}
        keep += [l for l in ls if id(l) not in keep_ids and len(seller_cards[l.seller_key]) >= 3 and l.price <= 3 * ls[0].price + 1]
        d.listings = keep


def _summarize(demands, shipping, chosen: dict[int, int], short: dict[str, int], label: str):
    by_seller: dict[str, dict] = {}
    card_cost = 0.0
    filler_value = 0.0
    for d in demands:
        for l in d.listings:
            q = chosen.get(id(l), 0)
            if q <= 0:
                continue
            s = by_seller.setdefault(
                l.seller_key, {"seller_key": l.seller_key, "seller_name": l.seller_name, "items": [], "subtotal": 0.0}
            )
            s["items"].append({"card": d.name, "mode": d.mode, "qty": q, "price": l.price, "condition": l.condition,
                               "printing": l.printing, "product_id": l.product_id, "listing_id": l.listing_id})
            s["subtotal"] += q * l.price
            card_cost += q * l.price
            if d.mode == "filler":
                filler_value += q * d.value
    ship_total = 0.0
    for key, s in by_seller.items():
        rule = shipping.get(key, Shipping(under=0.0, known=False))
        s["subtotal"] = round(s["subtotal"], 2)
        s["shipping"] = round(shipping_cost(rule, s["subtotal"]), 2)
        s["threshold"] = rule.threshold
        s["base_shipping"] = rule.under
        s["shipping_known"] = rule.known
        s["free_shipping"] = rule.threshold is not None and s["subtotal"] >= rule.threshold - 1e-9
        s["over_by"] = round(s["subtotal"] - rule.threshold, 2) if s["free_shipping"] else None
        s["short_by"] = round(rule.threshold - s["subtotal"], 2) if rule.threshold and not s["free_shipping"] else None
        ship_total += s["shipping"]
    sellers = sorted(by_seller.values(), key=lambda s: -s["subtotal"])
    return {
        "label": label,
        "sellers": sellers,
        "card_cost": round(card_cost, 2),
        "shipping": round(ship_total, 2),
        "total": round(card_cost + ship_total, 2),
        "filler_value": round(filler_value, 2),
        "packages": len(sellers),
        "short": {k: v for k, v in short.items() if v > 0},
    }


def greedy(demands: list[Demand], shipping: dict[str, Shipping]) -> dict:
    """Naive baseline: cheapest copy of every needed card, ignoring shipping."""
    chosen, short = {}, {}
    for d in demands:
        if d.mode != "need":
            continue
        left = d.qty
        for l in sorted(d.listings, key=lambda l: l.price):
            take = min(left, l.qty)
            if take:
                chosen[id(l)] = take
                left -= take
            if not left:
                break
        short[d.name] = left
    return _summarize(demands, shipping, chosen, short, "Cheapest copy per card (naive)")


def optimize(demands: list[Demand], shipping: dict[str, Shipping], opts: Options) -> dict:
    prune(demands, opts)
    prob = pulp.LpProblem("cart", pulp.LpMinimize)
    x: dict[int, pulp.LpVariable] = {}
    by_seller: dict[str, list[tuple[Listing, pulp.LpVariable]]] = defaultdict(list)
    objective = []
    shorts = {}

    for di, d in enumerate(demands):
        vars_ = []
        for li, l in enumerate(d.listings):
            v = pulp.LpVariable(f"x_{di}_{li}", 0, l.qty, cat="Integer")
            x[id(l)] = v
            vars_.append(v)
            by_seller[l.seller_key].append((l, v))
            objective.append(l.price * v - (d.value * v if d.mode == "filler" else 0))
        if d.mode == "need":
            short = pulp.LpVariable(f"short_{di}", 0, d.qty, cat="Integer")
            shorts[d.name] = short
            objective.append(SHORT_PENALTY * short)
            prob += pulp.lpSum(vars_) + short >= d.qty
            prob += pulp.lpSum(vars_) <= d.qty + opts.extra_copies
        else:
            prob += pulp.lpSum(vars_) <= d.qty

    y = {}
    for si, (key, items) in enumerate(by_seller.items()):
        rule = shipping.get(key, Shipping(under=0.0, known=False))
        ys = pulp.LpVariable(f"y_{si}", cat="Binary")
        y[key] = ys
        for l, v in items:
            prob += v <= l.qty * ys
        spend = pulp.lpSum(l.price * v for l, v in items)
        objective.append((rule.under + opts.seller_penalty) * ys)
        if rule.threshold is not None and rule.under > rule.over:
            zs = pulp.LpVariable(f"z_{si}", cat="Binary")
            prob += zs <= ys
            prob += spend >= rule.threshold * zs
            objective.append(-(rule.under - rule.over) * zs)

    if opts.max_sellers:
        prob += pulp.lpSum(y.values()) <= opts.max_sellers

    prob += pulp.lpSum(objective)
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=opts.time_limit))

    chosen = {k: int(round(v.value() or 0)) for k, v in x.items()}
    short = {name: int(round(v.value() or 0)) for name, v in shorts.items()}
    res = _summarize(demands, shipping, chosen, short, "Optimized")
    res["status"] = pulp.LpStatus[status]
    return res
