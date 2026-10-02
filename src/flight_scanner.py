"""Log round-trip flight prices from Google Flights into a CSV.

Scans every departure date in a window x every trip length in a range, and
appends one row per itinerary Google returns. Designed to be left running:
requests are slow on purpose, every row is flushed immediately, and with
--repeat-hours it keeps re-scanning so the CSV builds a price history.
"""

import argparse
import csv
import json
import os
import random
import time
from datetime import date, datetime, timedelta

import airportsdata
from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from selectolax.lexbor import LexborHTMLParser

AIRPORT_COUNTRY = {code: a["country"] for code, a in airportsdata.load("IATA").items()}

CSV_FIELDS = [
    "scraped_at", "origin", "dest", "depart_date", "return_date", "stay_days",
    "rank", "price", "currency", "airlines", "stops", "route",
    "depart_time", "arrive_time", "duration_min",
]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--origin", default="GRU")
    p.add_argument("--dest", default="YYZ")
    p.add_argument("--depart-from", default=None, type=date.fromisoformat,
                   help="first departure date to scan (YYYY-MM-DD; default: 30 days from today)")
    p.add_argument("--depart-to", default=None, type=date.fromisoformat,
                   help="last departure date to scan (YYYY-MM-DD; default: --depart-from + 30 days)")
    p.add_argument("--latest-return", type=date.fromisoformat, default=None,
                   help="drop trips returning after this date (default: no limit)")
    p.add_argument("--trip-min", type=int, default=10, help="min stay in days")
    p.add_argument("--trip-max", type=int, default=20, help="max stay in days")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--currency", default="CAD")
    p.add_argument("--max-stops", type=int, default=None)
    p.add_argument("--avoid-countries", default="",
                   help="comma-separated ISO country codes whose airports must not be "
                        "used as connections on the outbound leg, e.g. US or US,PR")
    p.add_argument("--top", type=int, default=5,
                   help="itineraries to log per date pair (cheapest first)")
    p.add_argument("--delay", type=float, default=10.0,
                   help="base seconds between requests (random jitter is added)")
    p.add_argument("--repeat-hours", type=float, default=0,
                   help="re-run the whole scan every N hours; 0 = scan once")
    p.add_argument("--out", default="data/prices.csv")
    args = p.parse_args()
    if args.depart_from is None:
        args.depart_from = date.today() + timedelta(days=30)
    if args.depart_to is None:
        args.depart_to = args.depart_from + timedelta(days=30)
    args.avoid_countries = {c.strip().upper() for c in args.avoid_countries.split(",") if c.strip()}
    return args


def date_pairs(args):
    d = args.depart_from
    while d <= args.depart_to:
        for stay in range(args.trip_min, args.trip_max + 1):
            ret = d + timedelta(days=stay)
            if args.latest_return is None or ret <= args.latest_return:
                yield d, ret
        d += timedelta(days=1)


def fetch(args, dep, ret, retries=4):
    q = create_query(
        flights=[
            FlightQuery(date=dep.isoformat(), from_airport=args.origin, to_airport=args.dest),
            FlightQuery(date=ret.isoformat(), from_airport=args.dest, to_airport=args.origin),
        ],
        trip="round-trip",
        passengers=Passengers(adults=args.adults),
        currency=args.currency,
        language="en",
        max_stops=args.max_stops,
    )
    for attempt in range(retries):
        try:
            payload = page_payload(fetch_flights_html(q))
            if payload is not None and payload[2] is None and payload[3] is None:
                # Google only embeds results in the HTML for some searches
                # (seemingly ones it has cached); for the rest the page
                # loads them afterwards via JavaScript, which a plain HTTP
                # fetch never runs. A headless browser picks those up.
                payload = BROWSER.fetch_payload(q.url())
            return extract_itineraries(payload)
        except Exception as e:
            # Google occasionally serves a consent/throttle page instead of
            # results, which surfaces as a parse error. Backing off
            # exponentially (1, 2, 4... minutes) usually clears it.
            wait = 60 * 2 ** attempt
            print(f"  ! {type(e).__name__}: {e} — retrying in {wait}s")
            time.sleep(wait)
    return None


def page_payload(html):
    """Return the results payload embedded in the search page's HTML.

    Returns None for Google's "no flights" response (retrying won't help).
    """
    js = LexborHTMLParser(html).css_first(r"script.ds\:1").text()
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return None
    return json.loads(data)


class Browser:
    """Headless Chromium, started on first use and reused for the whole run."""

    RESULTS_RPC = "FlightsFrontendService/GetShoppingResults"

    def __init__(self):
        self._pw = self._browser = None

    def fetch_payload(self, url):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        page = self._browser.new_page(locale="en-US")
        responses = []
        page.on("response", lambda r: responses.append(r) if self.RESULTS_RPC in r.url else None)
        try:
            # Wait for the results RPC itself rather than "networkidle", which
            # sometimes fired before the RPC was even sent.
            with page.expect_response(lambda r: self.RESULTS_RPC in r.url, timeout=60_000):
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)  # Google sometimes sends a second, fuller batch
            bodies = [r.text() for r in responses]
        finally:
            page.close()
        payload = None
        for body in bodies:
            payload = rpc_payload(body) or payload
        if payload is None:
            raise RuntimeError("browser got no results response")
        return payload

    def close(self):
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()


BROWSER = Browser()


def rpc_payload(body):
    """Pull the results payload out of a GetShoppingResults response.

    The body is Google's batchexecute format: a `)]}'` guard line, then
    length-prefixed JSON chunks; the payload is a JSON string inside the
    `["wrb.fr", ...]` row. It has the same layout as the one embedded in the
    HTML, so extract_itineraries handles both. The last chunk with results wins.
    """
    payload = None
    for line in body.splitlines():
        if not line.startswith("[["):
            continue
        for row in json.loads(line):
            if row[0] == "wrb.fr" and row[2]:
                p = json.loads(row[2])
                if p[2] is not None or p[3] is not None:
                    payload = p
    return payload


def extract_itineraries(payload):
    """Extract itineraries from both result lists in a results payload.

    fast-flights' own parser only reads the "best flights" list (payload[3]),
    usually 3-5 entries. The "other flights" list (payload[2]) is often
    cheaper and is needed so that --avoid-countries doesn't filter a date
    pair down to nothing. Indices mirror fast_flights.parser.parse_js.
    """
    if payload is None:
        return []
    results, seen = [], set()
    for section in (payload[3], payload[2]):
        for k in ((section or [None])[0] or []):
            flight, price = k[0], k[1][0][1]
            if price is None:
                continue
            legs = [{
                "from": s[3], "to": s[6],
                "dep": (tuple(s[20]), s[8]), "arr": (tuple(s[21]), s[10]),
                "duration": s[11] or 0,
            } for s in flight[2]]
            key = (price, tuple((l["from"], l["to"], l["dep"][0], tuple(l["dep"][1] or ())) for l in legs))
            if key not in seen:  # the same itinerary can appear in both lists
                seen.add(key)
                results.append({"price": price, "airlines": flight[1], "legs": legs})
    return results


def fmt_time(dt):
    (y, m, d), t = dt
    hh, mm = (list(t or []) + [0, 0])[:2]
    return f"{y:04d}-{m:02d}-{d:02d} {hh or 0:02d}:{mm or 0:02d}"


def uses_avoided_country(itin, avoid):
    # Only intermediate stops count: origin/dest are the user's own choice.
    # Unknown codes (missing from airportsdata) are let through.
    return any(AIRPORT_COUNTRY.get(l["to"]) in avoid for l in itin["legs"][:-1])


def to_rows(args, dep, ret, results, scraped_at):
    # Google lists outbound options for a round-trip search; each `price` is
    # already the full round-trip total, so the outbound legs are what we log.
    # The return itinerary isn't on this page, so it can't be filtered.
    kept = [r for r in results if not uses_avoided_country(r, args.avoid_countries)]
    for rank, f in enumerate(sorted(kept, key=lambda f: f["price"])[:args.top], 1):
        legs = f["legs"]
        yield {
            "scraped_at": scraped_at,
            "origin": args.origin,
            "dest": args.dest,
            "depart_date": dep.isoformat(),
            "return_date": ret.isoformat(),
            "stay_days": (ret - dep).days,
            "rank": rank,
            "price": f["price"],
            "currency": args.currency,
            "airlines": " + ".join(f["airlines"]),
            "stops": len(legs) - 1,
            "route": "-".join([legs[0]["from"]] + [l["to"] for l in legs]),
            "depart_time": fmt_time(legs[0]["dep"]),
            "arrive_time": fmt_time(legs[-1]["arr"]),
            "duration_min": sum(l["duration"] for l in legs),
        }


def open_writer(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    fh = open(path, "a", newline="")
    w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
    if new:
        w.writeheader()
    return fh, w


def scan(args, writer, fh):
    pairs = list(date_pairs(args))
    print(f"Scanning {args.origin}→{args.dest}: {len(pairs)} date pairs, "
          f"~{max(1, round(len(pairs) * args.delay * 1.25 / 60))} min minimum"
          + (f", avoiding connections in {','.join(sorted(args.avoid_countries))}"
             if args.avoid_countries else ""))
    best = None
    for i, (dep, ret) in enumerate(pairs, 1):
        scraped_at = datetime.now().isoformat(timespec="seconds")
        results = fetch(args, dep, ret)
        rows = list(to_rows(args, dep, ret, results or [], scraped_at))
        for r in rows:
            writer.writerow(r)
        fh.flush()  # keep the CSV usable if the script is killed mid-scan

        cheapest = rows[0]["price"] if rows else None
        print(f"[{i}/{len(pairs)}] {dep} → {ret}: "
              f"{cheapest if cheapest is not None else 'no results'}")
        if cheapest is not None and (best is None or cheapest < best[2]):
            best = (dep, ret, cheapest, rows[0]["airlines"])

        # Jitter so requests don't land on a fixed rhythm, which is the
        # easiest pattern for Google's bot detection to flag.
        time.sleep(args.delay * random.uniform(1.0, 1.5))

    if best:
        print(f"\n=== BEST THIS SCAN === {best[0]} → {best[1]}: "
              f"{best[2]} {args.currency} ({best[3]})")
    else:
        print("\nNo flights found this scan.")


def main():
    args = parse_args()
    fh, writer = open_writer(args.out)
    try:
        while True:
            scan(args, writer, fh)
            if args.repeat_hours <= 0:
                break
            nxt = datetime.now() + timedelta(hours=args.repeat_hours)
            print(f"Next scan at {nxt:%Y-%m-%d %H:%M}\n")
            time.sleep(args.repeat_hours * 3600)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        BROWSER.close()
        fh.close()


if __name__ == "__main__":
    main()
