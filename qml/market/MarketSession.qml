import QtQuick
import qs.Commons
import ".."
import "MarketClock.js" as Clock
import "DhakaClock.js" as Dhaka

// US session state shared by MarketStatus and MarketSky. The provider's
// marketState decides the session; the clock only positions and counts down.
// Dhaka rows carry no provider session state, so when `dhaka` is set its own
// fixed UTC+6 clock is the only source of truth.
QtObject {
    id: root
    // Set by a header that tracks the selected row's own exchange.
    property bool dhaka: false
    property bool active: false
    property double now: Date.now()
    readonly property var report: StockStore.marketQuotesRequest.data
    readonly property var quote: StockStore.marketQuotes["^SPX"] || {}
    readonly property bool fresh: !!report.fetched && !report.stale && !report.error && now / 1000 - report.fetched < 1200
    readonly property string raw: fresh ? (quote.marketState || "") : ""
    readonly property string reported: Clock.normalize(raw)
    // Background checks never blank the chip: while a reload is in flight the
    // last reported session stays up. Loading shows only before the first result.
    property string held: ""
    onReportedChanged: if (reported) held = reported
    readonly property string state: root.dhaka ? Dhaka.scheduled(now) : reported || (StockStore.marketQuotesRequest.busy ? held : "")
    readonly property bool opened: state === "REGULAR"
    readonly property string status: opened ? "Market open" : state === "PRE" ? "Pre-market" : state === "POST" ? "After hours"
        : state === "CLOSED" ? "Market closed" : StockStore.marketQuotesRequest.busy ? "Checking market…" : "Status unavailable"
    readonly property string countdown: state ? (root.dhaka ? Dhaka.countdown(state, now) : Clock.countdown(state, now)) : ""
    // 0..1 through the 04:00–20:00 ET day, -1 before it or at weekends.
    // For Dhaka, 0..1 through 09:00–15:30 BST and -1 on Friday and Saturday.
    readonly property real position: root.dhaka ? Dhaka.position(now) : Clock.position(now)
    readonly property string clock: {
        const minutes = Math.floor((root.dhaka ? Dhaka.dhaka(now) : Clock.eastern(now)).minutes)
        return Math.floor(minutes / 60) + ":" + String(minutes % 60).padStart(2, "0")
    }
    // The chip describes whichever market it is actually showing.
    readonly property string label: root.dhaka ? "Dhaka Stock Exchange · Dhaka " + clock + " BST" : "US market · New York " + clock + " ET"
    readonly property string schedule: root.dhaka ? "Regular 10:00–14:30 · Sun–Thu" : "Pre-market 4:00 · Regular 9:30–16:00 · After hours to 20:00"
    readonly property bool checked: !root.dhaka && !!report.fetched
    property string previousState: ""

    // Emitted when a known non-regular session turns regular.
    signal opening()
    onStateChanged: {
        if (!state) return
        if (previousState && previousState !== "REGULAR" && opened) opening()
        previousState = state
    }
    property Timer clockTimer: Timer { interval: 30000; running: root.active; repeat: true; triggeredOnStart: true; onTriggered: root.now = Date.now() }
}
