# NUM 2.5.1 — Apps total now matches your Month total

## The gap, explained and shown

The Apps tab never quite added up to the Month total — and a real
investigation showed it never can by measurement alone. The interface
counter (Day/Month) sees every byte on the wire, including packet headers
and TCP acknowledgements; per-app counting via `nettop` only sees the data
inside them. On two controlled 1 GB downloads, both apps were captured in
full (Chrome 1015.9 MB, qBittorrent 1040.3 MB) — the remaining ~6% of
download, and more of upload, was header overhead that belongs to no app.

Rather than hide that difference, or invent per-app numbers by scaling,
the Apps tab now shows it as one honest row: **Network overhead &
untracked** (Month total minus all apps, never negative). The Apps total
now always equals the Month total. If that row ever jumps by hundreds of
MB, something downloaded without being tracked — it doubles as a warning
light.

Also fixed: the Apps tab's comparison total now skips excluded networks,
exactly as the Month tab does.

## Verified: NUM counts only real internet traffic

A concern that NUM might over-count local traffic was tested directly.
Day/Month totals matched macOS's own interface counter at **99.7%**, and a
1.1 GB AirDrop transfer to a phone (which runs over `awdl0`, never touching
the internet) was **correctly not counted at all**.

## Faster startup from source

A leftover test script, `nt.py`, shared its name with a Windows-only Python
module that the standard library probes for at startup — so it silently
ran a 25-second `nettop` test every time NUM started from source. Moved to
`tools/nettop_probe.py`. The built app was never affected.

# NUM 2.5.0 — per-app accuracy, themes, and a real uninstall

## Per-app tracking, finally accurate

The previous rebuild (below) fixed per-app tracking being fabricated by
truncated-name bucketing — but real-world testing afterward found it was
**still under-counting, by about 25%** (qBittorrent: 14.62 GB recorded
against a true 19.39 GiB), from two separate causes in the safety cap that
guards against a reset counter being booked as a multi-gigabyte spike:

1. **The cap zeroed the whole delta instead of clamping it.** Ordinary
   bursty traffic routinely edges past the cap's estimate (it's built from a
   different data source on a different timer than the reading it checks),
   and the old code discarded the entire interval rather than keeping what
   was plausible and dropping only the excess.
2. **The cap's measurement window reset on every sample tick, regardless of
   whether fresh data had actually arrived.** Under heavy connection load,
   `nettop` can take much longer than a normal ~1–2s burst — a real
   908 MB delta spanning several slow ticks was measured against only the
   most recent ~10s of interface traffic and clipped to 304 MB.

Fixed: the cap clamps instead of zeroing, and its window now only advances
when the reader delivers genuinely fresh data. Verified on a real download,
summed across 413 logged ticks: **100.00% captured, 0 ticks with real
download-side loss.**

## Dark / Light / System theme

Settings -> Appearance -> Theme. "System" follows macOS live with no extra
code (the popover's vibrancy material is adaptive by default); Light and
Dark force one appearance.

Building this surfaced three separate rounds of the same bug — a hardcoded
colour that never went through the theme system at all, so it looked fine
in light mode and broke in dark:

- Selection pills, a text input, and five other spots hardcoded
  `background:#fff` — white-on-white once the (correctly theme-aware) text
  colour sat on top of it.
- Toggle switches: the off-track was hardcoded and never dark-aware; the
  knob had accidentally inherited a translucent treatment when a switch
  knob should always be solid white regardless of theme.
- Calendar day cells and month tiles used flat `rgba(255,255,255,…)`
  values, untouched by any dark-mode variable — the actual "white tabs"
  underneath the app-row text in a bug report earlier in development.

All three fixed by routing every one of these through proper light/dark
CSS variables instead of a literal colour.

## Separate colours for light and dark

Download/upload colours are now two independent pairs, not one pair reused
across both themes — a colour picked for a light card can be unreadable on
a dark one. Defaults: bright, saturated colours for dark theme (they need
to pop against a dark background); deeper, muted colours for light theme
(they need to stay legible against a light one) — deliberately opposite
ends, not the same colour at different brightness. A small reset control
restores the shipped defaults for both themes in one action.

## All-time tab

A fourth tab alongside Day / Month / Apps. Deliberately **not** live —
computing a true all-history total runs only when you tap Generate, and the
result is cached so it survives a relaunch. Keeps the ~1s live refresh loop
untouched by a full-history scan.

## A real in-app Uninstall

Settings -> **Uninstall NUM**. Beyond what a drag-to-Trash or a third-party
uninstaller can ever do correctly: unregisters launch-at-login (only
possible from the app's own running code — this is bundle-identity-bound
by macOS's own login-item API, no external script can fake it), clears
WebKit/cache/preferences data, deletes the entire usage-data folder, and
moves the app itself to Trash. Every step logs to
`~/Library/Logs/NUM/last_uninstall.log` — deliberately outside the folder
being deleted, so the log survives to actually be read.

The app also now notices if it's been dragged to Trash while running (no
OS hook exists for this — it just checks its own bundle path still exists,
once a tick) and quits itself, instead of lingering as an orphaned running
process with an icon in a folder that no longer exists.

## Smaller fixes

- **Apps-list scroll position, and destructive-action confirm dialogs
  ("Erase all data", "Uninstall"), were resetting on their own within a
  second of interacting with them.** Root cause: the whole panel rebuilds
  from scratch on every ~1s live tick, and both were tracked only in the
  DOM rather than in persistent state, so the next tick silently wiped
  them. Fixed by moving both into tracked JS state that survives the
  rebuild.
- **A new boolean setting (`show_system_apps`) could silently revert to
  its default after every relaunch** — it was missing from the
  JS-to-native boolean type-conversion list, so it saved as the string
  `"0"`/`"1"` instead of a real boolean, which a strict equality check on
  load never matched. Fixed the write path, and made settings-loading
  self-healing for any already-corrupted values.
- **The menu bar speed readout could get stuck showing MB/s** during a
  large local/loopback burst — the formatter checked KB/s -> MB/s and
  stopped, never checking for GB/s. Now cascades all the way up.
- **`Quit` and the destructive-action confirm buttons had a red-tinted
  background**, making Quit specifically hard to see against the panel.
  Changed to a neutral background matching every other button, with only
  the text/icon in red.
- **A double-clicked `.app` was silently reading the wrong data folder.**
  The v1/v2 data-folder split relied on an environment variable that's
  only ever set when launching from Terminal — a real installed app never
  inherits it, so it silently fell back to the near-empty v1 folder and
  looked like months of history had vanished. The unset default is now v2
  everywhere it's resolved.
- **`build.sh` / `make-dmg.sh` hardcoded the old app name**, breaking the
  moment a v2 build produced `NUM V2.app` instead of `NUM.app`. Both now
  detect the actual built app name.
- Bundle version (`CFBundleVersion` / `CFBundleShortVersionString` in
  `setup.py`) bumped to match — this is the version macOS's own Get Info
  and Launch Services actually report, separate from the in-app version
  string.

---

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
