"""Bulk sector snapshots and quotes. Never expand sectors into chart requests."""
import json
from pathlib import Path
from stocks import number, quote_units, symbol
from yahoo_http import authenticated


def presets():
    return json.loads((Path(__file__).resolve().parents[1] / "data/yahoo-sector-presets.json").read_text())["lists"]


def symbols(value):
    result = sorted({symbol(item) for item in value.split(",") if item})
    if not result or len(result) > 70:
        raise ValueError("Choose between 1 and 70 symbols.")
    return result


def raw(row, key):
    value = row.get(key)
    return number(value.get("raw")) if isinstance(value, dict) else None


def sector(slug, request=authenticated):
    group = next((group for group in presets() if group["id"] == slug), None)
    if not group:
        raise ValueError("Unknown market sector.")
    data = request("/v1/finance/sectors/" + slug).get("data") or {}
    if data.get("key") != slug or not isinstance(data.get("topCompanies"), list) or not data["topCompanies"]:
        raise ValueError("Yahoo returned an incomplete sector response.")
    quotes = {}
    for row in data["topCompanies"]:
        ticker = row.get("symbol")
        if ticker in quotes:
            raise ValueError("Yahoo returned duplicate sector symbols.")
        quotes[ticker] = row
    rows = []
    for member in group["members"]:
        quote = quotes.get(member["symbol"], {})
        change, ytd = raw(quote, "regMarketChangePercent"), raw(quote, "ytdReturn")
        rows.append({**member, "name": member.get("name") or quote.get("name") or member["symbol"],
                     "price": raw(quote, "lastPrice"), "percent": change * 100 if change is not None else None,
                     "ytd": ytd * 100 if ytd is not None else None, "marketCap": raw(quote, "marketCap")})
    return {"sector": slug, "name": group["name"], "rows": rows, "source": group["source"],
            "coverage": sum(row["percent"] is not None for row in rows)}


def parse_quotes(document, tickers):
    response = document.get("quoteResponse") or {}
    if response.get("error") or not isinstance(response.get("result"), list):
        raise ValueError("Yahoo returned an invalid bulk quote response.")
    rows = {}
    for quote in response["result"]:
        ticker = quote.get("symbol")
        if ticker not in tickers:
            continue
        if ticker in rows:
            raise ValueError("Yahoo returned duplicate quotes.")
        currency, scale = quote_units(quote.get("currency", ""))
        price = number(quote.get("regularMarketPrice"))
        change = number(quote.get("regularMarketChange"))
        rows[ticker] = {"symbol": ticker, "price": price * scale if price is not None else None,
                        "change": change * scale if change is not None else None, "marketCap": number(quote.get("marketCap")),
                        "percent": number(quote.get("regularMarketChangePercent")),
                        "currency": currency, "updated": number(quote.get("regularMarketTime")),
                        "marketState": quote.get("marketState") if quote.get("marketState") in ("REGULAR", "PRE", "PREPRE", "POST", "POSTPOST", "CLOSED") else ""}
    if not rows:
        raise ValueError("Yahoo returned no quotes for the requested symbols.")
    for ticker in tickers:
        rows.setdefault(ticker, {"symbol": ticker, "price": None, "percent": None, "error": "Bulk quote unavailable"})
    return {"rows": rows, "source": "Yahoo Finance", "quotesSchema": 3}


def quotes(tickers, request=authenticated):
    """One Yahoo request, plus the Dhaka rows Yahoo cannot serve.

    A `.BD` symbol always belongs to the DSE. A bare one goes to Yahoo first, so
    a US listing such as `MTB` keeps its own company, and only falls through to
    the DSE when Yahoo returns no quote for it at all. That guess reads only the
    company list a previous run saved, so a watchlist of ordinary symbols never
    spends a request looking for Bangladeshi ones.
    """
    import dse
    rows, remote, local = {}, [], []
    for ticker in tickers:
        (local if dse.is_dse(ticker) else remote).append(ticker)
    if remote:
        try:
            rows.update(parse_quotes(request("/v7/finance/quote", symbols=",".join(remote)), remote)["rows"])
        except ValueError:
            if not local:
                # Nothing came back, but a saved company list may still name
                # every symbol as a Dhaka one.
                local, remote = remote, []
    # Keyed by the tagged symbol, so a bare ticker and its `.BD` spelling resolve
    # to one request between them. Yahoo "declines" a symbol by returning no row
    # or a row with no price, and only then is a Dhaka listing worth looking up.
    origin = {dse.tagged(ticker): ticker for ticker in local}
    declined = [ticker for ticker in remote if rows.get(ticker, {}).get("price") is None]
    for bare_symbol in dse.listings(declined, cached=True):
        origin.setdefault(dse.tagged(bare_symbol), bare_symbol)
    for tagged, row in dse.quotes(sorted(origin)).items():
        rows[origin[tagged]] = {**row, "symbol": origin[tagged]}
    for ticker in tickers:
        rows.setdefault(ticker, {"symbol": ticker, "price": None, "percent": None, "error": "Bulk quote unavailable"})
    return {"rows": rows, "source": "Yahoo Finance / DSE Intelligence", "quotesSchema": 3}
