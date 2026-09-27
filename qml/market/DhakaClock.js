// Dhaka Stock Exchange session clock. Bangladesh abolished daylight saving in
// 2009, so UTC+6 applies all year and no DST arithmetic is needed. DSE trades
// Sunday through Thursday 10:00-14:30 and has no pre- or post-market session.
// Unlike the US clock there is no provider marketState to defer to: Dhaka rows
// carry no session state, so the clock is the only source of truth here.
var offset = 6 * 3600000
var sessionOpen = 600, sessionClose = 870   // 10:00 and 14:30
var dayOpen = 540, dayClose = 930           // 09:00 and 15:30, for the sky gradient

// Dhaka wall clock, using JS getUTCDay (Sun=0) since the offset is fixed.
function dhaka(utcMs) {
    const local = new Date(utcMs + offset)
    return {day: local.getUTCDay(), minutes: local.getUTCHours() * 60 + local.getUTCMinutes() + local.getUTCSeconds() / 60}
}

// DSE week runs Sunday to Thursday.
function tradingDay(day) { return day >= 0 && day <= 4 }

function midnight(utcMs, ahead) {
    const local = new Date(utcMs + offset)
    return Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate() + ahead) - offset
}

function dayOf(midnightMs) { return new Date(midnightMs + offset).getUTCDay() }

// REGULAR or CLOSED, from the clock alone.
function scheduled(utcMs) {
    const now = dhaka(utcMs)
    if (!tradingDay(now.day) || now.minutes < sessionOpen || now.minutes >= sessionClose) return "CLOSED"
    return "REGULAR"
}

function nextEvent(utcMs, boundaries) {
    for (let ahead = 0; ahead < 8; ahead++) {
        const base = midnight(utcMs, ahead)
        if (!tradingDay(dayOf(base))) continue
        for (const minute of boundaries) {
            const time = base + minute * 60000
            if (time > utcMs) return {time: time, minutes: (time - utcMs) / 60000, day: dayOf(base)}
        }
    }
}

function nextOpen(utcMs) { return nextEvent(utcMs, [sessionOpen]) }

// 0..1 through the 09:00-15:30 trading day, -1 before it or on Friday/Saturday.
function position(utcMs) {
    const now = dhaka(utcMs)
    if (!tradingDay(now.day) || now.minutes < dayOpen) return -1
    return Math.min(1, (now.minutes - dayOpen) / (dayClose - dayOpen))
}

function duration(minutes) {
    const total = Math.max(1, Math.ceil(minutes))
    const days = Math.floor(total / 1440), hours = Math.floor(total % 1440 / 60), rest = total % 60
    if (days) return days + "d " + hours + "h"
    return hours ? hours + "h " + (rest < 10 ? "0" : "") + rest + "m" : rest + "m"
}

var dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

function countdown(state, utcMs) {
    if (state !== scheduled(utcMs)) return ""
    const now = dhaka(utcMs)
    if (state === "REGULAR") return "closes in " + duration(sessionClose - now.minutes)
    const next = nextOpen(utcMs)
    return next.minutes < 1440 ? "opens in " + duration(next.minutes) : "opens " + dayNames[next.day] + " 10:00 BST"
}
