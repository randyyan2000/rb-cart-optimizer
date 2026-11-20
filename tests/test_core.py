import pytest

from rbcart.deck import parse_deck
from rbcart.listings import Listing, Shipping
from rbcart.optimize import Demand, Options, greedy, optimize
from rbcart.service import compute_needs


def L(seller, price, qty=4, pid=1):
    return Listing(f"{seller}-{pid}-{price}", pid, seller, seller, price, qty, "Near Mint", "Normal")


def test_parse_sections():
    es = parse_deck("Legend:\n1 Azir, Emperor of the Sands\nMainDeck:\n3 Defy\n2 Defy\nSideboard:\n1 Vi, Peacekeeper\n")
    assert [(e.section, e.name, e.qty) for e in es] == [
        ("Legend", "Azir, Emperor of the Sands", 1), ("MainDeck", "Defy", 5), ("Sideboard", "Vi, Peacekeeper", 1)]


def test_owned_covers_need_then_filler():
    rows = [
        {"key": "Main|vi", "card": "vi", "name": "Vi", "qty": 1, "default_mode": "buy", "market": 3},
        {"key": "Side|vi", "card": "vi", "name": "Vi", "qty": 1, "default_mode": "buy", "market": 3},
    ]
    assert compute_needs(rows, {"vi": 1}, {})["vi"]["buy"] == 1
    n = compute_needs(rows, {"vi": 2}, {"Side|vi": "filler"})["vi"]
    assert (n["buy"], n["filler_buy"]) == (0, 0)


def test_consolidates_to_hit_free_shipping():
    # A is slightly cheaper per card but both orders pay shipping; B has free shipping over $10.
    demands = [
        Demand("a", "A", 2, listings=[L("cheap1", 2.00, pid=1), L("B", 2.50, pid=1)]),
        Demand("b", "B", 2, listings=[L("cheap2", 2.00, pid=2), L("B", 2.60, pid=2)]),
    ]
    ship = {"cheap1": Shipping(1.31), "cheap2": Shipping(1.31), "B": Shipping(3.99, threshold=10)}
    naive = greedy(demands, ship)
    best = optimize(demands, ship, Options())
    assert naive["total"] == pytest.approx(10.62)
    assert best["total"] == 10.20 and best["packages"] == 1


def test_filler_only_when_it_pays():
    demands = [
        Demand("a", "A", 3, listings=[L("S", 3.00)]),
        Demand("f", "F", 1, mode="filler", value=0.80, listings=[L("S", 0.90, pid=2), L("T", 0.85, pid=2)]),
    ]
    ship = {"S": Shipping(3.99, threshold=10), "T": Shipping(0.99)}
    best = optimize(demands, ship, Options())
    # $9 alone pays $3.99 shipping; adding a $0.90 filler hits $9.90 -> still short. Not worth it.
    assert best["total"] == 12.99
    ship["S"] = Shipping(3.99, threshold=9.5)
    best = optimize(demands, ship, Options())
    assert best["total"] == 9.90 and best["filler_value"] == 0.8


def test_max_sellers():
    demands = [Demand(k, k, 1, listings=[L(f"s{k}", 1.0, pid=i), L("big", 1.5, pid=i)]) for i, k in enumerate("abc")]
    ship = {"sa": Shipping(0), "sb": Shipping(0), "sc": Shipping(0), "big": Shipping(0)}
    assert optimize(demands, ship, Options(max_sellers=1))["packages"] == 1
