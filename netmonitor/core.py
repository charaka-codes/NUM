"""
core.py — network detection, byte counters, and SQLite storage.

Pure logic, no UI. Imported by the menu bar app and the CLI report tool.
Stores only usage totals (bytes up/down per network per interval).
"""

import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".netmonitor" / "usage.db"


# ---------- macOS system queries ----------

def active_interface():
    """Primary active network interface, e.g. 'en0', or None."""
    try:
        out = subprocess.check_output(
            ["route", "get", "default"], text=True, stderr=subprocess.DEVNULL)
        m = re.search(r"interface:\s*(\S+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _wifi_ssid():
    """
    Current Wi-Fi SSID. On macOS 26 the OS hides this unless the app has
    Location permission. We try several methods in order of reliability.
    """
    # Method 1: CoreWLAN (works once Location permission is granted)
    try:
        import objc
        from CoreWLAN import CWWiFiClient
        client = CWWiFiClient.sharedWiFiClient()
        iface = client.interface()
        if iface is not None:
            ssid = iface.ssid()
            if ssid:
                return str(ssid)
    except Exception:
        pass

    # Method 2: system_profiler (works on Tahoe when location is granted)
    try:
        out = subprocess.check_output(
            ["system_profiler", "SPAirPortDataType"],
            text=True, stderr=subprocess.DEVNULL, timeout=5)
        # The current network appears under "Current Network Information:"
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if "Current Network Information:" in line:
                # the next non-empty line is the SSID (indented, ends with ':')
                for j in range(i + 1, min(i + 4, len(lines))):
                    cand = lines[j].strip().rstrip(":").strip()
                    if cand:
                        return cand
    except Exception:
        pass

    # Method 3: legacy networksetup (deprecated on Tahoe but works pre-26)
    try:
        out = subprocess.check_output(
            ["networksetup", "-getairportnetwork", "en0"],
            text=True, stderr=subprocess.DEVNULL)
        m = re.search(r"Current Wi-Fi Network:\s*(.+)", out)
        if m:
            name = m.group(1).strip()
            if name and "not associated" not in name.lower():
                return name
    except Exception:
        pass

    return None


def request_location_permission():
    """
    Ask macOS for Location permission (needed to read the Wi-Fi name on
    macOS 26). Safe no-op if CoreLocation isn't available. Should be called
    once at startup from the bundled app.
    """
    try:
        from CoreLocation import CLLocationManager
        mgr = CLLocationManager.alloc().init()
        try:
            mgr.requestWhenInUseAuthorization()
        except Exception:
            pass
        # Keep a reference so it isn't garbage-collected mid-request.
        return mgr
    except Exception:
        return None


def network_label(interface):
    """Friendly label: Wi-Fi SSID, or hardware port name (Ethernet, iPhone USB)."""
    if not interface:
        return "unknown"

    ssid = _wifi_ssid()
    if ssid:
        return f"Wi-Fi: {ssid}"

    try:
        out = subprocess.check_output(
            ["networksetup", "-listallhardwareports"],
            text=True, stderr=subprocess.DEVNULL)
        for block in out.split("Hardware Port:"):
            if f"Device: {interface}" in block:
                name = block.strip().splitlines()[0].strip()
                return name or interface
    except Exception:
        pass
    return interface


def interface_counters(interface):
    """
    (bytes_in, bytes_out) cumulative for an interface from `netstat -ibn`.

    Column positions can shift when the <Link#N> row has a blank Address field,
    so we locate Ibytes/Obytes by header name and, if that row is short by one
    (blank Address), shift the indices left by one to compensate. We pick the
    interface's row with the largest byte totals (the hardware Link row).
    """
    try:
        out = subprocess.check_output(
            ["netstat", "-ibn"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None

    lines = out.splitlines()
    if len(lines) < 2:
        return None

    header = lines[0].split()
    ncols = len(header)
    try:
        i_ib = header.index("Ibytes")
        i_ob = header.index("Obytes")
    except ValueError:
        i_ib, i_ob = 6, 9

    best = None
    for line in lines[1:]:
        parts = line.split()
        if not parts or parts[0] != interface:
            continue
        # If this row has one fewer column than the header, the Address field
        # was blank → every column after it shifts left by one.
        shift = ncols - len(parts)
        ib_idx = i_ib - shift
        ob_idx = i_ob - shift
        try:
            ib = int(parts[ib_idx])
            ob = int(parts[ob_idx])
        except (ValueError, IndexError):
            continue
        if best is None or (ib + ob) > (best[0] + best[1]):
            best = (ib, ob)

    return best


# ---------- storage ----------

def set_launch_at_login(enabled):
    """
    Register or unregister the app to launch at login using SMAppService
    (macOS 13+). Returns True on success. No-ops safely if the API isn't
    available (e.g. running from source rather than a bundled .app).
    """
    try:
        from ServiceManagement import SMAppService
        service = SMAppService.mainAppService()
        if enabled:
            ok, err = service.registerAndReturnError_(None)
            return bool(ok)
        else:
            ok, err = service.unregisterAndReturnError_(None)
            return bool(ok)
    except Exception:
        return False


def launch_at_login_status():
    """Return True if the app is currently registered to launch at login."""
    try:
        from ServiceManagement import SMAppService
        service = SMAppService.mainAppService()
        # status 1 = enabled (SMAppServiceStatusEnabled)
        return int(service.status()) == 1
    except Exception:
        return False


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False so a connection can be used from the thread that
    # created it even if AppKit hops callbacks around; we keep the sampler on
    # its OWN connection (see Sampler) so writes and UI reads don't collide.
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
    con.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            ts        TEXT NOT NULL,
            network   TEXT NOT NULL,
            interface TEXT NOT NULL,
            down      INTEGER NOT NULL,
            up        INTEGER NOT NULL
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON usage(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_net ON usage(network)")
    con.commit()
    return con


def record(con, network, interface, down, up):
    con.execute("INSERT INTO usage VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"),
                 network, interface, down, up))
    con.commit()


# ---------- queries for the UI / reports ----------

def month_breakdown(con, month):
    """List of (network, down, up) for a YYYY-MM month, busiest first."""
    return con.execute("""
        SELECT network, SUM(down), SUM(up) FROM usage
        WHERE substr(ts,1,7) = ?
        GROUP BY network ORDER BY SUM(down)+SUM(up) DESC
    """, (month,)).fetchall()


def day_total(con, day):
    """(down, up) totals for a YYYY-MM-DD day."""
    row = con.execute("""
        SELECT COALESCE(SUM(down),0), COALESCE(SUM(up),0) FROM usage
        WHERE substr(ts,1,10) = ?
    """, (day,)).fetchone()
    return row or (0, 0)


def refresh_view(con):
    """
    Force a connection to drop any cached read snapshot so the next query
    sees the latest committed data. In WAL mode a long-lived connection can
    otherwise keep returning an old snapshot, which made totals appear frozen
    even though the sampler was writing. Ending any implicit transaction with
    a commit refreshes the view cheaply.
    """
    try:
        con.commit()
    except Exception:
        pass


def months_available(con):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT substr(ts,1,7) m FROM usage ORDER BY m DESC")]


def years_available(con):
    """List of distinct YYYY strings that have data, newest first."""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT substr(ts,1,4) y FROM usage ORDER BY y DESC")]


def year_breakdown(con, year):
    """List of (network, down, up) for a whole YYYY, busiest first."""
    return con.execute("""
        SELECT network, SUM(down), SUM(up) FROM usage
        WHERE substr(ts,1,4) = ?
        GROUP BY network ORDER BY SUM(down)+SUM(up) DESC
    """, (year,)).fetchall()


def all_breakdown(con):
    """List of (network, down, up) across all history, busiest first."""
    return con.execute("""
        SELECT network, SUM(down), SUM(up) FROM usage
        GROUP BY network ORDER BY SUM(down)+SUM(up) DESC
    """).fetchall()


def month_daily_rows(con, month):
    """Per-day, per-network rows for a month: (day, network, down, up)."""
    return con.execute("""
        SELECT substr(ts,1,10) d, network, SUM(down), SUM(up) FROM usage
        WHERE substr(ts,1,7) = ?
        GROUP BY d, network ORDER BY d, SUM(down)+SUM(up) DESC
    """, (month,)).fetchall()


def daily_totals(con, month):
    """
    Dict of {day_number: (down, up)} for every day in a YYYY-MM that has data.
    e.g. {1: (4500000000, 860000000), 5: (...), 12: (...)}
    """
    rows = con.execute("""
        SELECT CAST(substr(ts,9,2) AS INTEGER) AS d,
               SUM(down), SUM(up)
        FROM usage
        WHERE substr(ts,1,7) = ?
        GROUP BY d
    """, (month,)).fetchall()
    return {d: (down, up) for d, down, up in rows}


def day_network_breakdown(con, day):
    """List of (network, down, up) for a single YYYY-MM-DD day, busiest first."""
    return con.execute("""
        SELECT network, SUM(down), SUM(up) FROM usage
        WHERE substr(ts,1,10) = ?
        GROUP BY network ORDER BY SUM(down)+SUM(up) DESC
    """, (day,)).fetchall()


def range_daily(con, start_day, end_day):
    """
    Dict {YYYY-MM-DD: (down, up)} for each day in [start_day, end_day] inclusive
    that has data. Used by the week view.
    """
    rows = con.execute("""
        SELECT substr(ts,1,10) AS d, SUM(down), SUM(up) FROM usage
        WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
        GROUP BY d
    """, (start_day, end_day)).fetchall()
    return {d: (down, up) for d, down, up in rows}


def range_total(con, start_day, end_day):
    """(down, up) totals across a day range, inclusive."""
    row = con.execute("""
        SELECT COALESCE(SUM(down),0), COALESCE(SUM(up),0) FROM usage
        WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
    """, (start_day, end_day)).fetchone()
    return row or (0, 0)


def range_network(con, start_day, end_day):
    """List of (network, down, up) across a day range, inclusive, busiest first."""
    return con.execute("""
        SELECT network, SUM(down), SUM(up) FROM usage
        WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
        GROUP BY network ORDER BY SUM(down)+SUM(up) DESC
    """, (start_day, end_day)).fetchall()


def year_months(con, year):
    """Dict {month_number: (down, up)} for each month of a year that has data."""
    rows = con.execute("""
        SELECT CAST(substr(ts,6,2) AS INTEGER) AS m, SUM(down), SUM(up)
        FROM usage WHERE substr(ts,1,4) = ?
        GROUP BY m
    """, (str(year),)).fetchall()
    return {m: (down, up) for m, down, up in rows}


def recent_speed(con, seconds=120):
    """
    Approximate current down/up speed in bytes-per-second, averaged over the
    last `seconds` of recorded samples. Returns (down_bps, up_bps).
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(seconds=seconds)).isoformat()
    row = con.execute("""
        SELECT COALESCE(SUM(down),0), COALESCE(SUM(up),0)
        FROM usage WHERE ts >= ?
    """, (cutoff,)).fetchone()
    down, up = row or (0, 0)
    return down / seconds, up / seconds


# ---------- formatting ----------

def human(nbytes):
    n = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}"
        n /= 1024


class Sampler:
    """Holds counter state and records deltas. Call sample() each tick.

    Creates its OWN database connection lazily on first use, so it belongs to
    the background sampling thread and never collides with the main-thread UI
    connection. WAL mode lets the UI read concurrently.
    """

    def __init__(self, con=None):
        self._own = None          # our private write connection
        self._last = {}

    def _con(self):
        if self._own is None:
            self._own = connect()
        return self._own

    def pause(self):
        """Forget the last counter reading so that when tracking resumes we
        start a fresh baseline (the paused traffic isn't recorded)."""
        self._last = {}

    def sample(self):
        """Take one reading. Returns (label, d_down, d_up) or None."""
        iface = active_interface()
        if not iface:
            return None
        counters = interface_counters(iface)
        if not counters:
            return None
        cin, cout = counters
        label = network_label(iface)
        result = None
        if iface in self._last:
            pin, pout = self._last[iface]
            d_down = cin - pin if cin >= pin else 0
            d_up = cout - pout if cout >= pout else 0
            if d_down or d_up:
                record(self._con(), label, iface, d_down, d_up)
            result = (label, d_down, d_up)
        self._last[iface] = (cin, cout)
        return result
