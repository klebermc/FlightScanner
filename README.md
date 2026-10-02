# FlightScanner

Scans a range of departure/return date combinations for a fixed origin/destination and trip-length window, and reports the cheapest fare found.

## Structure

```
FlightScanner/
├── src/
│   ├── flight_scanner.py            # current version — queries Kiwi's Tequila/Skypicker API
│   └── legacy/
│       └── google_flights_scanner.py  # earlier attempt — scrapes Google Flights' internal RPC endpoint
└── requirements.txt
```

## What it does

Both scripts do the same thing conceptually: for every day in a target month, and every trip length in a `TRIP_MIN`–`TRIP_MAX` day range, they query a flight-price source for that departure/return pair and print the price. At the end, they report the cheapest combination found.

- **`src/flight_scanner.py`** (current) — queries `api.skypicker.com` (Kiwi's older Tequila API) via `requests.get`, parses `data[0]["price"]` from the JSON response. Config (origin, destination, year, month, trip length range) is set as constants at the top of the file.
- **`src/legacy/google_flights_scanner.py`** — an earlier attempt that POSTs to Google Flights' undocumented internal RPC endpoint and regex-extracts a price from the response blob. Kept for reference only: it currently only runs one date pair (there's a debug `break` left in the loop) and has a leftover debug `print(r.text)`. Not recommended as a starting point without cleanup, since scraping an undocumented endpoint is fragile.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python src/flight_scanner.py
```

Edit the constants at the top of the file (`ORIGIN`, `DEST`, `YEAR`, `MONTH`, `TRIP_MIN`, `TRIP_MAX`) to change the search.

## Key dependencies

- `requests`

## Status

Working prototype. `flight_scanner.py` is the actively maintained version; the Google Flights RPC approach in `legacy/` was abandoned in favor of it and is not currently functional as committed (limited to one iteration, has debug output left in). No tests, no CLI args — all configuration is via constants in the script.

## License

MIT — see [LICENSE](LICENSE).
