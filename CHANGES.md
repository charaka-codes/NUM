# NUM — split-by-month rebuild

Six files. `store.py` is **new**; the other five are modified. Drop them all
into the `netmonitor/` package.

## Install

```bash
cd .../NetMonitor
cp ~/Downloads/num-v3/*.py netmonitor/
NUM_PROFILE=v2 python3 run.py
```

**Back up `~/.netmonitor-v2/` first.** Migration keeps your original as
`usage.db.premigration`, but keep your own copy anyway.

## What happens on first launch

Migration runs once, automatically, before anything reads or writes:

- every per-second row is folded into its day's running total, per month file
- `months/2026-08.db`, `months/2026-09.db` are created
- the tail of raw rows is seeded into `live.db` for the speed readout
- finished months are sealed
- the old file is renamed `usage.db.premigration` (not deleted)

Measured on the real 1,168,368-row database: **1.5 seconds**, lossless.

## The change in one line

A byte is counted **once, when it arrives**. The sampler adds each delta onto
today's running total, so nothing ever re-adds a million rows to answer a
question.

| | Before | After |
|---|---|---|
| Rows scanned per refresh | 1,168,368 | 31 |
| Refresh queries | 1,514 ms | 0.34 ms |
| Slowest screen | ~1.5 s (every second) | **29.7 ms** |
| Load August in Apps | impossible | **2.5 ms** |
| Working set on disk | 144.9 MB | **1.3 MB** |

## Bugs fixed

**1. Lag.** `_daily()`/`_networks()` grouped on `substr(ts,1,10)`, which can't
use an index, so both full-scanned the table — on the **main thread**, once a
second. At 1.5 s per pass against a 1 s timer the queue could never drain.
Reads now come from pre-aggregated month files.

**2. Apps tab couldn't show past months.** Native only ever computed
`datetime.now()`'s month and there was no way to ask for another, so arrowing
back to August always hit "No data for August 2026". There is now an
`appmonth:<YYYY-MM>` bridge call; sealed months are fetched **once** and cached
in the panel forever — no timer, no refetch, no live updating.

**3. Year month tiles did nothing.** They were built with no `data-m` and no
click handler. Tap a month to drill in (big card + network table follow the
selection), tap again to return to the year.

**4. Per-app numbers were fabricated.** `_read_nettop` bucketed processes by
their nettop-truncated name — every Chrome helper collapses to
`"Google Chrome H"`, every WebKit process to `"com.apple.WebKi"` — and kept only
the **largest** cumulative value in each bucket. Each process has its own
odometer, so when the leader exited the bucket dropped to the runner-up and the
reset guard booked that runner-up's entire multi-GB total as one 30-second
delta.

Real impact: September read 176.67 GB down against a true 102.09 GB (**1.73×**),
and 38.20 GB up against 5.77 GB (**6.62×**). 52 app-days exceeded that day's
whole-machine total.

Baselines are now keyed by full `Name.PID`, each process diffed against its own
previous reading, and the real deltas summed into the display name. Simulated on
the same scenario: **1.80× → 1.00×**.

**5. Network exclusion now applies to the Apps tab too** — it was only ever
applied to interface totals.

## ⚠️ Clear the old per-app data

Your existing `app_usage` rows carry bug 4's inflation and cannot be corrected
(the error depends on how often each bucket churned, so it isn't a fixed
multiplier). After confirming the upgrade works, use **Settings → Export & data
→ Erase all data**, or delete just the app rows:

```bash
for f in ~/.netmonitor-v2/months/*.db; do sqlite3 "$f" "DELETE FROM app_usage;"; done
```

Interface totals are unaffected and stay accurate.

## Layout

```
~/.netmonitor-v2/
├── live.db                 raw per-second rows — live speed only, pruned past 2 days
├── months/2026-08.db       sealed: finished, never written again
├── months/2026-09.db       current month, accumulating
├── settings.json           unchanged
└── usage.db.premigration   your original, kept as backup
```

Networks are stored by fingerprint, never display name, so renaming still
relabels all history with no migration.

## What to test

1. Totals match your screenshots — **345.93 GB** for 2026, Home **328.72 / 17.21**
2. Panel opens instantly; tabs and Settings respond immediately
3. Year → tap **Aug** → shows 238.07 GB (↓226.63 ↑11.44); tap again → back to 2026
4. Apps → arrow back to **August** → list loads; return to it → instant
5. Apps → tap an app row → expands to its per-network split
6. Day view week navigation
7. CSV export (month / year / all time)
8. Leave it running a few hours — confirm `live.db` stays small and the current
   month's totals keep climbing

## Note on housekeeping

Sealing and pruning run from the sampler every ~1800 ticks (~30 min at 1 s).
Month rollover is handled by `add_daily` writing into whichever month today
belongs to, so a new month file appears by itself at midnight on the 1st.
