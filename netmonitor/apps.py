"""
apps.py — per-app (per-process) network usage via `nettop`.

Companion to core.py. Adds the v2 "which app used my data" feature without
touching the existing per-interface tracking. Follows the SAME patterns as
core.Sampler: reset-guarded deltas, its own write connection on the sampling
thread, WAL mode, and refresh_view before UI reads.

Design decisions (see the NUM design notes):
  * SLOW beat. Per-app sampling is heavier than reading one interface counter,
    so it runs on its own timer (default 30s), NOT the ~2s live-speed loop.
  * Short bursts. We spawn `nettop` for two snapshots and let it exit, rather
    than holding a live process open all day (lighter on battery).
  * Daily aggregation. Instead of one timestamped row per app per tick (which
    would bloat the DB), we UPSERT into a per-day / per-network / per-app row
    and add the delta on. Small table, fast queries, matches the UI which is
    month/day/year totals — not a live feed.
  * Tagged by network. Every row carries the current network label, so the UI
    can expand an app to show which networks it used (the merged A+B design).

nettop reports bytes_in / bytes_out CUMULATIVE since each process started, so
we diff successive readings exactly like core.Sampler diffs the interface
counter, guarding against resets (process restarts → counter drops → treat the
new value as the delta).
"""

import os
import subprocess
import threading
import time
from datetime import datetime

# We reuse core's connection helpers, network detection, and formatting so the
# two modules can never drift apart. Package-relative import (matches app.py,
# popover.py, etc.) with a fallback so the module still works if run standalone.
try:
    from .core import connect, active_interface, network_label, refresh_view, human
except ImportError:
    from core import connect, active_interface, network_label, refresh_view, human


# ---------- friendly names for cryptic system daemons ----------
#
# The biggest data users are often background daemons with opaque names. This
# lookup turns "replicatord" into "iCloud" so a non-technical user understands
# what actually ate their data. Unknown processes just show their raw name.
# This table is the honest value-add of the feature — extend it freely.

# Ceiling for a plausible per-process transfer rate (bytes/sec). 1.25 GB/s is
# 10 Gbit — far above any real link here, so this only ever catches junk.
_MAX_BPS = 1_250_000_000

FRIENDLY = {
    "replicatord":     "iCloud",
    "cloudd":          "iCloud",
    "bird":            "iCloud Drive",
    "fileproviderd":   "iCloud Drive",
    "sharingd":        "AirDrop / Handoff",
    "mDNSResponder":   "Network (Bonjour)",
    "apsd":            "Apple Push",
    "nsurlsessiond":   "Background Downloads",
    "softwareupdated": "Software Update",
    "AssetCacheLocat": "Content Caching",
    "com.apple.WebKi": "WebKit",
    "Google Chrome H": "Google Chrome",
    "Google Chrome":   "Google Chrome",
    "Claude Helper":   "Claude",
    "Creative Cloud":  "Adobe Creative Cloud",
    "Core Sync":       "Adobe Sync",
    "Adobe Desktop S": "Adobe",
    "Adobe Illustrat": "Adobe Illustrator",
    "rapportd":        "Continuity",
    "netbiosd":        "File Sharing",
    "identityservice": "Apple ID",
    "Mail":            "Mail",
}


# ---------- system / local-network services ----------
#
# These processes carry LOCAL traffic — device discovery, AirDrop, Continuity,
# file sharing, Wi-Fi management. They are not what "which app used my internet
# data" is asking about, and they were the source of the most absurd readings
# (mDNSResponder claiming 10.98 GB of upload).
#
# They are still RECORDED and still SHOWN, in their own group, but they never
# count toward the Apps total. Nothing is hidden — a daemon behaving oddly stays
# visible, which is exactly how the original bug was spotted.

SYSTEM_LOCAL = {
    "mDNSResponder",    # Bonjour / multicast discovery
    "sharingd",         # AirDrop, Handoff
    "rapportd",         # Continuity between Apple devices
    "netbiosd",         # Windows file sharing
    "airportd",         # Wi-Fi management
    "locationd",        # Location services
    "AirPlayXPCHelper", # AirPlay to local devices
    "AirPlayUIAgent",
    "remoted",
    "wifianalyticsd",
    "identityservice",
    "helpd",
    "nehelper",
    "configd",
    "UserEventAgent",
}


def is_system_local(proc):
    """True if this process is local-network plumbing rather than an app."""
    if proc in SYSTEM_LOCAL:
        return True
    base = proc.rsplit(".", 1)[0] if "." in proc else proc
    return base in SYSTEM_LOCAL


# ---------- anomaly flags ----------
#
# Arithmetic only — never a judgement about what is "normal for this app". Each
# flag is an internal inconsistency that cannot be explained by ordinary use, so
# it can't cry wolf. Returns (level, reason) with level None / "amber" / "red".

def anomaly_for(app, down, up, iface_down=None, iface_up=None):
    # RED: physically impossible — one process cannot move more than the whole
    # machine did. This is what 52 app-days did before the nettop fix.
    if iface_down and down > iface_down:
        return ("red", "Used more than the whole machine recorded this month.")
    if iface_up and up > iface_up:
        return ("red", "Uploaded more than the whole machine recorded this month.")

    # AMBER: a local-only daemon moving app-scale data. Bonjour is kilobytes.
    if is_system_local(app) and (down + up) > 1_000_000_000:
        return ("amber", "A local network service moving unusually large amounts "
                         "of data.")

    # AMBER: uploading far more than downloading. Rare for normal apps, and the
    # signal that exposed the Claude Helper reading (13.62 GB up vs 4.81 down).
    if up > max(down * 3, 2_000_000_000) and up > 1_000_000_000:
        return ("amber", "Uploaded far more than it downloaded, which is unusual "
                         "for this kind of app.")
    return (None, "")


def friendly_name(proc):
    """Human label for a process name, or the raw name if we don't know it."""
    return FRIENDLY.get(proc, proc)


# ---------- nettop reading ----------

def parse_nettop_block(lines):
    """Parse one nettop report into {"Name.PID": (bytes_in, bytes_out)}.

    Keyed by the FULL "Name.PID". nettop truncates process names to ~15 chars,
    so every Chrome helper type collapses to the identical string
    "Google Chrome H" and all WebKit processes to "com.apple.WebKi". Bucketing
    on that base name and keeping only the largest value (what this module used
    to do) meant that whenever the busiest process exited, the bucket dropped to
    the runner-up and the reset guard booked that runner-up's entire cumulative
    total as one delta. Measured on real data: 1.73x inflation on download,
    6.62x on upload, with 52 app-days exceeding the whole machine's total.

    One entry per PID means every counter is diffed against its own previous
    reading; sample() sums the real deltas into the display name.
    """
    out = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            bout = int(parts[-1])
            bin_ = int(parts[-2])
        except ValueError:
            continue
        out[" ".join(parts[:-2])] = (bin_, bout)
    return out


def base_name(proc):
    """'Google Chrome H.4821' -> 'Google Chrome H'."""
    return proc.rsplit(".", 1)[0] if "." in proc else proc


def _read_nettop(samples=1, interval=1, timeout=12):
    """One-shot read, kept for diagnostics and as a fallback.

    NOT used by the live sampler any more — see NettopReader. Spawning a fresh
    nettop per sample was the bug: each invocation only observes the interval it
    is alive for, so a 1-second burst every 30 seconds captured roughly 1/30th
    of real traffic.
    """
    try:
        out = subprocess.check_output(
            ["nettop", "-P", "-x", "-s", str(interval), "-l", str(samples),
             "-J", "bytes_in,bytes_out"],
            text=True, stderr=subprocess.STDOUT, timeout=timeout)
    except Exception as e:
        if os.environ.get("NUM_DEBUG"):
            print(f"[apps] nettop failed: {type(e).__name__}: {e}")
            out_attr = getattr(e, "output", None)
            if out_attr:
                print(f"[apps] nettop output: {out_attr[:500]!r}")
        return {}
    snaps, cur = [], []
    for line in out.splitlines():
        if "bytes_in" in line and "bytes_out" in line:
            if cur:
                snaps.append(cur)
            cur = []
            continue
        if line.strip():
            cur.append(line)
    if cur:
        snaps.append(cur)
    if not snaps and os.environ.get("NUM_DEBUG"):
        print(f"[apps] nettop returned no parseable snapshot. Raw output "
              f"({len(out)} chars): {out[:500]!r}")
    return parse_nettop_block(snaps[-1]) if snaps else {}


class NettopReader:
    """Keeps the latest per-PID byte counters fresh, using short nettop bursts.

    WHY BURSTS AND NOT ONE LONG-LIVED PROCESS
    -----------------------------------------
    Streaming a persistent `nettop` through a pipe returns NOTHING: it
    block-buffers when stdout is not a terminal, so reports never arrive
    (measured: 0 reports in 30 s). Burst mode with -l N exits on its own and
    flushes, so the output is always readable.

    WHY BURSTS ARE ENOUGH
    ---------------------
    The counters are cumulative since each PROCESS started, and they survive
    nettop being restarted. Verified on real traffic:

        22:19   7,846,304,415 -> 7,902,341,170
        22:30   9,161,847,381 -> 9,231,790,943
        22:53  13,518,643,651 -> 13,567,244,640

    Because the value keeps climbing across separate invocations, comparing a
    fresh reading against the previous one captures everything that happened in
    between INCLUDING while nettop was not running. So the interval controls
    freshness, not accuracy.

    That was never the original bug. The original bug was collapsing every PID
    that shares a truncated name ("Google Chrome H") into one bucket and keeping
    only the largest — which destroyed the comparison. That is fixed by keying
    on the full Name.PID; see parse_nettop_block().
    """

    def __init__(self, interval=10):
        self.interval = max(3, int(interval))
        self._latest = {}
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._reports = 0
        self._errors = 0
        self._last_update_ts = None   # wall-clock time of the last SUCCESSFUL refresh

    def start(self):
        if self._running:
            return
        self._running = True
        # Prime immediately so the first sample() has a baseline to work from.
        self._refresh()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def snapshot(self):
        with self._lock:
            return dict(self._latest)

    def reports_seen(self):
        return self._reports

    def errors_seen(self):
        return self._errors

    def last_update_time(self):
        """Wall-clock time.time() of the last SUCCESSFUL refresh, or None if
        nettop has never once returned data. Callers use this to tell fresh
        data from a stale, unchanged snapshot (nettop timing out doesn't
        update this)."""
        return self._last_update_ts

    def _refresh(self):
        # 25s, not 15s: under heavy P2P/torrent load nettop has more sockets to
        # enumerate and can genuinely take longer than a "normal" ~1-2s burst.
        # Killing and retrying a slow-but-working call wastes the work it had
        # already done; better to let it finish.
        # 60s: with a torrent pulling from a dozen+ active peers at once,
        # nettop has to enumerate every one of those connections, and 25s
        # turned out to still be too tight (100% failures observed during a
        # 47-seed/12-peer download). Slower-but-succeeding beats fast-but-
        # always-failing — the cap-window fix already handles a reader that
        # stays stale for a while.
        data = _read_nettop(samples=1, interval=1, timeout=60)
        if data:
            with self._lock:
                self._latest = data
                self._last_update_ts = time.time()
            self._reports += 1
        else:
            self._errors += 1
        return bool(data)

    def _loop(self):
        while self._running:
            time.sleep(self.interval)
            if not self._running:
                break
            try:
                self._refresh()
            except Exception:
                self._errors += 1


# ---------- storage ----------

def ensure_schema(con):
    """
    Create the app_usage table if missing. One row per (day, network, app);
    deltas are added onto it. Kept separate from core's `usage` table so the
    existing feature is completely untouched.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS app_usage (
            day     TEXT NOT NULL,
            network TEXT NOT NULL,
            app     TEXT NOT NULL,
            down    INTEGER NOT NULL DEFAULT 0,
            up      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, network, app)
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_app_day ON app_usage(day)")
    con.commit()


def _store():
    try:
        from . import store as _s
    except Exception:
        import store as _s
    return _s


def _add_app_usage(con, day, network, app, d_down, d_up):
    """Add a delta onto today's row for this app+network, in that month's file."""
    _store().add_app(day, network, app, d_down, d_up)


# ---------- the slow-beat sampler ----------

class AppSampler:
    """Per-app counterpart to core.Sampler.

    Owns a NettopReader (one long-lived nettop) and drains it on each sample()
    call. Because the reader keeps running between calls, the cumulative
    counters it reports are always comparable with the previous reading, so no
    traffic is lost in the gaps — which is what the old spawn-per-sample
    approach could not do.
    """

    def __init__(self, interval=10):
        self._last = {}            # "Name.PID" -> (last_bin, last_bout)
        self._reader = NettopReader(interval=interval)
        self._started = False
        # Diagnostic breadcrumbs from the MOST RECENT sample() call — read-only
        # for callers (sd.py). Populated with the exact numbers used to decide
        # what got written, so a diagnostic never has to reimplement the diff/
        # cap/guard logic (that reimplementation is what broke the first sd.py).
        self.last_tick = {}        # "Name.PID" -> dict(counter_down, counter_up,
                                    #   raw_down, raw_up, recorded_down, recorded_up,
                                    #   cap_down, cap_up, verdict)
        self.last_vanished = []    # PIDs that were being tracked but disappeared
                                    # from this tick's nettop output entirely
        self._cap_window_start = None  # wall-clock start of the span the NEXT
                                        # cap computation should cover
        self._reader_seen_ts = None    # last NettopReader.last_update_time()
                                        # we've already accounted for

    def _con(self):
        # Writes go through store.add_app (month files); nothing to open here.
        return None

    def start(self):
        if not self._started:
            self._reader.start()
            self._started = True

    def stop(self):
        try:
            self._reader.stop()
        except Exception:
            pass
        self._started = False

    def pause(self):
        """Drop baselines so resuming starts fresh (paused traffic ignored)."""
        self._last = {}

    def _interface_delta(self, headroom=2.0, advance_window=True):
        """(max_down, max_up) the interface moved since the cap window began.

        Read from the raw live store, which core.Sampler writes every second and
        which has been accurate throughout. Returns None if unavailable, in
        which case no cap is applied (better to record than to discard).

        IMPORTANT: the window does NOT simply span "since the previous
        sample() call". nettop can time out and leave the reader's snapshot
        stale for one or more sample() cycles; when it finally returns fresh
        data, the per-process delta legitimately spans however many cycles it
        was stale for — potentially several minutes, not one interval. If the
        cap's window kept resetting every call regardless, a real, entirely
        legitimate multi-cycle delta would be measured against just the last
        interval's interface traffic and get clipped to a fraction of itself
        (measured: a 908 MB real delta capped to 304 MB because the window
        only covered the most recent ~10s while nettop had been stale for the
        prior ~50s). So the caller tells us whether this tick actually
        consumed fresh reader data via `advance_window`; on a stale tick we
        leave `_cap_window_start` untouched so it keeps growing to match.
        """
        try:
            from . import store as _st
        except Exception:
            try:
                import store as _st
            except Exception:
                return None
        now = time.time()
        since = self._cap_window_start
        if since is None:
            self._cap_window_start = now
            return None
        try:
            from datetime import datetime as _dt, timedelta as _td
            cutoff = (_dt.now() - _td(seconds=(now - since) + 5)
                      ).isoformat(timespec="seconds")
            con = _st.connect_live()
            row = con.execute(
                "SELECT COALESCE(SUM(down),0), COALESCE(SUM(up),0) FROM usage "
                "WHERE ts >= ?", (cutoff,)).fetchone()
            if not row:
                return None
            # Headroom covers loopback/LAN traffic nettop sees but netstat may
            # not, plus a floor so a quiet window never caps everything to zero.
            result = (max(int(row[0] * headroom), 8_000_000),
                      max(int(row[1] * headroom), 8_000_000))
        except Exception:
            return None
        finally:
            # Only start a NEW window once we've actually consumed the data
            # this one covers — a stale tick must not reset the clock.
            if advance_window:
                self._cap_window_start = now
        return result

    def reader_ready(self):
        return self._reader.reports_seen() > 0

    def sample(self):
        """Record everything that moved since the previous call.

        Returns the number of apps that moved data, or 0.
        """
        self.start()                      # idempotent
        current = self._reader.snapshot()
        if not current:
            return 0

        # Tag with the network's stable FINGERPRINT (gateway MAC) so the per-app
        # split resolves to the same (possibly renamed) network as everything
        # else. Reuse the last good one on a transient miss.
        try:
            from .core import network_fingerprint
        except Exception:
            from core import network_fingerprint
        iface = active_interface()
        fp = network_fingerprint()
        if fp:
            label = fp
            self._last_fp = fp
        elif getattr(self, "_last_fp", None):
            label = self._last_fp
        else:
            label = network_label(iface, allow_compute=False) if iface else "unknown"
        day = datetime.now().strftime("%Y-%m-%d")

        # How much the INTERFACE actually moved since the cap window began.
        # This is the ceiling for any single process (see the cap check
        # below). "Fresh" means the reader actually delivered new data since
        # our last diff — if nettop has been timing out and the snapshot is
        # unchanged from last tick, the window must NOT reset, or the eventual
        # catch-up delta gets measured against far too short a span.
        reader_ts = self._reader.last_update_time()
        fresh = (self._reader_seen_ts is None) or (reader_ts != self._reader_seen_ts)
        self._reader_seen_ts = reader_ts
        cap = self._interface_delta(advance_window=fresh)
        if bool(os.environ.get("NUM_DEBUG")) and not fresh:
            print("[apps] reader snapshot unchanged this tick (nettop stale) "
                  "— cap window carried over, not reset")

        # Diff EVERY PID against its own previous reading, then sum the real
        # deltas into the display name.
        debug = bool(os.environ.get("NUM_DEBUG"))
        self.last_tick = {}
        agg = {}
        for proc, (bin_, bout) in current.items():
            prev = self._last.get(proc)
            if prev is not None:
                pin, pout = prev
                # RESET GUARD — record ZERO when a counter goes backwards.
                #
                # A counter that falls means the baseline is gone (process
                # restarted, PID reused, or nettop reported a partial value). We
                # cannot know how much traffic happened, and the old code's
                # guess — booking the whole new value as one delta — is
                # catastrophic at scale: qBittorrent's counter sits near 13.9 GB,
                # so a handful of these added 66 GB of traffic that never
                # happened (per-app grew 67 GB while the interface grew 3.5 GB).
                #
                # Recording 0 loses at most one interval of real traffic for one
                # process; guessing invents gigabytes. core.Sampler has always
                # done it this way for the interface counter — this makes the
                # per-app path consistent with it.
                raw_down = bin_ - pin if bin_ >= pin else -1
                raw_up = bout - pout if bout >= pout else -1
                d_down = raw_down if raw_down >= 0 else 0
                d_up = raw_up if raw_up >= 0 else 0
                verdict = "ok"
                if raw_down < 0 or raw_up < 0:
                    verdict = "DROP->0"

                # SANITY CAP against the real interface total.
                #
                # A bad reading hurts twice: once when the counter drops (above),
                # and again on the REBOUND, when it climbs back to its true value
                # and that jump looks like legitimate traffic. The rebound is the
                # bigger error and no per-process rule can spot it.
                #
                # There is a hard physical bound available: no single process can
                # transfer more than the machine's own interface did over the same
                # window. cap is that figure (with headroom for loopback and LAN
                # traffic, which nettop counts and netstat may not).
                #
                # CLAMP, don't zero. The cap is an ESTIMATE — it's built from a
                # different data source (netstat, on a different timer) than the
                # per-process reading (nettop), so the two windows never line up
                # exactly. Zeroing the whole delta the instant it edges past a
                # noisy estimate was the actual cause of the ~25% under-count:
                # a real 30s reading was thrown away entirely rather than losing
                # just the (usually tiny) excess over the estimate. This still
                # catches the rebound-after-reset case (Bug B) — it just no
                # longer punishes ordinary bursty traffic for it.
                cap_down = cap[0] if cap else None
                cap_up = cap[1] if cap else None
                if cap is not None:
                    if d_down > cap[0]:
                        d_down = cap[0]
                        verdict = "CLIPPED" if verdict == "ok" else verdict
                    if d_up > cap[1]:
                        d_up = cap[1]
                        verdict = "CLIPPED" if verdict == "ok" else verdict

                self.last_tick[proc] = {
                    "counter_down": bin_, "counter_up": bout,
                    "raw_down": max(raw_down, 0), "raw_up": max(raw_up, 0),
                    "recorded_down": d_down, "recorded_up": d_up,
                    "cap_down": cap_down, "cap_up": cap_up,
                    "verdict": verdict,
                }
                if debug and (d_down or d_up or verdict != "ok"):
                    print(f"[apps] {proc:<28} raw_down={max(raw_down,0):>12,} "
                          f"rec_down={d_down:>12,} cap_down={(cap_down or 0):>12,} "
                          f"{verdict}")

                if d_down or d_up:
                    key = base_name(proc)
                    c = agg.get(key, (0, 0))
                    agg[key] = (c[0] + d_down, c[1] + d_up)
            self._last[proc] = (bin_, bout)

        # Forget processes that vanished so the baseline can't grow unbounded.
        # These PIDs had a baseline going into this tick but produced no row
        # above — whatever they moved between their last successful diff and
        # now is unrecoverable (no per-process rule can reconstruct it), so
        # surface it explicitly rather than let it disappear silently.
        gone = set(self._last) - set(current)
        self.last_vanished = sorted(gone)
        if debug and gone:
            print(f"[apps] vanished this tick: {sorted(gone)}")
        for g in gone:
            self._last.pop(g, None)

        moved = 0
        for key, (d_down, d_up) in agg.items():
            _add_app_usage(None, day, label, key, d_down, d_up)
            moved += 1
        return moved


# ---------- queries for the UI ----------
#
# These feed the "Apps" tab: a ranked list of apps for a period, each row
# expandable to its per-network split. Period filters mirror core.py's style
# (substr on the day string).

def _month_rows(period, value):
    """(day, network, app, down, up) rows for a period, read from month files."""
    st = _store()
    if period == "day":        # value = YYYY-MM-DD
        return [r for r in st.month_apps(value[:7]) if r[0] == value]
    if period == "month":      # value = YYYY-MM
        return st.month_apps(value)
    if period == "year":       # value = YYYY
        rows = []
        for m in st.available_months():
            if m.startswith(str(value)):
                rows.extend(st.month_apps(m))
        return rows
    rows = []                  # 'all'
    for m in st.available_months():
        rows.extend(st.month_apps(m))
    return rows


def app_breakdown(con, period="month", value=None):
    """Ranked (app, friendly, down, up) for a period, busiest first.

    BUG FIXED (2026-10-01): a sealed month's day/network app rows are
    deliberately DROPPED once collapse_month() runs — that's the whole point
    of collapsing, see store.py. This function used to always read those
    rows via _month_rows()/store.month_apps(), which is correct for the
    live current month but returns [] for any past, collapsed month — the
    Apps tab showed "Collecting per-app data" for a month with real,
    correctly-recorded history sitting in app_snapshot the whole time.
    Fixed: for a single sealed month, read the always-correct
    store.month_app_totals() (snapshot-aware) instead.

    One known, intentional limitation this doesn't attempt to fix: network
    exclusion can't be re-applied to an already-collapsed snapshot, since
    app_snapshot never stored which network each byte came from (that detail
    is specifically what collapsing discards). Excluding a network AFTER a
    month has already sealed won't retroactively affect that month's app
    totals. Narrow edge case; the alternative (keeping full day/network
    detail forever) is exactly the storage bloat collapsing exists to avoid.
    """
    try:
        from .core import excluded_fingerprints
    except Exception:
        from core import excluded_fingerprints
    ex = excluded_fingerprints()

    if period == "month" and value:
        st = _store()
        if st.is_sealed(value):
            totals = st.month_app_totals(value)
            out = [(a, friendly_name(a), d, u) for a, d, u in totals]
            out.sort(key=lambda r: r[2] + r[3], reverse=True)
            return out

    agg = {}
    for _day, net, app, d, u in _month_rows(period, value):
        if net in ex:                      # honour network exclusion here too
            continue
        c = agg.get(app, (0, 0))
        agg[app] = (c[0] + (d or 0), c[1] + (u or 0))
    out = [(a, friendly_name(a), d, u) for a, (d, u) in agg.items()]
    out.sort(key=lambda r: r[2] + r[3], reverse=True)
    return out


def app_networks(con, app, period="month", value=None):
    """Per-network split for ONE app: (display_name, down, up), busiest first.

    Returns [] for an app in a SEALED (collapsed) month — not a bug. Collapsing
    deliberately discards day/network detail, keeping only one (app, down, up)
    row per app for the whole month (see store.collapse_month). There is
    nothing left to reconstruct a network split from for a past month; this
    is a real, permanent limitation of the storage design, not a query that
    needs fixing. Only the live current month (not yet collapsed) can show
    this. dashboard.py should render an empty list here as "not available for
    past months", not as a loading/empty-state bug.
    """
    try:
        from .core import display_network, excluded_fingerprints
    except Exception:
        from core import display_network, excluded_fingerprints
    ex = excluded_fingerprints()
    agg = {}
    for _day, net, a, d, u in _month_rows(period, value):
        if a != app or net in ex:
            continue
        name = display_network(net)
        c = agg.get(name, (0, 0))
        agg[name] = (c[0] + (d or 0), c[1] + (u or 0))
    out = [(n, d, u) for n, (d, u) in agg.items()]
    out.sort(key=lambda r: r[1] + r[2], reverse=True)
    return out


def app_months_available(con=None):
    """Months that have per-app data, newest first.

    BUG FIXED (2026-10-01): the old check (`st.month_apps(m)`) reads only
    app_usage, which is empty for any sealed/collapsed month by design — so
    this silently dropped every past month from the Apps tab's navigable
    month list the moment it sealed. Sealed months now check
    month_app_totals() (collapse-aware); the current, not-yet-sealed month
    still checks month_apps() since it has no snapshot yet.
    """
    st = _store()
    out = []
    for m in st.available_months():
        if st.is_sealed(m):
            if st.month_app_totals(m):
                out.append(m)
        elif st.month_apps(m):
            out.append(m)
    return out


def app_years_available(con=None):
    return sorted({m[:4] for m in app_months_available()}, reverse=True)


def app_dashboard_payload(con, period="month", value=None, top=25):
    """Structure the Apps tab renders.

    Apps are split into two groups:
      apps    — real applications; these sum to the tab total
      system  — local-network services (Bonjour, AirDrop, Continuity...). Shown
                in their own group, NEVER counted in the total.

    Each row may carry an anomaly flag (see anomaly_for) with a plain-language
    reason, so something behaving oddly is visible rather than silently filtered.
    """
    # Interface totals for the same period — the ceiling any single app is
    # measured against for the "impossible" flag.
    iface_d = iface_u = 0
    try:
        from .core import _store as _cs
    except Exception:
        try:
            from core import _store as _cs
        except Exception:
            _cs = None
    try:
        if _cs and period == "month" and value:
            # Skip excluded networks, exactly as the Month tab does
            # (popover._daily) — otherwise the two tabs disagree whenever a
            # network is excluded.
            try:
                from .core import excluded_fingerprints as _exf
            except Exception:
                from core import excluded_fingerprints as _exf
            _ex = _exf()
            for _day, _net, dn, up in _cs().month_daily(value):
                if _net in _ex:
                    continue
                iface_d += dn or 0
                iface_u += up or 0
    except Exception:
        pass

    apps, system = [], []
    tot_d = tot_u = 0
    for app, friendly, down, up in app_breakdown(con, period, value)[:top]:
        nets = [{"network": n, "down": d, "up": u}
                for (n, d, u) in app_networks(con, app, period, value)]
        level, reason = anomaly_for(app, down, up, iface_d, iface_u)
        row = {"app": app, "friendly": friendly, "down": down, "up": up,
               "networks": nets, "flag": level, "reason": reason}
        if is_system_local(app):
            row["system"] = True
            system.append(row)
        else:
            apps.append(row)
            tot_d += down or 0
            tot_u += up or 0

    # OVERHEAD ROW (2026-10-01): make the Apps total equal the Month total.
    #
    # The interface counter (Month tab) includes bytes no app can ever claim:
    # packet headers, TCP acknowledgements, system services (shown in their
    # own group, never in the apps sum), apps past the top-N list, and
    # processes that exited between samples. Measured on real downloads:
    # ~6% of download, more of upload. Rather than hide that difference or
    # invent per-app numbers by scaling, show it as one honest, labelled row.
    #
    # Clamped at zero per direction: if apps ever exceed the interface (nettop
    # also counts some loopback/LAN traffic), no negative row is shown and
    # the total simply stays the apps sum. Only for single months, the only
    # period the Apps tab navigates.
    ov_d = max(0, iface_d - tot_d)
    ov_u = max(0, iface_u - tot_u)
    if period == "month" and (ov_d or ov_u):
        label = "Network overhead & untracked"
        apps.append({"app": label, "friendly": label,
                     "down": ov_d, "up": ov_u, "networks": [],
                     "flag": None, "reason": "", "overhead": True})
        tot_d += ov_d
        tot_u += ov_u

    return {"period": period, "value": value,
            "apps": apps, "system": system,
            "total": {"down": tot_d, "up": tot_u},
            "iface": {"down": iface_d, "up": iface_u}}
