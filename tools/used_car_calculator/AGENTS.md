# Browsing for listings — the assistant's contract

This file is the instruction set for an AI assistant (Claude, or any agent with web access)
asked to find cars and load them into the Used Car Deal Analyzer.

The analyzer scores one car at a time and ranks a whole market when given many. Your job is
to turn a shopping brief into **a JSON array of listings in the schema below**, then hand it
to `scrape_listings.py`. The tool does the scoring, comparables and ranking — you do the
finding and the reading.

---

## 1. Get the brief

You need, at minimum, what to look for and where. Ask for anything missing:

- **What** — make/model, or a class ("reliable midsize sedan", "3-row SUV")
- **Budget** — maximum out-the-door, or maximum monthly
- **Where** — ZIP or city, and how far they will travel
- **Constraints** — maximum mileage, minimum year, clean title only, no accidents,
  private sellers welcome or dealers only

## 2. Search broadly, then read

Aim for **150–400 listings**. The cohort is what makes the comparables work: fewer than
about 30 similar cars and the fair-value estimate falls back to thin evidence.

Search across several sources so no single site's pricing skews the market picture — the
large aggregators, manufacturer certified-pre-owned inventories, big dealer groups, and the
classified sites where private sellers list. Vary the query: by model, by trim, by year, by
nearby metro, by price band. A single query rarely returns the whole market.

Read enough of each listing to fill the schema. Do **not** guess: a field you did not see
must be `null`. A wrong mileage or an invented accident history is worse than a missing one,
because the score will quietly believe it.

## 3. Emit this schema

```json
{
  "query": "2018-2021 Toyota Camry / Honda Accord under $25k, within 100mi of 22102",
  "listings": [
    {
      "year": 2019,
      "make": "Toyota",
      "model": "Camry",
      "trim": "SE",
      "miles": 52341,
      "price": 18995,
      "vin": "4T1B11HK5KU123456",
      "title": "clean",
      "accidents": "none",
      "owners": 1,
      "condition": "good",
      "seller": "dealer",
      "dealer": "Example Motors",
      "city": "Vienna",
      "state": "VA",
      "zip": "22182",
      "distance": 12,
      "daysOnMarket": 34,
      "priceDrop": 500,
      "url": "https://example.com/listing/123",
      "source": "example.com",
      "mpg": 32,
      "notes": "one-owner lease return, new tyres"
    }
  ]
}
```

**Required for a listing to be scored:** `year`, `make`, `model`, `price`, `miles`.
Everything else improves the result and may be `null`.

Field notes:

| Field | Say |
|---|---|
| `title` | `clean`, `rebuilt` (salvage/reconstructed) or `lemon`. Free text is matched too. |
| `accidents` | `none`, `minor`, `major`, or `unknown` when no report was shown. **Default to `unknown`,** not `none` — absence of a report is not absence of an accident. |
| `owners` | integer, `null` if not stated |
| `seller` | `dealer` or `private` |
| `distance` | miles from the buyer's ZIP, if the site shows it |
| `daysOnMarket` | a long-listed car is a negotiable car — capture it when shown |
| `priceDrop` | dollars the price has already fallen, if shown |
| `vin` | 17 characters. Omit rather than guess — the tool validates and discards malformed VINs. |
| `url` | **always include it.** It is how the user opens the car you found, and the site is derived from it, so duplicates across aggregators reconcile on their own. |
| `source` | optional — taken from the `url` when you leave it out |

## 4. Hand it over

**The simplest route needs no shell at all: paste your answer straight into the Market
section's box and press Load.** It is deliberately forgiving — it will read a bare array, a
`{"listings": […]}` envelope, JSON inside a markdown fence, one object per line, or a
CSV/TSV block, with or without a sentence of prose around it. So you can simply reply
normally and the user pastes the whole thing.

Field names are matched loosely too: `mileage`, `odometer`, `listPrice`, `link`,
`modelYear`, `sellerType` and similar all resolve. Use whatever is natural; do not
contort your output to match.

**Add to what is already there** with the *Add to what is loaded* button, so several
searches accumulate into one market. Duplicates across sites are reconciled automatically —
the cheaper sighting is kept and the other is recorded.

If the user prefers the shell:

```bash
python3 scrape_listings.py --from-json found.json --query "Camry/Accord under 25k" --merge
your-search | python3 scrape_listings.py --stdin --query "..."
```

Both accept the same forgiving formats.

## 5. Fetching pages yourself

`scrape_listings.py --fetch <url> ...` reads pages directly and extracts schema.org
`Vehicle` JSON-LD, which listing sites publish so machines can read their inventory. It
checks `robots.txt` first, waits two seconds between requests, and identifies itself in the
User-Agent.

It will not help on sites that render listings only in JavaScript — there, read the page
yourself and emit JSON.

**Terms of use are the operator's responsibility.** This is built for reading a handful of
pages you are already shopping, at human speed. Many listing sites restrict automated
collection; where a site offers an API (Marketcheck, Auto.dev), prefer it and put the key in
the tool's Sources panel.

## 6. What good work looks like

- Breadth over depth: 200 mediocre-quality listings beat 20 perfect ones, because the
  comparables come from the cohort.
- `unknown` wherever you did not see the answer.
- Both dealer and private listings — private sellers price differently, and the tool
  reports the split.
- The same car listed twice at two prices is a *finding*, not noise. Capture both; the tool
  keeps the cheapest and records the other.
- Tell the user what you could not cover: a site you could not read, a region you skipped.
- Every car carries its link. A ranked shortlist the user cannot click through to is
  half a result.
