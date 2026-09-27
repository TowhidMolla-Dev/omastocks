const assert = require("node:assert/strict")
const fs = require("node:fs")
const vm = require("node:vm")
const path = require("node:path")
const ctx = vm.createContext({})
vm.runInContext(fs.readFileSync(path.join(__dirname, "../qml/market/DhakaClock.js"), "utf8"), ctx)
const Clock = ctx

// A Dhaka wall time is UTC+6 year-round, so build one from its UTC instant.
const at = (y, m, d, hh, mm) => Date.UTC(y, m - 1, d, hh, mm) - 6 * 3600000

// 2026-09-27 is a Sunday, the first day of the DSE week.
assert.equal(new Date(at(2026, 9, 27, 12, 0)).getUTCDay(), 0)
assert.equal(Clock.dhaka(at(2026, 9, 27, 12, 0)).minutes, 720)

// Session boundaries: 10:00 and 14:30 BST, no pre- or post-market.
assert.equal(Clock.scheduled(at(2026, 9, 27, 9, 59)), "CLOSED")
assert.equal(Clock.scheduled(at(2026, 9, 27, 10, 0)), "REGULAR")
assert.equal(Clock.scheduled(at(2026, 9, 27, 12, 30)), "REGULAR")
assert.equal(Clock.scheduled(at(2026, 9, 27, 14, 29)), "REGULAR")
assert.equal(Clock.scheduled(at(2026, 9, 27, 14, 30)), "CLOSED")

// Sunday through Thursday trade; Friday and Saturday do not.
for (const [month, day, open] of [[9, 27, true], [9, 28, true], [9, 29, true], [9, 30, true], [9, 31, true], [10, 1, true], [10, 2, false], [10, 3, false]])
    assert.equal(Clock.scheduled(at(2026, month, day, 12, 0)) === "REGULAR", open, `${month}/${day}`)

// Countdown inside the session counts down to the close.
assert.equal(Clock.countdown("REGULAR", at(2026, 9, 27, 12, 0)), "closes in 2h 30m")
// Before the open it counts forward, and names the day when it is further out.
assert.equal(Clock.countdown("CLOSED", at(2026, 9, 27, 8, 0)), "opens in 2h 00m")
assert.equal(Clock.countdown("CLOSED", at(2026, 9, 26, 12, 0)), "opens in 22h 00m")
// Thursday evening opens on Sunday, skipping the weekend.
assert.equal(Clock.countdown("CLOSED", at(2026, 10, 1, 16, 0)), "opens Sun 10:00 BST")
// A state the clock disagrees with yields no countdown rather than a wrong one.
assert.equal(Clock.countdown("CLOSED", at(2026, 9, 27, 12, 0)), "")

// Sky gradient: -1 off the trading day, then 0..1 across 09:00-15:30.
assert.equal(Clock.position(at(2026, 10, 2, 12, 0)), -1)      // Friday
assert.equal(Clock.position(at(2026, 9, 27, 8, 0)), -1)       // before 09:00
assert.equal(Clock.position(at(2026, 9, 27, 9, 0)), 0)
assert.equal(Clock.position(at(2026, 9, 27, 15, 30)), 1)
assert.equal(Clock.position(at(2026, 9, 27, 18, 0)), 1)        // capped after the close
console.log("dhaka clock ok")
