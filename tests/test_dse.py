import copy
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "bin"))
import dse
import market_bulk
import overview
import research
import stocks


COMPANIES = {"ok": True, "companies": [
    {"symbol": "GP", "name": "Grameenphone Ltd.", "sector": "Telecommunication", "category": "A", "market_cap_mn": 322721.705},
    {"symbol": "SQUARETEXT", "name": "Square Textiles PLC.", "sector": "Textiles", "category": "A", "market_cap_mn": 9744.249},
    {"symbol": "ABBANK", "name": "AB Bank PLC.", "sector": "Bank", "category": "A", "market_cap_mn": 0},
    {"symbol": "MTB", "name": "Mutual Bank PLC.", "sector": "Bank", "category": "A", "market_cap_mn": 15503.405},
    {"symbol": "AMCL(PRAN)", "name": "Agricultural Marketing Company Ltd. (Pran)", "sector": "Food", "category": "A", "market_cap_mn": None},
    {"symbol": "", "name": "Nameless", "sector": "", "category": "", "market_cap_mn": 1},
    "not a row"]}
QUOTE = {"ok": True, "quote": {"date": "2026-09-23", "open": 240.9, "high": 244.3, "low": 240.7,
                                "close": 243.4, "previous_close": 240.9, "change": 2.5,
                                "change_percent": 1.0378, "volume": 77720, "trades": 688, "value_mn": 18.826}}
HISTORY = {"ok": True, "count": 2, "history": [
    {"date": "2026-09-23", "open": 240.9, "high": 244.3, "low": 240.7, "close": 243.4, "volume": 77720},
    {"date": "2026-09-22", "open": 241.9, "high": 242, "low": 240.8, "close": 240.9, "volume": 51024}]}
MARKET = {"ok": True, "indices": {
    "DSEX": {"close": 5596.21859, "change": 38.5874, "change_percent": 0.6943, "date": "2026-09-23"},
    "DSES": {"close": 1119.32239, "change": 8.0978, "change_percent": 0.7237, "date": "2026-09-23"}},
    "breadth": {"date": "2026-09-23", "advanced": 246, "declined": 102, "unchanged": 37}}
UNLISTED = {"ok": False, "error": "No price on record for GHOST"}


def provider(documents):
    """A stand-in for the DSE transport, keyed by endpoint, recording every call.

    It reports `ok: false` the way `read` does, so callers see the same
    `UnknownSymbol` they would see from a real provider.
    """
    calls = []

    def request(parameters, opener=None):
        calls.append(dict(parameters))
        endpoint = parameters.get("endpoint")
        document = documents.get((endpoint, parameters.get("symbol")) if endpoint == "quote" else endpoint)
        if document is None:
            raise AssertionError(f"unexpected DSE request: {parameters}")
        document = copy.deepcopy(document)
        if not document.get("ok", True):
            raise stocks.UnknownSymbol(str(document.get("error") or "DSE reported no result."))
        return document

    request.calls = calls
    return request


def history(count, close=243.4):
    """`count` daily bars ending 2026-09-23, newest first, as the provider sends them."""
    rows = []
    for index in reversed(range(count)):
        day = datetime(2026, 9, 23) - timedelta(days=index)
        rows.append({"date": day.date().isoformat(), "open": close, "high": close + 2,
                     "low": close - 1, "close": close - index, "volume": 1000 + index})
    return {"ok": True, "count": count, "history": rows}


def body(text):
    """A stand-in response object, so `read` can be exercised without a socket."""
    class Response:
        def read(self, _limit):
            return text.encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False
    return Mock(side_effect=lambda *_args, **_kwargs: Response())


class IsolatedState(unittest.TestCase):
    """Keeps every test off the real cache, so no fixture can touch live data."""

    # The budget tests exercise `reserve` itself, so they opt out of stubbing it.
    stub_reserve = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        context = patch.dict(os.environ, {"STOCKS_STATE_DIR": self.temp.name})
        context.start()
        self.addCleanup(context.stop)
        if self.stub_reserve:
            reserve = patch.object(dse, "reserve")
            reserve.start()
            self.addCleanup(reserve.stop)
        # A guard: the fixture's state must be this test's own directory.
        self.assertEqual(dse.state_directory(), Path(self.temp.name))


class Symbols(unittest.TestCase):
    def test_the_suffix_marks_dhaka_and_leaves_every_other_market_alone(self):
        for ticker in ("GP.BD", "AMCL(PRAN).BD"):
            self.assertTrue(dse.is_dse(ticker), ticker)
            self.assertEqual(dse.tagged(dse.bare(ticker)), ticker)
        for ticker in ("AAPL", "RELIANCE.NS", "005930.KS", "BRK-B", "^SPX", "BTC-USD", "ES=F", "MTB"):
            self.assertFalse(dse.is_dse(ticker), ticker)
            self.assertEqual(dse.bare(ticker), ticker)
            self.assertEqual(dse.tagged(ticker), ticker + ".BD")
        # Tagging is idempotent, so a symbol can be normalised twice safely.
        self.assertEqual(dse.tagged(dse.tagged("GP")), "GP.BD")
        self.assertEqual(dse.bare(dse.tagged("GP")), "GP")

    def test_dhaka_tradable_symbols_survive_validation(self):
        self.assertEqual(stocks.symbol("gp.bd"), "GP.BD")
        self.assertEqual(stocks.symbol("AMCL(PRAN).BD"), "AMCL(PRAN).BD")
        for invalid in ("", ".BD", "GP BD", "GP;BD", "A" * 31):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stocks.symbol(invalid)

    def test_bars_are_stamped_at_the_dhaka_close_not_midnight_utc(self):
        # 14:30 in Asia/Dhaka is 08:30 UTC, so a bar never lands on a bare day.
        self.assertEqual(dse.stamp("2026-09-23"),
                         int(datetime(2026, 9, 23, 8, 30, tzinfo=ZoneInfo("UTC")).timestamp()))
        for invalid in (None, "", "2026-13-01", "2026-09", "yesterday", 20260923):
            self.assertIsNone(dse.stamp(invalid), invalid)

    def test_session_follows_the_bangladeshi_week_and_hours(self):
        zone = ZoneInfo(dse.TIMEZONE)
        # 2026-09-23 is a Wednesday, 2026-09-27 a Sunday, 2026-09-25 a Friday.
        for moment, expected in [
            (datetime(2026, 9, 23, 12, 0, tzinfo=zone), "REGULAR"),
            (datetime(2026, 9, 23, 14, 30, tzinfo=zone), "REGULAR"),
            (datetime(2026, 9, 23, 15, 0, tzinfo=zone), "CLOSED"),
            (datetime(2026, 9, 23, 9, 59, tzinfo=zone), "CLOSED"),
            (datetime(2026, 9, 27, 12, 0, tzinfo=zone), "REGULAR"),
            (datetime(2026, 9, 25, 12, 0, tzinfo=zone), "CLOSED"),
            # 12:00 UTC is 18:00 in Dhaka, long after the close.
            (datetime(2026, 9, 23, 12, 0, tzinfo=ZoneInfo("UTC")), "CLOSED")]:
            with self.subTest(moment=moment):
                self.assertEqual(dse.session(moment), expected)


class Charts(IsolatedState):
    def entry(self):
        return {"name": "Grameenphone Ltd.", "marketCap": 322721705000.0}

    def test_a_daily_chart_matches_the_shape_the_plugin_already_reads(self):
        row = dse.build_chart(HISTORY, "GP.BD", "1D", self.entry())
        self.assertEqual(row["symbol"], "GP.BD")
        self.assertEqual(row["name"], "Grameenphone Ltd.")
        self.assertEqual(row["instrumentType"], "DSE")
        self.assertEqual(row["currency"], "BDT")
        self.assertEqual(row["exchange"], dse.EXCHANGE)
        self.assertEqual(row["schema"], 2)
        self.assertEqual(row["range"], "1D")
        self.assertEqual(row["timezone"], dse.TIMEZONE)
        self.assertEqual(row["price"], 243.4)
        self.assertEqual(row["previous"], 240.9)
        self.assertEqual(row["change"], 2.5)
        self.assertEqual(row["open"], 240.9)
        self.assertEqual(row["high"], 244.3)
        self.assertEqual(row["low"], 240.7)
        self.assertEqual(row["volume"], 77720)
        self.assertEqual(row["marketCap"], 322721705000.0)
        self.assertEqual([close for _, close in row["points"]], [240.9, 243.4])
        self.assertEqual(row["dates"], ["2026-09-22", "2026-09-23"])
        self.assertEqual(row["volumes"], [51024, 77720])
        self.assertEqual(row["events"], [])
        # End-of-day bars carry no intraday session to shade on the chart.
        self.assertIsNone(row["sessionStart"])
        self.assertIsNone(row["sessionEnd"])

    def test_only_the_daily_range_quotes_a_session_change(self):
        daily = dse.build_chart(history(30), "GP.BD", "1D")
        self.assertEqual(daily["previous"], daily["points"][-2][1])
        self.assertAlmostEqual(daily["percent"], (daily["price"] - daily["previous"]) / daily["previous"] * 100)
        self.assertEqual(daily["open"], 243.4)
        monthly = dse.build_chart(history(30), "GP.BD", "1M")
        self.assertIsNone(monthly["previous"])
        self.assertIsNone(monthly["change"])
        self.assertIsNone(monthly["percent"])
        self.assertIsNone(monthly["open"])

    def test_points_arrive_oldest_first_however_the_provider_orders_them(self):
        row = dse.build_chart({"ok": True, "history": list(reversed(HISTORY["history"]))}, "GP.BD", "1D")
        stamps = [stamp for stamp, _ in row["points"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(row["dates"], ["2026-09-22", "2026-09-23"])
        self.assertEqual(row["previous"], 240.9)

    def test_a_52_week_range_waits_for_a_year_of_bars_before_it_is_reported(self):
        short = dse.build_chart(history(30), "GP.BD", "1M")
        self.assertIsNone(short["yearHigh"])
        self.assertIsNone(short["yearLow"])
        long = dse.build_chart(history(dse.YEAR_BARS), "GP.BD", "1Y")
        self.assertEqual(long["yearHigh"], 243.4)
        self.assertEqual(long["yearLow"], 243.4 - (dse.YEAR_BARS - 1))

    def test_a_52_week_range_ignores_bars_older_than_the_trailing_year(self):
        # A 2Y chart must report the same annual range as a 1Y chart. Counting the
        # whole window would drag the low down to 243.4 - (2 * YEAR_BARS - 1).
        one = dse.build_chart(history(dse.YEAR_BARS), "GP.BD", "1Y")
        two = dse.build_chart(history(2 * dse.YEAR_BARS), "GP.BD", "2Y")
        self.assertEqual(two["yearHigh"], one["yearHigh"])
        self.assertEqual(two["yearLow"], one["yearLow"])
        self.assertEqual(two["yearLow"], 243.4 - (dse.YEAR_BARS - 1))

    def test_a_single_bar_uses_its_own_open_as_the_previous_close(self):
        row = dse.build_chart({"ok": True, "history": [HISTORY["history"][0]]}, "GP.BD", "1D")
        self.assertEqual(row["previous"], 240.9)
        self.assertEqual(row["change"], 2.5)

    def test_a_missing_catalog_leaves_the_ticker_as_the_name_rather_than_failing(self):
        row = dse.build_chart(HISTORY, "GP.BD", "1D")
        self.assertEqual(row["name"], "GP")
        self.assertIsNone(row["marketCap"])

    def test_unusable_history_is_reported_rather_than_returned_as_an_empty_chart(self):
        # A body that is not a bar list at all is a provider error.
        for document in ({"ok": True, "history": "none"}, {"ok": True, "history": {"a": 1}}, {"ok": True}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                dse.build_chart(document, "GP.BD", "1D")
        # A well-formed body with nothing usable in it means the symbol is unknown.
        for document in ({"ok": True, "history": []},
                         {"ok": True, "history": [{"date": "2026-09-23", "close": None}]},
                         {"ok": True, "history": [{"date": "bad", "close": 1}]},
                         {"ok": True, "history": ["not a bar"]}):
            with self.subTest(document=document), self.assertRaises(stocks.UnknownSymbol):
                dse.build_chart(document, "GP.BD", "1D")

    def test_chart_spends_one_price_request_and_keeps_the_symbol_it_was_asked_for(self):
        request = provider({"companies": COMPANIES, "history": HISTORY})
        with patch.object(dse, "reserve"):
            row = dse.chart("GP.BD", "1D", request=request)
            self.assertEqual([call["endpoint"] for call in request.calls].count("history"), 1)
            self.assertEqual(request.calls[0]["limit"], dse.BARS["1D"])
            self.assertEqual(request.calls[0]["symbol"], "GP")
            self.assertEqual(row["symbol"], "GP.BD")
            self.assertEqual(row["name"], "Grameenphone Ltd.")
            self.assertEqual(row["marketCap"], 322721705000.0)
            # A bare ticker keeps its own spelling, so the chart still matches the
            # watchlist entry and the selection that asked for it.
            self.assertEqual(dse.chart("GP", "1D", request=request)["symbol"], "GP")

    def test_each_range_asks_for_about_as_many_sessions_as_it_spans(self):
        request = provider({"companies": COMPANIES, "history": HISTORY})
        for period, expected in (("1D", 2), ("1W", 5), ("1M", 22), ("1Y", 250), ("ALL", 1000)):
            with self.subTest(period=period), patch.object(dse, "reserve"):
                request.calls.clear()
                dse.chart("GP.BD", period, request=request)
                history_calls = [call for call in request.calls if call["endpoint"] == "history"]
                self.assertEqual(len(history_calls), 1)
                self.assertEqual(history_calls[0]["limit"], expected)
                self.assertEqual(history_calls[0]["symbol"], "GP")


class Quotes(IsolatedState):
    def test_a_quote_row_derives_a_missing_percentage_and_keeps_a_reported_one(self):
        quote = {"close": 243.4, "change": 2.5, "date": "2026-09-23"}
        derived = dse.quote_row("GP.BD", quote)
        self.assertAlmostEqual(derived["percent"], 2.5 / 240.9 * 100)
        self.assertEqual(derived["currency"], "BDT")
        self.assertEqual(derived["updated"], dse.stamp("2026-09-23"))
        self.assertIn(derived["marketState"], ("REGULAR", "CLOSED"))
        self.assertEqual(dse.quote_row("GP.BD", {**quote, "change_percent": 1.0378})["percent"], 1.0378)
        # An index level with no change on record reports neither, not a divide.
        self.assertIsNone(dse.quote_row("DSEX.BD", {"close": 10})["percent"])
        self.assertIsNone(dse.quote_row("DSEX.BD", {"close": 10, "change": 10})["percent"])

    def test_one_market_request_covers_every_index_and_one_call_covers_each_share(self):
        request = provider({"companies": COMPANIES, "market": MARKET, ("quote", "GP"): QUOTE,
                            ("quote", "SQUARETEXT"): QUOTE, ("quote", "ABBANK"): QUOTE})
        rows = dse.quotes(["DSEX.BD", "DSES.BD", "GP.BD", "SQUARETEXT.BD", "ABBANK.BD"], request=request)
        self.assertEqual([call["endpoint"] for call in request.calls].count("market"), 1)
        self.assertEqual(len([call for call in request.calls if call["endpoint"] == "quote"]), 3)
        self.assertEqual(rows["DSEX.BD"]["price"], 5596.21859)
        self.assertEqual(rows["DSEX.BD"]["percent"], 0.6943)
        self.assertEqual(rows["DSES.BD"]["price"], 1119.32239)
        self.assertEqual(rows["GP.BD"]["price"], 243.4)
        self.assertEqual(rows["GP.BD"]["marketCap"], 322721705000.0)
        self.assertIsNone(rows["DSEX.BD"]["marketCap"])
        # A market cap the provider reports as zero is no market cap at all.
        self.assertIsNone(rows["ABBANK.BD"]["marketCap"])
        self.assertEqual(len(rows), 5)

    def test_a_repeated_symbol_is_fetched_once_and_answered_once(self):
        request = provider({"companies": COMPANIES, ("quote", "GP"): QUOTE})
        self.assertEqual(dse.quotes(["GP.BD", "GP.BD"], request=request),
                         {"GP.BD": dse.quote_row("GP.BD", QUOTE["quote"], 322721705000.0)})
        self.assertEqual(len([call for call in request.calls if call["endpoint"] == "quote"]), 1)

    def test_one_unlisted_share_does_not_lose_the_rest_of_the_batch(self):
        request = provider({"companies": COMPANIES, ("quote", "GP"): QUOTE,
                            ("quote", "SQUARETEXT"): QUOTE, ("quote", "GHOST"): UNLISTED})
        rows = dse.quotes(["GP.BD", "GHOST.BD", "SQUARETEXT.BD"], request=request)
        self.assertEqual(rows["GP.BD"]["price"], 243.4)
        self.assertEqual(rows["SQUARETEXT.BD"]["price"], 243.4)
        self.assertIsNone(rows["GHOST.BD"]["price"])
        self.assertIn("No Dhaka price", rows["GHOST.BD"]["error"])

    def test_an_exhausted_budget_reports_the_remaining_shares_at_once(self):
        def request(parameters, opener=None):
            if parameters.get("endpoint") == "companies":
                return copy.deepcopy(COMPANIES)
            if parameters.get("symbol") == "GP":
                return copy.deepcopy(QUOTE)
            raise dse.BudgetExceeded("DSE data allows 120 requests an hour.")

        rows = dse.quotes(["GP.BD", "SQUARETEXT.BD", "ABBANK.BD"], request=request)
        self.assertEqual(rows["GP.BD"]["price"], 243.4)
        for ticker in ("SQUARETEXT.BD", "ABBANK.BD"):
            self.assertIsNone(rows[ticker]["price"])
            self.assertIn("request limit", rows[ticker]["error"])

    def test_no_symbols_costs_nothing(self):
        self.assertEqual(dse.quotes([]), {})


class Catalog(IsolatedState):
    def test_the_company_list_is_fetched_once_and_reused(self):
        request = provider({"companies": COMPANIES})
        first, second = dse.catalog(request=request), dse.catalog(request=request)
        self.assertEqual(len(request.calls), 1)
        self.assertEqual(first, second)
        # A row with no symbol, and a row that is not an object, are both dropped.
        self.assertEqual([row["symbol"] for row in first], ["GP", "SQUARETEXT", "ABBANK", "MTB", "AMCL(PRAN)"])
        self.assertEqual(first[0], {"symbol": "GP", "name": "Grameenphone Ltd.", "sector": "Telecommunication",
                                    "category": "A", "marketCap": 322721705000.0})

    def test_market_caps_in_millions_become_units_and_zero_means_unknown(self):
        rows = {row["symbol"]: row for row in dse.catalog(request=provider({"companies": COMPANIES}))}
        self.assertEqual(rows["GP"]["marketCap"], 322721705000.0)
        self.assertIsNone(rows["ABBANK"]["marketCap"])
        self.assertIsNone(rows["AMCL(PRAN)"]["marketCap"])

    def test_a_cached_only_lookup_never_spends_a_request(self):
        self.assertEqual(dse.listings(["GP"], cached=True), {})
        dse.catalog(request=provider({"companies": COMPANIES}))
        saved = dse.listings(["GP", "MTB", "NOSUCH"], cached=True)
        self.assertEqual(sorted(saved), ["GP", "MTB"])
        self.assertEqual(saved["GP"]["name"], "Grameenphone Ltd.")

    def test_a_failed_list_leaves_quotes_working_without_names_or_caps(self):
        def request(parameters, opener=None):
            raise ValueError("Could not reach DSE data")
        self.assertEqual(dse.listings(["GP"], request=request), {})
        self.assertEqual(dse.search("Grameenphone", request=request), [])

    def test_an_empty_company_list_is_not_cached_as_the_market(self):
        with self.assertRaises(ValueError):
            dse.catalog(request=provider({"companies": {"ok": True, "companies": []}}))
        self.assertFalse((dse.state_directory() / "dse/catalog.json").exists())

    def test_search_matches_a_ticker_against_its_name_and_tags_the_result(self):
        request = provider({"companies": COMPANIES})
        by_name = dse.search("Square Textiles", request=request)
        self.assertEqual([row["symbol"] for row in by_name], ["SQUARETEXT.BD"])
        self.assertEqual(by_name[0]["name"], "Square Textiles PLC.")
        self.assertEqual(by_name[0]["exchange"], dse.EXCHANGE)
        self.assertEqual(by_name[0]["type"], "EQUITY")
        # A ticker the company spells differently in its name is still found.
        self.assertEqual([row["symbol"] for row in dse.search("ABBANK", request=request)], ["ABBANK.BD"])
        self.assertEqual([row["symbol"] for row in dse.search("grameen", request=request)], ["GP.BD"])
        self.assertEqual([row["symbol"] for row in dse.search("MTB", request=request)], ["MTB.BD"])
        self.assertEqual(dse.search("   "), [])
        self.assertEqual(dse.search("no such company", request=request), [])

    def test_search_caps_the_rows_it_hands_the_merger(self):
        many = {"ok": True, "companies": [{"symbol": f"BANK{index:02d}", "name": f"Bank {index:02d} PLC.",
                                           "sector": "Bank", "category": "A", "market_cap_mn": 1}
                                          for index in range(40)]}
        self.assertEqual(len(dse.search("bank", request=provider({"companies": many}))), 8)

    def test_every_listed_company_carries_a_searchable_name_and_symbol(self):
        rows = dse.catalog(request=provider({"companies": COMPANIES}))
        for row in rows:
            self.assertTrue(row["name"], row)
            self.assertTrue(row["symbol"], row)


class Budget(IsolatedState):
    stub_reserve = False

    def setUp(self):
        super().setUp()
        self.path = dse.state_directory() / "dse/traffic.json"

    def spent(self):
        return json.loads(self.path.read_text())["stamps"]

    def test_the_hourly_allowance_is_spent_refused_and_never_oversold(self):
        # The pacing gap is a real delay, so it is stubbed out here and asserted
        # separately rather than spent thirty seconds inside the test.
        with patch.object(dse.time, "time", return_value=1000), patch.object(dse.time, "sleep") as sleep:
            for _ in range(dse.LIMIT):
                dse.reserve()
            self.assertEqual(len(self.spent()), dse.LIMIT)
            for _ in range(3):
                with self.assertRaises(dse.BudgetExceeded) as raised:
                    dse.reserve()
                # A refused call must not spend, or the window never recovers.
                self.assertEqual(len(self.spent()), dse.LIMIT)
            self.assertIn(str(dse.LIMIT), str(raised.exception))
            # Successive requests are spaced so the provider is not hammered.
            self.assertGreaterEqual(sleep.call_count, dse.LIMIT - 1)
            # Once the oldest spend falls out of the window, requests resume.
            sleep.reset_mock()
            with patch.object(dse.time, "time", return_value=1000 + dse.WINDOW + 1):
                dse.reserve()
            self.assertEqual(len(self.spent()), 1)

    def test_the_pacing_gap_is_waited_out_between_two_live_requests(self):
        # A saved `next` in the future means the last request was too recent, so
        # this one waits out the remainder rather than hitting the provider twice.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"stamps": [1000], "next": 1000 + dse.GAP / 2}))
        with patch.object(dse.time, "time", return_value=1000), patch.object(dse.time, "sleep") as sleep:
            dse.reserve()
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [dse.GAP / 2])
        # And the file records the next earliest moment for the call after this.
        self.assertGreaterEqual(json.loads(self.path.read_text())["next"], 1000 + dse.GAP)

    def test_a_stale_or_corrupt_allowance_is_discarded_rather_than_trusted(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for saved in ({"stamps": "none"}, {"stamps": [True, "x", None]}, {},
                      {"stamps": [1000, 2000], "next": "soon"}, {"stamps": [1000], "next": None}):
            with self.subTest(saved=saved):
                self.path.write_text(json.dumps(saved))
                dse.reserve()
                self.assertEqual(len(self.spent()), 1)

    def test_a_failed_request_still_costs_its_share_of_the_allowance(self):
        with self.assertRaises(stocks.UnknownSymbol):
            dse.read({"endpoint": "quote", "symbol": "GHOST"}, opener=body('{"ok": false, "error": "none"}'))
        self.assertEqual(len(self.spent()), 1)

    def test_a_200_body_reporting_no_price_is_an_unknown_symbol(self):
        with self.assertRaises(stocks.UnknownSymbol) as raised:
            dse.read({"endpoint": "quote", "symbol": "GP"}, opener=body('{"ok": false, "error": "No price on record for GP"}'))
        self.assertIn("No price on record", str(raised.exception))

    def test_an_unreachable_or_unreadable_provider_is_a_value_error(self):
        for opener in (body("not json"), body("[]"), body("null"), Mock(side_effect=OSError("reset"))):
            with self.subTest(opener=opener), self.assertRaises(ValueError):
                dse.read({"endpoint": "market"}, opener=opener)

    def test_the_transport_addresses_the_documented_endpoint(self):
        with patch.object(dse, "reserve"), patch.object(dse.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            dse.read({"endpoint": "quote", "symbol": "GP"})
        url = urlopen.call_args.args[0].full_url
        self.assertTrue(url.startswith(dse.BASE + "?"), url)
        self.assertIn("endpoint=quote", url)
        self.assertIn("symbol=GP", url)


class Routing(IsolatedState):
    """Yahoo and the DSE both list some tickers, so neither may shadow the other."""

    def test_a_us_listing_keeps_its_own_company_beside_the_dhaka_one(self):
        # Mutual Bank on the DSE and MTB Corp in New York share a ticker.
        yahoo = Mock(return_value={"quoteResponse": {"result": [
            {"symbol": "MTB", "regularMarketPrice": 221.19, "regularMarketChange": 2.53,
             "regularMarketChangePercent": 1.157, "marketCap": 31943432192, "currency": "USD"},
            {"symbol": "AAPL", "regularMarketPrice": 341.07, "currency": "USD"}]}})
        with patch.object(dse, "read", provider({"companies": COMPANIES, "market": MARKET,
                                                 ("quote", "MTB"): QUOTE})):
            rows = market_bulk.quotes(["MTB", "MTB.BD", "AAPL"], request=yahoo)["rows"]
        # Yahoo answered, so the bare ticker is the American bank and only the
        # suffixed one reaches Dhaka.
        self.assertEqual(yahoo.call_count, 1)
        self.assertEqual(yahoo.call_args.kwargs["symbols"], "MTB,AAPL")
        self.assertEqual(rows["MTB"]["price"], 221.19)
        self.assertEqual(rows["MTB"]["currency"], "USD")
        self.assertEqual(rows["MTB.BD"]["price"], 243.4)
        self.assertEqual(rows["MTB.BD"]["symbol"], "MTB.BD")
        self.assertEqual(rows["AAPL"]["price"], 341.07)
        self.assertNotIn("MTB.BD", [row["symbol"] for row in rows.values() if row["symbol"] == "AAPL"])

    def test_a_bare_ticker_yahoo_cannot_quote_falls_through_to_the_dse(self):
        yahoo = Mock(return_value={"quoteResponse": {"result": [
            {"symbol": "AAPL", "regularMarketPrice": 341.07, "currency": "USD"}]}})
        with patch.object(dse, "read", provider({"companies": COMPANIES, ("quote", "SQUARETEXT"): QUOTE})):
            dse.catalog(request=provider({"companies": COMPANIES}))
            rows = market_bulk.quotes(["SQUARETEXT", "AAPL"], request=yahoo)["rows"]
        self.assertEqual(rows["SQUARETEXT"]["price"], 243.4)
        # The row is keyed by the symbol that was asked for, not the DSE spelling.
        self.assertEqual(rows["SQUARETEXT"]["symbol"], "SQUARETEXT")
        self.assertNotIn("SQUARETEXT.BD", rows)
        self.assertEqual(rows["AAPL"]["price"], 341.07)

    def test_a_watchlist_without_dhaka_listings_never_asks_the_dse(self):
        yahoo = Mock(return_value={"quoteResponse": {"result": [
            {"symbol": "AAA.L", "regularMarketPrice": 1234, "regularMarketChange": -23, "currency": "GBp"},
            {"symbol": "ZERO", "regularMarketPrice": 0}]}})
        with patch.object(dse, "read") as read:
            rows = market_bulk.quotes(["AAA.L", "ZERO", "MISS"], request=yahoo)["rows"]
        read.assert_not_called()
        self.assertEqual(rows["AAA.L"]["price"], 12.34)
        self.assertEqual(rows["AAA.L"]["currency"], "GBP")
        self.assertIsNone(rows["MISS"]["price"])
        self.assertEqual(rows["MISS"]["error"], "Bulk quote unavailable")

    def test_a_bulk_quote_failure_still_leaves_room_for_dhaka_rows(self):
        yahoo = Mock(return_value={"quoteResponse": {"result": [{"symbol": "OTHER", "regularMarketPrice": 1}]}})
        with patch.object(dse, "read", provider({"companies": COMPANIES, ("quote", "GP"): QUOTE})):
            rows = market_bulk.quotes(["NOSUCH", "GP.BD"], request=yahoo)["rows"]
        self.assertEqual(rows["GP.BD"]["price"], 243.4)
        self.assertIsNone(rows["NOSUCH"]["price"])

    def test_an_exhausted_allowance_leaves_yahoo_quotes_intact(self):
        yahoo = Mock(return_value={"quoteResponse": {"result": [
            {"symbol": "AAPL", "regularMarketPrice": 341.07, "currency": "USD"}]}})
        with patch.object(dse, "read", side_effect=dse.BudgetExceeded("spent")):
            rows = market_bulk.quotes(["AAPL", "GP.BD"], request=yahoo)["rows"]
        self.assertEqual(rows["AAPL"]["price"], 341.07)
        self.assertIsNone(rows["GP.BD"]["price"])
        self.assertIn("request limit", rows["GP.BD"]["error"])


class RepositoryRouting(IsolatedState):
    def setUp(self):
        super().setUp()
        self.repository = stocks.Repository(Path(self.temp.name))

    def transport(self):
        return provider({"companies": COMPANIES, "history": HISTORY, "market": MARKET})

    def test_a_suffixed_ticker_never_asks_yahoo(self):
        with patch.object(dse, "read", self.transport()), patch.object(stocks, "fetch") as fetch:
            row = self.repository.chart("GP.BD", "1D", force=True)
        fetch.assert_not_called()
        self.assertEqual(row["instrumentType"], "DSE")
        self.assertEqual(row["price"], 243.4)
        self.assertEqual(row["ttl"], dse.QUOTE_TTL)
        self.assertEqual(row["currency"], "BDT")

    def test_a_bare_dhaka_ticker_falls_through_only_after_yahoo_declines(self):
        with patch.object(dse, "read", self.transport()), \
                patch.object(stocks, "fetch", side_effect=stocks.UnknownSymbol("none")) as fetch:
            row = self.repository.chart("GP", "1D", force=True)
        fetch.assert_called_once()
        self.assertEqual(row["symbol"], "GP")
        self.assertEqual(row["instrumentType"], "DSE")
        self.assertEqual(row["name"], "Grameenphone Ltd.")
        # The long daily horizon is remembered for the next read.
        self.assertEqual(row["ttl"], dse.QUOTE_TTL)

    def test_a_ticker_neither_provider_lists_keeps_the_yahoo_error(self):
        with patch.object(dse, "read", self.transport()), \
                patch.object(stocks, "fetch", side_effect=stocks.UnknownSymbol("No market data found for this symbol.")):
            row = self.repository.chart("NOSUCH", "1D", force=True)
        self.assertTrue(row["stale"])
        self.assertEqual(row["error"], "No market data found for this symbol.")

    def test_a_yahoo_rate_limit_is_never_retried_against_the_dse(self):
        transport = self.transport()
        with patch.object(dse, "read", transport), \
                patch.object(stocks, "fetch", side_effect=ValueError("Yahoo Finance is rate limiting requests.")):
            row = self.repository.chart("GP", "1D", force=True)
        self.assertEqual(transport.calls, [])
        self.assertIn("rate limiting", row["error"])

    def test_a_daily_quote_is_reused_and_then_refreshed_on_its_own_schedule(self):
        transport = self.transport()
        with patch.object(dse, "read", transport), patch.object(stocks, "fetch"):
            self.repository.chart("GP.BD", "1D", force=True)
            transport.calls.clear()
            with patch.object(dse.time, "time", return_value=dse.time.time() + dse.QUOTE_TTL - 10):
                self.assertEqual(self.repository.chart("GP.BD", "1D")["price"], 243.4)
            self.assertEqual(transport.calls, [])
            with patch.object(dse.time, "time", return_value=dse.time.time() + dse.QUOTE_TTL + 10):
                self.repository.chart("GP.BD", "1D")
            self.assertTrue(transport.calls)

    def test_a_dhaka_row_outlives_the_intraday_staleness_horizon(self):
        self.repository.mutate("add", "GP.BD")
        with patch.object(dse, "read", self.transport()), patch.object(stocks, "fetch"):
            self.repository.chart("GP.BD", "1D", force=True)
        # 700 seconds is past the 600-second Yahoo horizon but inside the daily
        # one a Dhaka close is worth.
        with patch.object(stocks.time, "time", return_value=stocks.time.time() + 700):
            row = next(row for row in self.repository.snapshot()["entries"] if row["symbol"] == "GP.BD")
        self.assertFalse(row["stale"])
        self.assertEqual(row["price"], 243.4)

    def test_a_failed_refresh_keeps_saved_prices_and_cools_down(self):
        with patch.object(dse, "read", self.transport()), patch.object(stocks, "fetch"):
            self.repository.chart("GP.BD", "1D", force=True)
        # A rate limit is reported, but the last good price is kept on screen.
        with patch.object(dse, "read", side_effect=dse.BudgetExceeded("DSE data allows 120 requests an hour.")):
            row = self.repository.chart("GP.BD", "1D", force=True)
        self.assertTrue(row["stale"])
        self.assertEqual(row["price"], 243.4)
        self.assertEqual(row["currency"], "BDT")
        self.assertIn("120 requests an hour", row["error"])
        # And the next read is served from cache until the cooldown passes.
        with patch.object(dse, "read", side_effect=AssertionError("should not be called")) as read:
            self.assertEqual(self.repository.chart("GP.BD", "1D")["price"], 243.4)
            self.assertGreater(self.repository.chart("GP.BD", "1D")["retryAfter"], 0)


class WatchlistPerformance(IsolatedState):
    """The watchlist return table needs history, which Yahoo has no Dhaka rows for."""

    def two_years(self, close=1000.0):
        # A base high enough that every close across 500 bars stays positive,
        # since a baseline at or below zero is not a usable return.
        return history(dse.BARS["2Y"], close=close)

    def test_a_dhaka_listing_reports_its_own_returns_in_taka(self):
        request = provider({"companies": COMPANIES, "history": self.two_years()})
        with patch.object(dse, "read", request):
            row = overview.overview("GP.BD")
        # Two years of bars, so a one-year baseline is never the very first one.
        history_calls = [call for call in request.calls if call["endpoint"] == "history"]
        self.assertEqual([call["limit"] for call in history_calls], [dse.BARS["2Y"]])
        self.assertEqual(row["symbol"], "GP.BD")
        self.assertEqual(row["currency"], "BDT")
        self.assertEqual(row["source"], "DSE Intelligence")
        self.assertEqual(row["timezone"], dse.TIMEZONE)
        self.assertNotIn("stale", row)
        for period in ("1W", "1M", "YTD", "1Y"):
            self.assertIsNotNone(row["returns"][period], period)
            self.assertIsNotNone(row["baselines"][period], period)
            self.assertIsNotNone(row["baselinePrices"][period], period)
        self.assertEqual(row["overviewSchema"], 2)

    def test_the_bangladesh_table_agrees_with_the_dhaka_quote_currency(self):
        # The table divides the live quote by the baseline, so a currency
        # mismatch would silently blank every cell.
        with patch.object(dse, "read", provider({"companies": COMPANIES, "history": self.two_years()})):
            report = overview.overview("GP.BD")
            quote = dse.quote_row("GP.BD", QUOTE["quote"])
        self.assertEqual(report["currency"], quote["currency"])

    def test_a_yahoo_listing_still_asks_yahoo_for_its_baselines(self):
        with patch.object(dse, "read", side_effect=AssertionError("the DSE must not be asked")):
            with patch.object(overview, "fetch", return_value={}), \
                    patch.object(overview, "parse_chart", return_value={
                        "price": 120, "timezone": "UTC", "updated": 1, "currency": "USD",
                        "dates": ["2025-09-22", "2025-12-31", "2026-08-21", "2026-09-15", "2026-09-22"],
                        "points": [[1, 60], [2, 80], [3, 100], [4, 110], [5, 120]]}), \
                    patch.object(overview, "datetime") as clock:
                from datetime import datetime as real_datetime, timezone
                clock.now.return_value = real_datetime(2026, 9, 22, tzinfo=timezone.utc)
                row = overview.overview("AAPL")
        self.assertEqual(row["source"], "Yahoo Finance")
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["returns"]["1Y"], 100)

    def test_too_little_dhaka_history_leaves_a_range_unreported(self):
        # A young listing has no one-year baseline, and inventing one would
        # report a return the market never made.
        with patch.object(dse, "read", provider({"companies": COMPANIES, "history": self.two_years()})):
            fresh = overview.overview("GP.BD")
        short = history(30)
        with patch.object(dse, "read", provider({"companies": COMPANIES, "history": short})):
            row = overview.overview("GP.BD")
        self.assertIsNotNone(fresh["returns"]["1W"])
        self.assertIsNone(row["returns"]["1Y"])
        self.assertIsNone(row["returns"]["YTD"])
        self.assertIsNone(row["baselines"]["1Y"])


class BulkCaching(IsolatedState):
    """A watchlist of Dhaka listings must not spend the whole hourly allowance."""

    def setUp(self):
        super().setUp()
        self.saved = {"quotesSchema": 3, "rows": {"AAPL": {"symbol": "AAPL", "price": 341.07}}}

    def test_a_dhaka_watchlist_polls_no_faster_than_the_allowance_permits(self):
        # Sixty Dhaka listings is where the ordinary five-minute poll would spend
        # the entire hourly allowance in a single refresh, so `research` spaces
        # the poll out. The cost of one refresh is bounded here: a catalog call
        # for the names, and one call per listing for the price, and no index call
        # because none was asked for.
        count = 60
        tickers = [f"S{index}.BD" for index in range(count)]
        many = {"ok": True, "companies": [
            {"symbol": f"S{index}", "name": f"Synthetic {index} PLC.", "sector": "Test",
             "category": "A", "market_cap_mn": 1} for index in range(count)]}
        documents = {"companies": many, **{(f"quote", f"S{index}"): QUOTE for index in range(count)}}
        with patch.object(dse, "read", provider(documents)) as read:
            rows = market_bulk.quotes(tickers, request=Mock(return_value={"quoteResponse": {"result": []}}))
        endpoints = [call["endpoint"] for call in read.calls]
        self.assertEqual(endpoints.count("companies"), 1)
        self.assertEqual(endpoints.count("quote"), count)
        self.assertNotIn("market", endpoints)
        self.assertEqual(len(rows["rows"]), count)
        self.assertTrue(all(row["price"] == 243.4 for row in rows["rows"].values()))

        # The allowance covers a 60-listing watchlist, and the poll interval the
        # research layer derives for it fits inside the same budget. The one
        # catalog call is cached for hours, so only the prices are charged here.
        ttl = max(dse.QUOTE_TTL, int(count * dse.WINDOW / (dse.LIMIT * .6)))
        self.assertLessEqual(count * dse.WINDOW / ttl, dse.LIMIT * .6)
        # Even counting the catalog call on every refresh, the hard limit holds.
        self.assertLessEqual((count + 1) * dse.WINDOW / ttl, dse.LIMIT)
        self.assertGreaterEqual(ttl, dse.QUOTE_TTL)

    def test_a_watchlist_with_no_dhaka_listing_keeps_the_ordinary_poll(self):
        with patch.object(research, "load", return_value=copy.deepcopy(self.saved)) as load:
            research.main(["quotes", "AAPL,MSFT"])
            research.main(["quotes", "AAPL,MSFT"])
            self.assertEqual(load.call_count, 1)
            # A forced refresh always goes out, whatever the cache says.
            research.main(["quotes", "AAPL,MSFT", "--force"])
            self.assertEqual(load.call_count, 2)

    def test_a_dhaka_listing_extends_the_poll_beyond_five_minutes(self):
        # `load` is the fetch this layer calls, so counting it shows exactly when
        # the saved answer is reused rather than refreshed.
        with patch.object(research, "load", return_value=copy.deepcopy(self.saved)) as load:
            with patch.object(research.time, "time", return_value=1000):
                research.main(["quotes", "AAPL,GP.BD"])
                self.assertEqual(load.call_count, 1)
                # Well past the five minutes a Yahoo-only watchlist would use,
                # but inside the daily horizon a Dhaka close is worth.
                with patch.object(research.time, "time", return_value=1000 + 700):
                    research.main(["quotes", "AAPL,GP.BD"])
                self.assertEqual(load.call_count, 1)
                with patch.object(research.time, "time", return_value=1000 + dse.QUOTE_TTL + 60):
                    research.main(["quotes", "AAPL,GP.BD"])
                self.assertEqual(load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
