import requests
import json
from datetime import date, timedelta

# -------------------------------
# CONFIGURATION
# -------------------------------
ORIGIN = "BSB"
DEST = "YYZ"
YEAR = 2025
MONTH = 12
TRIP_MIN = 10
TRIP_MAX = 20

URL = "https://www.google.com/flights/rpc?gl=US&hl=en&currency=USD"

HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "User-Agent": "Mozilla/5.0"
}

# -------------------------------
# Generate date combinations
# -------------------------------
def generate_pairs(year, month, stay_min, stay_max):
    pairs = []
    d = date(year, month, 1)

    # find last day of month
    next_month = date(year + (month == 12), (month % 12) + 1, 1)

    while d < next_month:
        for stay in range(stay_min, stay_max+1):
            ret = d + timedelta(days=stay)
            if ret < next_month:
                pairs.append((d, ret))
        d += timedelta(days=1)
    return pairs


# -------------------------------
# Query Google Flights RPC
# -------------------------------
def query_google_flights(dep, ret):
    payload = [
        [
            "Search",
            json.dumps({
                "origin": ORIGIN,
                "destination": DEST,
                "date": dep.strftime("%Y-%m-%d"),
                "returnDate": ret.strftime("%Y-%m-%d"),
                "adults": 1
            })
        ]
    ]

    try:
        r = requests.post(URL, json=payload, headers=HEADERS)
        if r.status_code != 200:
            print(f"Error {r.status_code}: {r.text[:80]}")
            return None
        print(r.text)  # Debug: print full response text
        text = r.text
        # Google's RPC response isn't plain JSON — it's a JS-callback-style
        # blob with a JSON array embedded in it, so we slice out the
        # outermost [[ ... ]] before parsing. This is brittle: it assumes
        # the payload's structure hasn't changed and doesn't validate it.
        start = text.find("[[")
        end = text.rfind("]]") + 2
        raw = json.loads(text[start:end])

        # Convert whole chunk to text for easy price regex
        block = json.dumps(raw)

        import re
        # No documented schema for this undocumented endpoint, so instead of
        # navigating the parsed structure we just regex out the first
        # "amount" field found anywhere in the blob.
        m = re.search(r'"amount":"(\d+)"', block)
        return int(m.group(1)) if m else None

    except Exception as e:
        print("Exception:", e)
        return None


# -------------------------------
# Main scanning loop
# -------------------------------
def run_scan():
    print(f"Scanning flights {ORIGIN} → {DEST} for December {YEAR}...\n")
    pairs = generate_pairs(YEAR, MONTH, TRIP_MIN, TRIP_MAX)

    results = []
    for dep, ret in pairs:
        price = query_google_flights(dep, ret)
        results.append((dep, ret, price))
        print(f"{dep} → {ret}: {price if price else 'N/A'}")
        # Leftover from debugging the RPC response parsing above — limits
        # the scan to a single date pair. Remove to scan all combinations.
        break
    
    print(results)
    valid = [x for x in results if x[2] is not None]
    if valid:
        best = min(valid, key=lambda x: x[2])
        print("\n=== BEST PRICE FOUND ===")
        print(f"Depart: {best[0]} Return: {best[1]} Price: ${best[2]}")
    else:
        print("No prices found in this range.")

run_scan()
