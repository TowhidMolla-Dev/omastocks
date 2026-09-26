"""Dhaka Stock Exchange prices, because Yahoo Finance lists no DSE company.

Bangladeshi shares carry a `.BD` suffix, matching the exchange suffixes the
plugin already shows for other markets, so `GP.BD` is Grameenphone and a bare
`GP` still reaches the US listing Yahoo knows. Data comes from DSE Intelligence,
which republishes dsebd.org. It answers one symbol per request, allows 120 an
hour per IP and publishes end-of-day bars only, so the company catalog, every
quote and every chart are cached on disk behind a shared hourly budget.
"""

from contextlib import contextmanager
from datetime import datetime, time as clock_time
import fcntl
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stocks import UnknownSymbol, number, read_json, state_directory, write_json


BASE = "https://stockchartbd.com/api/v1.php"
SUFFIX = ".BD"
CURRENCY = "BDT"
EXCHANGE = "Dhaka Stock Exchange"
TIMEZONE = "Asia/Dhaka"
# The Dhaka session runs 10:00 to 14:30, so a daily bar is stamped at its close.
SESSION_OPEN, SESSION_CLOSE = clock_time(10, 0), clock_time(14, 30)
# Sunday to Thursday, the Bangladeshi trading week.
TRADING_WEEK = {6, 0, 1, 2, 3}
INDEXES = {"DSEX": "DSEX Index", "DSES": "DSES Index",
           "DS30": "DS30 Index", "CDSET": "CDSET Index"}
# The provider's published ceiling, and the gap it expects between calls.
LIMIT, WINDOW, GAP = 120, 3600, 0.25
QUOTE_TTL, CHART_TTL, CATALOG_TTL = 900, 3600, 21600
# End-of-day bars only: 1D shows the last two sessions rather than intraday
# ticks, and the longer ranges ask for roughly as many sessions as they span.
BARS = {"1D": 2, "1W": 5, "1M": 22, "3M": 66, "YTD": 130,
        "1Y": 250, "2Y": 500, "5Y": 1000, "ALL": 1000}
# Roughly a trading year. Below this a 52-week range from these bars would be
# narrower than the year it claims to cover, so it stays unreported instead.
YEAR_BARS = 200


class BudgetExceeded(ValueError):
    """The hourly request allowance is spent; saved prices should be shown."""


def zone():
    try:
        return ZoneInfo(TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def is_dse(ticker):
    # Case-insensitive, so a symbol that has not been through `stocks.symbol()`
    # yet is still recognised as a Dhaka listing rather than sent to Yahoo.
    return isinstance(ticker, str) and ticker.upper().endswith(SUFFIX)


def bare(ticker):
    return ticker[:-len(SUFFIX)] if is_dse(ticker) else ticker


def tagged(ticker):
    return ticker if is_dse(ticker) else ticker + SUFFIX


def session(moment=None):
    """Whether the Dhaka session is open, for the watchlist session badge."""
    local = moment if moment is not None else datetime.now(zone())
    local = local.astimezone(zone())
    if local.weekday() in TRADING_WEEK and SESSION_OPEN <= local.time() <= SESSION_CLOSE:
        return "REGULAR"
    return "CLOSED"


def stamp(value):
    """Epoch seconds for a trading date, at that session's close."""
    try:
        year, month, day = (int(part) for part in str(value).split("-"))
        return int(datetime(year, month, day, SESSION_CLOSE.hour, SESSION_CLOSE.minute,
                             tzinfo=zone()).timestamp())
    except (AttributeError, TypeError, ValueError):
        return None


@contextmanager
def locked(name):
    directory = state_directory() / "dse"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / (name + ".lock")).open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory


def reserve(count=1):
    """Spend from the shared hourly allowance, refusing rather than exceeding it.

    Waiting out the window would stall a whole watchlist refresh, so an
    exhausted budget raises and every caller falls back to its saved data.
    """
    with locked("traffic"):
        path = state_directory() / "dse/traffic.json"
        saved = read_json(path, {})
        now = time.time()
        stamps = [value for value in saved.get("stamps", [])
                  if isinstance(value, (int, float)) and not isinstance(value, bool) and now - value < WINDOW]
        if len(stamps) + count > LIMIT:
            wait = int(min(stamps) + WINDOW - now) + 1 if stamps else WINDOW
            raise BudgetExceeded(f"DSE data allows {LIMIT} requests an hour. Saved prices are shown; "
                                 f"try again in {max(1, wait // 60)} min.")
        # `next` is a convenience, not a fact: a hand-edited or half-written file
        # just loses the pacing gap rather than failing the request.
        ready = saved.get("next")
        if isinstance(ready, (int, float)) and not isinstance(ready, bool):
            time.sleep(max(0, ready - now))
        write_json(path, {"stamps": stamps + [now] * count, "next": time.time() + GAP})


def read(parameters, opener=None):
    reserve()
    url = BASE + "?" + urllib.parse.urlencode(parameters)
    request = urllib.request.Request(url, headers={"User-Agent": "Omastocks/0.1",
                                                   "Accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=15) as response:
            document = json.loads(response.read(2 * 1024 * 1024 + 1))
    except urllib.error.HTTPError as error:
        raise ValueError(f"DSE data returned HTTP {error.code}.") from error
    except (OSError, TimeoutError, ValueError) as error:
        raise ValueError(f"Could not reach DSE data: {error}") from error
    # An unknown symbol is reported in a 200 body rather than a 404.
    if not isinstance(document, dict) or not document.get("ok"):
        message = document.get("error") if isinstance(document, dict) else ""
        raise UnknownSymbol(str(message or f"No Dhaka price on record for {parameters.get('symbol', 'this symbol')}."))
    return document


def catalog(force=False, cached=False, request=None):
    """Every listed company with its sector and market cap: one request, then cached.

    `cached` reads only what a previous run saved, for callers that would rather
    miss a company than spend a request discovering one.
    """
    request = request or read
    path = state_directory() / "dse/catalog.json"
    saved = read_json(path, {})
    now = time.time()
    rows = saved.get("rows")
    fresh = isinstance(rows, list) and saved.get("fetched") and now - saved["fetched"] < CATALOG_TTL
    if cached and not fresh:
        raise ValueError("No saved DSE company list.")
    if not force and fresh:
        return rows
    rows = []
    for row in request({"endpoint": "companies"}).get("companies") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("symbol") or "").strip().upper()
        if not ticker:
            continue
        cap = number(row.get("market_cap_mn"))
        rows.append({"symbol": ticker, "name": str(row.get("name") or ticker).strip(),
                     "sector": str(row.get("sector") or ""), "category": str(row.get("category") or ""),
                     "marketCap": cap * 1e6 if cap is not None and cap > 0 else None})
    if not rows:
        raise ValueError("DSE returned an empty company list.")
    write_json(path, {"fetched": now, "rows": rows})
    return rows


def listings(tickers, request=None, cached=False):
    """Catalog rows keyed by bare ticker, or {} when the list is unavailable.

    Market caps and company names are a bonus on top of prices, so a failed or
    rate-limited catalog never blocks the quotes the caller actually needs.
    """
    wanted = {bare(ticker).upper() for ticker in tickers}
    try:
        rows = catalog(request=request, cached=cached)
    except (OSError, TypeError, ValueError):
        return {}
    return {row["symbol"]: row for row in rows if row["symbol"] in wanted}


def quote_row(ticker, quote, cap=None):
    """A market_bulk-shaped row from a provider quote or index entry."""
    if not isinstance(quote, dict):
        raise ValueError("DSE returned an invalid quote.")
    price, change = number(quote.get("close")), number(quote.get("change"))
    percent = number(quote.get("change_percent"))
    if percent is None and price is not None and change is not None and price != change:
        percent = change / (price - change) * 100
    return {"symbol": ticker, "price": price, "change": change, "percent": percent,
            "marketCap": cap, "currency": CURRENCY, "updated": stamp(quote.get("date")),
            "marketState": session()}


def quotes(tickers, request=None):
    """market_bulk-shaped rows for Dhaka tickers, spending as few requests as possible.

    All four indices arrive in one market call; each share then costs a single
    request. A share the provider does not record, or one the hourly budget can
    no longer cover, is reported as an empty row rather than failing the batch.
    """
    request = request or read
    wanted = list(dict.fromkeys(tickers))
    if not wanted:
        return {}
    rows, indexes, shares = {}, [], []
    for ticker in wanted:
        (indexes if bare(ticker).upper() in INDEXES else shares).append(ticker)
    # Market caps are a bonus, so an index-only batch never pays for the list.
    caps = listings(shares, request=request) if shares else {}
    if indexes:
        indices = request({"endpoint": "market"}).get("indices") or {}
        for ticker in indexes:
            entry = indices.get(bare(ticker).upper()) or {}
            rows[ticker] = quote_row(ticker, entry, None)
    for position, ticker in enumerate(shares):
        try:
            document = request({"endpoint": "quote", "symbol": bare(ticker).upper()})
            rows[ticker] = quote_row(ticker, document.get("quote") or {},
                                     caps.get(bare(ticker).upper(), {}).get("marketCap"))
        except BudgetExceeded:
            # Every remaining symbol would spend a file read to fail the same
            # way, so the rest of the batch is reported at once.
            for pending in shares[position:]:
                rows[pending] = {"symbol": pending, "price": None, "percent": None,
                                 "error": "DSE request limit reached; saved prices are shown"}
            break
        except UnknownSymbol:
            rows[ticker] = {"symbol": ticker, "price": None, "percent": None,
                            "error": "No Dhaka price on record"}
        except ValueError as error:
            rows[ticker] = {"symbol": ticker, "price": None, "percent": None, "error": str(error)}
    return rows


def build_chart(document, ticker, period, entry=None):
    """A parse_chart-shaped row built from end-of-day DSE bars."""
    rows = document.get("history") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("DSE returned an invalid price history.")
    bars = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close, when = number(row.get("close")), stamp(row.get("date"))
        if close is None or when is None:
            continue
        bars.append({"timestamp": when, "date": str(row.get("date")), "close": close,
                     "open": number(row.get("open")), "high": number(row.get("high")),
                     "low": number(row.get("low")), "volume": number(row.get("volume"))})
    bars.sort(key=lambda bar: bar["timestamp"])
    if not bars:
        raise UnknownSymbol(f"No Dhaka price history for {bare(ticker)}.")
    last = bars[-1]
    entry = entry or {}
    # Only 1D quotes a session change; the longer ranges leave it to the chart.
    previous = bars[-2]["close"] if len(bars) > 1 else last["open"]
    change = last["close"] - previous if previous is not None else None
    # The gauge is labelled 52-week, so only the trailing year's sessions count:
    # a 2Y or 5Y chart must not report its whole window as the annual range.
    year = [bar["close"] for bar in bars[-YEAR_BARS:]] if len(bars) >= YEAR_BARS else []
    return {
        "symbol": ticker, "name": entry.get("name") or bare(ticker),
        "instrumentType": "DSE", "currency": CURRENCY, "exchange": EXCHANGE,
        "price": last["close"], "previous": previous if period == "1D" else None,
        "change": change if period == "1D" else None,
        "percent": change / previous * 100 if period == "1D" and change is not None and previous else None,
        "updated": last["timestamp"],
        "open": last["open"] if period == "1D" else None,
        "high": last["high"], "low": last["low"],
        "yearHigh": max(year) if year else None, "yearLow": min(year) if year else None,
        "marketCap": entry.get("marketCap"),
        "volume": last["volume"], "sessionStart": None, "sessionEnd": None,
        "points": [[bar["timestamp"], bar["close"]] for bar in bars],
        "dates": [bar["date"] for bar in bars],
        "volumes": [bar["volume"] for bar in bars],
        "events": [], "timezone": TIMEZONE, "schema": 2,
        "range": period, "stale": False, "error": "",
    }


def chart(ticker, period, request=None):
    """A parse_chart-shaped row for a Dhaka ticker, plus its catalog details.

    The row keeps the symbol it was asked for, so a bare ticker that Yahoo does
    not list still matches the watchlist entry and chart it was requested for.
    """
    request = request or read
    document = request({"endpoint": "history", "symbol": bare(ticker).upper(), "limit": BARS.get(period, 2)})
    return build_chart(document, ticker, period, listings([ticker], request=request).get(bare(ticker).upper()))


def search(query, limit=8, request=None):
    """Dhaka companies matching a query, shaped like the Yahoo search rows.

    Tokens match the start of a name or symbol word, so a ticker such as ABBANK
    still finds "AB Bank" and a name fragment such as "pharma" still finds
    "Beacon Pharmaceuticals".
    """
    from search_catalog import normalize
    request = request or read
    words = normalize(query).split()
    if not words:
        return []
    try:
        rows = catalog(request=request)
    except (OSError, TypeError, ValueError):
        return []
    matches = []
    for row in rows:
        words_in_row = normalize(row["name"] + " " + row["symbol"]).split()
        if all(any(part.startswith(word) for part in words_in_row) for word in words):
            matches.append(row)
    return [{"symbol": tagged(row["symbol"]), "name": row["name"],
             "exchange": EXCHANGE, "type": "EQUITY"} for row in matches[:limit]]
