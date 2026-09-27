# Omastocks

A tileable stock research app for [Omarchy](https://omarchy.org), with an optional
favorites ticker in the top bar. Built with Quickshell; follows your desktop's
theme, fonts and scaling. No API keys or Python dependencies.

![Omastocks opening on AMD: daily chart, watchlist and stock research](preview.png)

> [!IMPORTANT]
> **This is a fork.** It started as
> [dmitry-solomadin/omastocks](https://github.com/dmitry-solomadin/omastocks) and
> is maintained here as `TowhidMolla-Dev/omastocks`, under its own plugin id.
> All credit for the original app goes to its author. See
> [What this fork changes](#what-this-fork-changes).

## Install

Requires **Omarchy's Quickshell-based shell**, Python **3.10+**, and `tzdata`.
The launcher helpers use Bash, coreutils, diffutils (`cmp`) and util-linux
(`flock`), supplied by Omarchy. Git is required by the plugin manager.

Install and enable the plugin:

```sh
omarchy plugin add https://github.com/TowhidMolla-Dev/omastocks --enable
```

The launcher entry and icon are installed automatically. Open the Omarchy launcher
with **Super+Space**, search for **Stocks**, and launch the app.

Installation is user-local. The window is titled **Stocks**; closing it leaves
the optional favorites ticker running.

## What this fork changes

### Dhaka Stock Exchange

Yahoo Finance carries no DSE listings at all, so before this fork a Dhaka stock
could not be searched, quoted or charted. There is now a second provider behind
the same shapes the UI already consumes.

- **Quote, chart, search and add** any DSE equity with a `.BD` suffix, e.g.
  `GP.BD`, `SQUARETEXT.BD`, `AMCL(PRAN).BD`.
- **`.BD` settles venue collisions.** `GP.BD` is Grameenphone in Dhaka; bare `GP`
  is Grupo Pearson in Madrid. A bare ticker still tries Yahoo first and only
  falls through to Dhaka when Yahoo has no price *and* the cached catalog
  recognises it, so an unknown ticker stays an honest error rather than a guess.
- **`DSEX.BD`, `DSES.BD`, `DS30.BD` and `CDSET.BD`** appear in the Market view
  under a **Bangladesh** group.
- **Local moving averages.** Yahoo's averages endpoint does not cover Dhaka, so
  20/50/200-day averages are computed from the daily bars instead.
- **Watchlist returns** for Dhaka rows read two years of daily bars, which is
  what the 1W/1M/YTD/1Y columns need.
- **Research panels are hidden** for a `.BD` row: earnings, statements, analyst
  targets and news all come from providers with no Dhaka coverage, so a blank
  panel beats an error.
- **Rate-limit aware.** The upstream allowance is 120 requests an hour per IP.
  Spend is tracked in a file that survives restarts, with a small gap between
  calls, and a watchlist that would not fit the budget reports which rows were
  skipped instead of silently returning nothing.

### International listings

Stocks from all over the world are reachable, not just US ones. Any venue Yahoo
covers works by suffix, and the ones below were verified against the live
service:

| Venue | Suffix | Example |
| --- | --- | --- |
| Poland, Warsaw | `.WA` | `PKN.WA` — Orlen, PLN |
| Saudi Arabia | `.SR` | `2222.SR` — Saudi Arabian Oil Company, SAR |
| India, NSE and BSE | `.NS` / `.BO` | `RELIANCE.NS` — Reliance Industries, INR |
| United Kingdom, London | `.L` | `ISDE.L` — pence |
| Germany, Frankfurt | `.DE` | — euro |
| France, Paris | `.PA` | — euro |
| Canada, Toronto | `.TO` | — CAD |
| South Africa | `.JO` | — rand |

Search resolves the exchange too, so `orlen` offers `PKN.WA` (WSE), `sabic`
offers `2020.SR` (Saudi Stock Exchange) and `tata` offers `TCS.NS` (NSE).
Quotes denominated in pence, rand or shekels are converted correctly.
`ISDE.L`, `ISDW.L`, `MNZL` and `EURUSD=X` in the default watchlist already rely
on that. Dhaka fills a gap that was genuinely missing rather than incrementally
widening coverage that already worked.

### Fixes

- The 52-week range no longer stays blank on venues whose quote payload omits it;
  it falls back to the loaded chart.
- That range now counts only the trailing year, so a 2Y or 5Y chart no longer
  reports a multi-year span under a 52-week label.
- A watchlist row whose Yahoo price is missing is no longer treated as an error
  when Dhaka has the listing.
- `write_json` creates missing parent directories instead of failing on a first
  write to a fresh state directory.
- Symbols containing parentheses validate, which is what Dhaka's `AMCL(PRAN)`
  style tickers need.
- The header session chip follows the selected row's own exchange. A `.BD` row
  reports Dhaka's Sun–Thu 10:00–14:30 BST session on a dedicated clock, instead
  of New York hours and a next-open day that is wrong for Bangladesh. The chip's
  tooltip names whichever market it is showing, and the sky gradient follows the
  Dhaka trading day rather than the US one.
- Watchlist state is written through a single path that re-derives the legacy
  top-level `entries` mirror from `watchlists[].entries` on every save, so the
  mirror cannot be left pointing at a different list than the one being edited.

## Explore

| View | What's inside |
|---|---|
| **Stock** | Six chart ranges, extended hours, volume, moving averages, earnings, financial statements, analyst targets, insider activity, news and social feeds. **Compare** combines charts and fundamentals for up to five symbols. |
| **Market** | Index benchmarks, futures, commodities, crypto, rates and currencies; Fear & Greed and VIX sentiment; sector/index heatmaps; US economic releases and market news. |
| **Watchlist** | Performance from 1D through 1Y, a heatmap, and an earnings calendar with EPS/revenue estimates and the latest surprises. |

Market maps cover the **S&P 500, Nasdaq 100, Dow Jones**, and **11 sectors**.
Choose **1D / YTD** and **Top 50 / Top 100 / All** for larger indexes. Click a
benchmark, instrument or tile to open its Stock view.

### Watchlists and controls

- Search by ticker or company name; click **+** to add a result.
- Use the list dropdown and **pen** to manage up to **12 lists, 60 stocks each**.
- **Sort Watchlist** offers Custom, Price Change, Percentage Change, Market Cap,
  Symbol and Name. Drag rows in **Custom** mode; the Overview follows the same order.
- **Star** a stock to put it in the top bar. Favorites are shared across lists;
  an overflowing ticker scrolls and pauses on hover.
- **Settings** controls the topbar widget, its fields and width, the sidebar's
  displayed metric, chart volume and event markers.
- Hover a chart for prices; drag to measure an interval. Moving averages are
  available on **1M and longer** ranges. **Extended** adds supported pre-/post-market
  sessions to 1D. Expand a research section to load its details.
- **Last report**, earnings-call links, headlines and source buttons open in your
  browser. The Last report link resolves Google's first result for the release.

| Shortcut | Action |
|---|---|
| `Ctrl+S` | Focus search |
| `↑` / `↓` | Select the previous/next stock |
| `Ctrl+R` | Refresh the current view |
| `Escape` | Clear a chart selection, exit Compare, or clear search |
| `Ctrl+W` | Close Stocks |

## Screenshots

Click a thumbnail to view the full-size screenshot.

<table>
  <tr>
    <td align="center"><a href="docs/screenshots/stock.png"><img src="docs/screenshots/thumbs/stock.png" width="420" alt="AMD monthly chart with moving average and volume"></a><br><b>Stock chart</b></td>
    <td align="center"><a href="docs/screenshots/market.png"><img src="docs/screenshots/thumbs/market.png" width="420" alt="Market benchmarks, cross-asset prices and sentiment"></a><br><b>Market overview</b></td>
  </tr>
  <tr>
    <td align="center"><a href="docs/screenshots/heatmap.png"><img src="docs/screenshots/thumbs/heatmap.png" width="420" alt="S&amp;P 500 market-cap-weighted heatmap"></a><br><b>Market map</b></td>
    <td align="center"><a href="docs/screenshots/watchlist.png"><img src="docs/screenshots/thumbs/watchlist.png" width="420" alt="Watchlist performance and earnings calendar"></a><br><b>Watchlist &amp; Calendar</b></td>
  </tr>
  <tr>
    <td align="center"><a href="docs/screenshots/research.png"><img src="docs/screenshots/thumbs/research.png" width="420" alt="Quarterly financial statements and analyst research"></a><br><b>Company research</b></td>
    <td align="center"><a href="docs/screenshots/compare.png"><img src="docs/screenshots/thumbs/compare.png" width="420" alt="AMD, NVIDIA and Broadcom charts and fundamentals compared"></a><br><b>Compare</b></td>
  </tr>
</table>

## Data and storage

Public feeds from **Yahoo Finance, Nasdaq, TradingView, CNN, Google News,
Stocktwits, ApeWisdom and DSE Intelligence** supply the data. Prices may be
delayed, earnings dates may be estimates, and coverage varies. Missing values
stay **—**; failed refreshes retain saved data. Manual refresh respects provider
rate limits.

**Bangladesh.** Dhaka Stock Exchange listings are searched and charted with a
`.BD` suffix, such as `GP.BD` and `SQUARETEXT.BD`, because Yahoo carries no DSE
listings and some tickers exist on both venues. These are end-of-day bars on an
hourly allowance, so they carry no intraday session and no company research. The
52-week range appears once a yearly range has loaded, and moving averages are
computed locally. `DSEX.BD`, `DSES.BD`, `DS30.BD` and `CDSET.BD` are available
in the Market view. See [What this fork changes](#what-this-fork-changes).

Watchlist returns exclude dividends. Market-map areas use company market cap,
not official index weights; memberships are [bundled snapshots](data/README.md).
Financials retain reporting dates and currencies. Reddit mentions measure
attention, not sentiment. Company-only panels are hidden for non-company instruments.

Watchlists and caches live in
`${XDG_STATE_HOME:-~/.local/state}/omarchy/io.github.TowhidMolla-Dev.omastocks/`.
Display preferences use Omarchy's plugin settings. Queries go to the relevant
provider; there is no telemetry or account setup. See
[network and cache details](docs/DEVELOPMENT.md#data-flow) for contributors.

## Update or remove

```sh
omarchy plugin update io.github.TowhidMolla-Dev.omastocks
omarchy plugin remove io.github.TowhidMolla-Dev.omastocks
```

Removal asks for confirmation. The plugin cleans up its launcher entry and icon
when unloaded; your watchlists and caches are preserved.

## Development

```text
qml/        UI, shared stores and JavaScript, organized by feature
bin/        Python data helpers and provider integrations
assets/     Launcher entry and icon
data/       Saved market memberships and their provenance
tests/      Python and JavaScript regression tests
docs/       Contributor guide and screenshot gallery
```

See the [contributor guide](docs/DEVELOPMENT.md) for setting up a development
checkout, architecture, checks and reload commands.
The experimental **Brief me** AI feature lives on [`feature/brief-me`](https://github.com/TowhidMolla-Dev/omastocks/tree/feature/brief-me).

## Roadmap

Open an issue if you want to pick something up.

### Known gaps

- [ ] No DSE intraday. The provider publishes end-of-day bars only, so `1D` is
      the last two sessions rather than a live session.
- [ ] No DSE company research. Earnings, statements, news and analyst targets
      need a Dhaka-aware source before those panels can return.
- [ ] A watchlist heavy on `.BD` rows spends its hourly allowance on `2Y` charts
      for the performance table. A shared cache keyed by symbol would let one
      fetch serve both the chart and the table.

### Ideas

- [ ] More South Asian exchanges, where Yahoo coverage is similarly thin.
- [ ] BDT conversion toggle for comparing a Dhaka holding against a USD one.
- [ ] DSE exchange holidays and session calendar, so the Dhaka chip can hold the
      session closed on a public holiday the way the US one defers to a
      provider's `marketState`; Dhaka rows carry no session state to defer to.
- [ ] Screenshot of a `.BD` row in the gallery, which currently shows US names only.
- [ ] Publish to the Omarchy plugin marketplace under this fork's own id.

## License

[MIT](LICENSE). Market data and publisher images belong to their respective
providers.
