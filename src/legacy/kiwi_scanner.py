import requests
from datetime import date, timedelta

ORIGIN = "GRU"
DEST = "YYZ"
YEAR = 2025
MONTH = 12
TRIP_MIN = 10
TRIP_MAX = 20

def format_date(dt):
    return dt.strftime("%d/%m/%Y")

def get_price(dep, ret):
    url = "https://api.skypicker.com/flights"
    params = {
        "fly_from": ORIGIN,
        "fly_to": DEST,
        # Kiwi's API takes date *ranges*; setting from == to pins it to a
        # single exact departure/return date instead of a range search.
        "date_from": format_date(dep),
        "date_to": format_date(dep),
        "return_from": format_date(ret),
        "return_to": format_date(ret),
        "adults": 1,
        "curr": "CAD",
        "limit": 1,
        "partner": "picky"   # required by Kiwi
    }
    r = requests.get(url, params=params)
    data = r.json()
    if data.get("data"):
        return data["data"][0]["price"]
    return None

def run_scan():
    print(f"Scanning flights {ORIGIN} → {DEST} for December {YEAR}...\n")
    d = date(YEAR, MONTH, 1)
    # Handles the December → January rollover: if MONTH is 12, bump YEAR by
    # one and wrap MONTH to 1 via `% 12`.
    next_month = date(YEAR + (MONTH == 12), (MONTH % 12) + 1, 1)

    results = []
    while d < next_month:
        for stay in range(TRIP_MIN, TRIP_MAX+1):
            ret = d + timedelta(days=stay)
            # Only consider trips that return within the same target month.
            if ret < next_month:
                price = get_price(d, ret)
                results.append((d, ret, price))
                print(f"{d} → {ret}: {price}")
        d += timedelta(days=1)

    valid = [x for x in results if x[2] is not None]
    if valid:
        best = min(valid, key=lambda x: x[2])
        print("\n=== BEST PRICE ===")
        print(f"{best[0]} → {best[1]}: ${best[2]}")
    else:
        print("No flights found.")

run_scan()

