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


def test_cardtrader_product_parsing():
    from rbcart.sources_extra import CT_ZERO_KEY, parse_ct_product
    p = {"id": 9, "blueprint_id": 5, "quantity": 3, "graded": False, "on_vacation": False,
         "properties_hash": {"condition": "Slightly Played", "riftbound_language": "en", "riftbound_foil": True},
         "user": {"username": "bob", "can_sell_via_hub": True, "country_code": "IT"},
         "price": {"cents": 125, "currency": "USD"}}
    l = parse_ct_product(p, 111, zero_only=True)
    assert (l.seller_key, l.price, l.qty, l.printing, l.currency) == (CT_ZERO_KEY, 1.25, 3, "Foil", "USD")
    assert parse_ct_product({**p, "user": {"username": "x", "can_sell_via_hub": False}}, 111, True) is None
    assert parse_ct_product(p, 111, zero_only=False).seller_key == "ct-bob"
    fr = {**p, "properties_hash": {**p["properties_hash"], "riftbound_language": "fr"}}
    assert parse_ct_product(fr, 111, True) is None


def test_cardtrader_conditions_map():
    from rbcart.listings import condition_ok
    assert condition_ok("Slightly Played", "Lightly Played")
    assert not condition_ok("Played", "Lightly Played")
