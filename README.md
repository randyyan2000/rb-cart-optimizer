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

## Listing sources (Settings → Listings source)

| Source | What it is | Shipping |
|---|---|---|
| **TCGplayer live listings** | Real seller listings from the endpoints tcgplayer.com's pages use (unofficial). | Each seller's own rate and free-shipping minimum, looked up automatically. |
| **TCGplayer daily snapshot** | TCGplayer's daily lowest price for each printing, from tcgcsv.com. No individual sellers. | Pessimistic estimate: each card is assumed to come from a different store at the "snapshot shipping" price. Lowest prices can be any condition. Good for a ballpark only. |
| **CardTrader** | CardTrader's official API. Needs `CARDTRADER_TOKEN` (see below). | The API has no shipping prices. With "CardTrader Zero only", the Zero sellers' items are combined into one package at the CardTrader Zero shipping price you enter. Otherwise every seller is its own package at the per-seller estimate. Look up CardTrader's current US rates and enter them. |
| **Demo** | Made-up sellers for trying the UI. | Made up. |

### CardTrader setup

1. Get your API token in CardTrader under Settings → API.
2. Start the app with it set:

```bash
export CARDTRADER_TOKEN=...   # keep it out of git
python -m rbcart
```

Cards are matched to CardTrader using the TCGplayer product ID CardTrader stores for each card, with the card name as a fallback. Riftbound is CardTrader game 22. Prices are in your CardTrader account's currency, and the app warns if that isn't USD.

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
