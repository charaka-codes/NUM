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

try:
    from .core import connect, active_interface, network_label, refresh_view, human
except ImportError:
    from core import connect, active_interface, network_label, refresh_view, human

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

SYSTEM_LOCAL = {
    "mDNSResponder", "sharingd", "rapportd", "netbiosd", "airportd",
    "locationd", "AirPlayXPCHelper", "AirPlayUIAgent", "remoted",
    "wifianalyticsd", "identityservice", "helpd", "nehelper", "configd",
    "UserEventAgent",
}


def is_system_local(proc):
    if proc in SYSTEM_LOCAL:
        return True
    base = proc.rsplit(".", 1)[0] if "." in proc else proc
    return base in SYSTEM_LOCAL


def anomaly_for(app, down, up, iface_down=None, iface_up=None):
    if iface_down and down > iface_down:
        return ("red", "Used more than the whole machine recorded this month.")
    if iface_up and up > iface_up:
        return ("red", "Uploaded more than the whole machine recorded this month.")
    if is_system_local(app) and (down + up) > 1_000_000_000:
        return ("amber", "A local network service moving unusually large amounts "
                         "of data.")
    if up > max(down * 3, 2_000_000_000) and up > 1_000_000_000:
        return ("amber", "Uploaded far more than it downloaded, which is unusual "
                         "for this kind of app.")
    return (None, "")


def friendly_name(proc):
    return FRIENDLY.get(proc, proc)


def parse_nettop_block(lines):
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
    return proc.rsplit(".", 1)[0] if "." in proc else proc


def _read_nettop(samples=1, interval=1, timeout=12):
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
    def __init__(self, interval=10):
        self.interval = max(3, int(interval))
        self._latest = {}
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._reports = 0
        self._errors = 0
        self._last_update_ts = None

    def start(self):
        if self._running:
            return
        self._running = True
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
        return self._last_update_ts

    def _refresh(self):
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


def ensure_schema(con):
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
    _store().add_app(day, network, app, d_down, d_up)


class AppSampler:
    def __init__(self, interval=10):
        self._last = {}
        self._reader = NettopReader(interval=interval)
        self._started = False
        self.last_tick = {}
        self.last_vanished = []
        self._cap_window_start = None
        self._reader_seen_ts = None

    def _con(self):
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
        self._last = {}

    def _interface_delta(self, headroom=2.0, advance_window=True):
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
            result = (max(int(row[0] * headroom), 8_000_000),
                      max(int(row[1] * headroom), 8_000_000))
        except Exception:
            return None
        finally:
            if advance_window:
                self._cap_window_start = now
        return result

    def reader_ready(self):
        return self._reader.reports_seen() > 0

    def sample(self):
        self.start()
        current = self._reader.snapshot()
        if not current:
            return 0

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

        reader_ts = self._reader.last_update_time()
        fresh = (self._reader_seen_ts is None) or (reader_ts != self._reader_seen_ts)
        self._reader_seen_ts = reader_ts
        cap = self._interface_delta(advance_window=fresh)
        if bool(os.environ.get("NUM_DEBUG")) and not fresh:
            print("[apps] reader snapshot unchanged this tick (nettop stale) "
                  "— cap window carried over, not reset")

        debug = bool(os.environ.get("NUM_DEBUG"))
        self.last_tick = {}
        agg = {}
        for proc, (bin_, bout) in current.items():
            prev = self._last.get(proc)
            if prev is not None:
                pin, pout = prev
                raw_down = bin_ - pin if bin_ >= pin else -1
                raw_up = bout - pout if bout >= pout else -1
                d_down = raw_down if raw_down >= 0 else 0
                d_up = raw_up if raw_up >= 0 else 0
                verdict = "ok"
                if raw_down < 0 or raw_up < 0:
                    verdict = "DROP->0"

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
    """Ranked (app, friendly, down, u) for a period, busiest first.

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
    """Structure the Apps tab renders."""
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
            for _day, _net, dn, up in _cs().month_daily(value):
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

    return {"period": period, "value": value,
            "apps": apps, "system": system,
            "total": {"down": tot_d, "up": tot_u},
            "iface": {"down": iface_d, "up": iface_u}}
