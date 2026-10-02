# FlightScanner

> **Note:** All code in this repository was written by Kleber Cabral. The README documentation and inline code comments were added with AI assistance (Claude).

Logs round-trip flight prices from Google Flights into a CSV. It scans every departure date in a window and every trip length in a range, then writes one row per itinerary. It's built to be left running for hours, and it can re-scan on a schedule to build a price history.

Free: no API key or account needed.

## Structure

```
FlightScanner/
├── src/
│   ├── flight_scanner.py              # current version — Google Flights via fast-flights
│   └── legacy/
│       ├── kiwi_scanner.py            # dead: Kiwi's public skypicker API now returns 404
│       └── google_flights_scanner.py  # dead: hand-rolled POST to Google's internal RPC
├── data/                              # CSV output (gitignored)
└── requirements.txt
```

## How it works

[`fast-flights`](https://github.com/AWeirdDev/fast-flights) builds the same protobuf-encoded `?tfs=` URL that the Google Flights web UI uses. It fetches the page with a Chrome-impersonating HTTP client and parses the embedded result data.

That only works for some searches. For others, Google's page arrives without results and its JavaScript loads them afterwards through a separate `GetShoppingResults` request, which a plain HTTP fetch never runs. When that happens, the script reloads the search in headless Chromium through Playwright and reads that request's response. Without this fallback, many date pairs would log "no results". The browser starts once, on first use, and is reused for the rest of the run.

The script reads both result lists on the page: "best flights" and "other flights". `fast-flights`' own parser only reads the first, and the second often holds the cheapest fares, usually via US hubs. Airport countries come from [`airportsdata`](https://pypi.org/project/airportsdata/), an offline table, so `--avoid-countries` makes no extra requests.

For each (departure, return) pair, the script logs the `--top` cheapest itineraries that pass the filters. Each price is the **full round-trip total**. The route, times and stops describe the outbound leg. Rows are flushed as soon as they're written, so a killed run still leaves a usable CSV.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # one-time ~115 MB download to ~/.cache/ms-playwright
```

## Run

```bash
# One pass over the defaults (GRU→YYZ, departures 30–60 days from today, 10–20 day stays)
.venv/bin/python src/flight_scanner.py

# Custom search, repeated every 6h to build a price history
.venv/bin/python src/flight_scanner.py --origin GRU --dest YYZ \
    --depart-from 2027-03-01 --depart-to 2027-03-31 --trip-min 10 --trip-max 20 \
    --latest-return 2027-04-15 --avoid-countries US --repeat-hours 6 --out data/gru_yyz.csv

# Keep it running after closing the terminal
nohup .venv/bin/python src/flight_scanner.py --repeat-hours 6 > scan.log 2>&1 &
```

### Parameters

All parameters are optional. `--help` prints the same list.

| Parameter | Default | Description |
|---|---|---|
| `--origin` | `GRU` | Departure airport as an IATA code. |
| `--dest` | `YYZ` | Destination airport as an IATA code. |
| `--depart-from` | today + 30 days | First departure date to scan (`YYYY-MM-DD`). |
| `--depart-to` | `--depart-from` + 30 days | Last departure date to scan (`YYYY-MM-DD`), inclusive. |
| `--latest-return` | none | Skip trips that return after this date (`YYYY-MM-DD`). With no value, a return can fall past `--depart-to`. |
| `--trip-min` | `10` | Shortest stay to scan, in days. |
| `--trip-max` | `20` | Longest stay to scan, in days, inclusive. |
| `--adults` | `1` | Number of adult passengers. The logged price covers all of them. |
| `--currency` | `CAD` | Currency code for prices (e.g. `BRL`, `USD`). |
| `--max-stops` | none | Maximum stops per direction. `0` means nonstop only. With no value, any number of stops is allowed. |
| `--avoid-countries` | none | Comma-separated ISO country codes. Itineraries that **connect** through an airport in any of these countries are dropped before ranking and logging, e.g. `--avoid-countries US` to skip US layovers (useful when you lack a US transit visa). The origin and destination are never filtered. US territories have their own codes, so use `US,PR,VI,GU` to exclude them too. Only the outbound route is checked; see Status. |
| `--top` | `5` | How many itineraries to log per date pair, cheapest first. |
| `--delay` | `10` | Base wait between requests, in seconds. The script adds 0–50% random jitter. Lowering it raises the risk of Google throttling. |
| `--repeat-hours` | `0` | Re-run the full scan every N hours and append to the same CSV. `0` scans once and exits. |
| `--out` | `data/prices.csv` | CSV file to append to. It's created, header included, if it doesn't exist. |

The scan covers every departure date from `--depart-from` to `--depart-to` × every stay from `--trip-min` to `--trip-max`, minus any trips cut by `--latest-return`. For example, the default 31 departure days × 11 stay lengths gives 341 requests. At the default delay, a pass takes about 60–85 minutes when results come from the plain fetch. Each pair that needs the browser fallback adds about 10 s, so a large scan can take up to ~2 hours.

### Output

CSV columns: `scraped_at, origin, dest, depart_date, return_date, stay_days, rank, price, currency, airlines, stops, route, depart_time, arrive_time, duration_min`.

`price` is the round-trip total. `stops`, `route`, `depart_time`, `arrive_time` and `duration_min` describe the outbound flight only. `duration_min` is the sum of flight times and excludes layovers.

## Key dependencies

- `fast-flights` (pulls in `primp` for the HTTP client, `protobuf` and `selectolax`)
- `airportsdata` (airport → country lookup)
- `playwright` + Chromium (fallback for results Google loads via JavaScript)

## Status

Working as of 2026-09-27. Tested live on a Brazil→Canada route.

- **`--avoid-countries` checks only the outbound flight.** For a round-trip search, Google's first results page shows outbound options, each priced with the cheapest matching return. The return route isn't on that page. Google's connecting-airport filter only works as an *include* list (flights may connect only at the listed airports), so exclusion can't be pushed into the search either. Check the return connections yourself before booking.
- The page carries roughly 5–10 itineraries per date pair, not Google's full expanded list, so heavy filtering can leave some pairs with no results.
- Relies on Google Flights' page format. If Google changes it, parsing breaks until `fast-flights` is updated (`pip install -U fast-flights`).
- The default is about 10–15 s between requests. Going much faster risks throttling. Failed requests retry with 1/2/4/8-minute backoff, then get logged as "no results".

## Cleanup notes

- **2026-09-27:** Replaced the Kiwi `skypicker` scanner, whose free public API is shut down (404), with a Google Flights scanner based on `fast-flights`. Added CLI args, CSV logging, repeat mode, jittered delays and retry/backoff. Moved the old script to `src/legacy/kiwi_scanner.py`.

- **2026-09-27:** Added `--avoid-countries`, which filters outbound connections by country via `airportsdata`. The scanner now also parses Google's "other flights" list, which `fast-flights` ignores. That roughly doubles the itineraries per pair and surfaces the cheapest fares. CSV format unchanged.
- **2026-09-27:** Fixed many date pairs logging "no results". Google only embeds results in the HTML for some searches, and loads the rest via JavaScript. Added a Playwright headless-browser fallback that captures the `GetShoppingResults` response, which has the same payload layout as the embedded data.

- **2026-10-02:** Sanitized for public release. Defaults are now GRU→YYZ with a relative date window (departures 30–60 days out) instead of hardcoded dates. Legacy scripts use the same route.

## License

MIT — see [LICENSE](LICENSE).
