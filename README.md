# Riftbound Cart Optimizer

A local web app that builds the cheapest TCGplayer cart for a Riftbound deck. It solves exactly for card prices **plus shipping**, using each seller's free-shipping minimum.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m rbcart            # then open http://127.0.0.1:5057
```

Python 3.10 or newer. The first start downloads the Riftbound catalog from tcgcsv.com (about 30 seconds, then cached for a day).

## Using it

- **Deck list:** open "Deck list" and paste any deck in the `Section:` / `3 Card Name` export format. `deck.txt` is the default.
- **Buy / Filler / Skip** for each card or whole section:
  - **Buy:** must be in the cart.
  - **Filler:** bought only when it pays for itself. A filler card counts as worth its market price, so it's added when it costs about that or less, or when it pushes an order over a seller's free-shipping minimum and saves more than it costs. Good for sideboard cards or cards you'd want eventually.
  - **Skip:** left out. Rune Pool starts as skip.
- **Owned:** copies you already have, for each card. They count against Buy first, then Filler. The Owned box is shared by every row with the same card, so 2 owned copies of Vi, Peacekeeper cover both the main-deck and sideboard copies.
- **Settings:** worst acceptable condition, foils, alt-art and promo printings, maximum packages, a "hassle cost" per package, spare copies allowed (buying an extra copy is sometimes cheaper than paying shipping), and the TCGplayer Direct free-shipping minimum.
- Your choices are saved to `state.json`.

The results show each seller's items, subtotal, and shipping, and how far over or under their free-shipping minimum the order is. They also show a naive "cheapest copy of every card" cart for comparison.

## How the optimizer works

It's a mixed-integer program (PuLP + CBC). For every listing it chooses how many copies to buy. For every seller it chooses whether to order from them at all (if so, you pay their shipping), and whether the order reaches their free-shipping minimum (only allowed if their subtotal is at least the minimum). It minimizes card cost + shipping paid + per-package hassle cost − value of filler bought. It considers the cheapest 25 listings per card, plus listings from any seller who stocks 3 or more of the cards you need.

## Data sources and caveats

- **Catalog and market prices:** [tcgcsv.com](https://tcgcsv.com), a daily mirror of TCGplayer's catalog. Riftbound is category 89.
- **Seller listings and shipping:** the unofficial endpoints tcgplayer.com's own product pages use. TCGplayer's official API no longer accepts new developers. These endpoints are undocumented: they can change or block automated use, and you're responsible for staying within TCGplayer's terms. The tool waits between requests and caches listings for 1 hour and seller shipping rules for 24 hours (`.cache/`).
- If seller shipping rules can't be fetched, those sellers are flagged **shipping estimated**. Their listing's shipping price is used and no free-shipping minimum is assumed.
- **Demo** (in Settings) uses made-up sellers so you can try the UI without network access.
- Prices and stock change quickly, so build the cart right after optimizing.

## Tests

```bash
pip install pytest && python -m pytest -q
```
