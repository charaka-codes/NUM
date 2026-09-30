"""
core.py — network detection, byte counters, and SQLite storage.

Pure logic, no UI. Imported by the menu bar app and the CLI report tool.
Stores only usage totals (bytes up/down per network per interval).
"""

import os
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

# Data directory is profile-aware. v2 is now the real, shipped app (v1 was
# only for running the two side by side during the rebuild), so it's the
# DEFAULT when NUM_PROFILE isn't set — not "" as before. That old default
# was fine from Terminal (where NUM_PROFILE=v2 was typed each launch) but
# silently broke for a double-clicked .app / DMG install: a GUI-launched app
# never inherits a shell's environment variables, so it fell back to the old
# v1 folder and looked like months of history had vanished. Set
# NUM_PROFILE=v1 explicitly if you ever need the old location back (e.g. to
# inspect legacy data); anything else, including unset, means v2.
#   v2 (default, or NUM_PROFILE=v2): ~/.netmonitor-v2/usage.db
#   v1 (NUM_PROFILE=v1 explicitly):  ~/.netmonitor/usage.db
_PROFILE = os.environ.get("NUM_PROFILE", "v2").strip()
_DATA_DIRNAME = ".netmonitor" if _PROFILE == "v1" else f".netmonitor-{_PROFILE}"

DATA_DIR = Path.home() / _DATA_DIRNAME
DB_PATH = DATA_DIR / "usage.db"


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


def _gateway_ip():
    """Default gateway (router) IP, e.g. '192.168.1.1', or None."""
    try:
        out = subprocess.check_output(
            ["route", "get", "default"], text=True, stderr=subprocess.DEVNULL)
        m = re.search(r"gateway:\s*(\S+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


_FP_CACHE = {}  # gateway_ip -> "gw:..mac.." (remembers MAC across transient ARP misses)


def network_fingerprint():
    """
    A STABLE per-network identifier that does NOT require Location permission.

    Uses the default gateway's (router's) hardware MAC from the ARP table:
    unique per physical router, stable across reconnects/reboots, readable
    without Location permission.

    MUST be fast — it runs on the 2s sampler tick, so no pings, no retries, a
    short timeout. For a transient ARP miss we reuse the last MAC we saw for
    this gateway (cache), only falling back to an 'ip:' key if we've never seen
    the MAC. This keeps the sampler responsive and avoids phantom networks.
    """
    gw = _gateway_ip()
    if not gw:
        return None
    try:
        out = subprocess.check_output(
            ["arp", "-n", gw], text=True, stderr=subprocess.DEVNULL, timeout=2)
        m = re.search(r"([0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5})", out)
        if m:
            parts = m.group(1).split(":")
            mac_fp = "gw:" + ":".join(p.zfill(2).lower() for p in parts)
            _FP_CACHE[gw] = mac_fp
            return mac_fp
    except Exception:
        pass
    # Transient miss: reuse the last known MAC for this gateway if we have one,
    # so we don't spawn a phantom 'ip:' network for the same real network.
    if gw in _FP_CACHE:
        return _FP_CACHE[gw]
    return "ip:" + gw


def _load_networks():
    try:
        from . import settings as _s
    except Exception:
        import settings as _s
    data = _s.load()
    nets = data.get("networks") or {}
    counter = int(data.get("network_counter") or 0)
    return _s, data, nets, counter


_NET_REG_CACHE = {}  # fingerprint -> last (ssid) we registered, to skip disk I/O


def resolve_network_name(fingerprint, ssid=None):
    """
    Resolve the DISPLAY name for a network fingerprint, by strict priority:
      1. the user's custom name, if set                     -> always wins
      2. the real SSID, but ONLY if network names are enabled (permission on)
      3. a stable auto name ("Network 1", "Network 2", ...)

    Registers new fingerprints (assigning the next "Network N") and remembers
    the last-seen SSID. IMPORTANT: this runs on the 1s sampler tick, so it must
    NOT hit disk every call. We keep an in-memory cache of what we've already
    registered; if the fingerprint+ssid is unchanged, we return the resolved
    name from the cached store WITHOUT reading or writing settings.
    """
    if not fingerprint:
        if _names_enabled() and ssid:
            return f"Wi-Fi: {ssid}"
        return "Network"

    # Fast path: already registered this fingerprint with this ssid -> no disk.
    cached = _NET_REG_CACHE.get(fingerprint)
    if cached is not None and cached.get("ssid") == ssid:
        entry = cached["entry"]
        if entry.get("name"):
            return entry["name"]
        if _names_enabled() and entry.get("ssid"):
            return f"Wi-Fi: {entry['ssid']}"
        return entry.get("auto") or "Network"

    # Slow path: read settings, register/update, write only if changed.
    _s, data, nets, counter = _load_networks()
    entry = nets.get(fingerprint)
    changed = False

    if entry is None:
        counter += 1
        entry = {"name": None, "auto": f"Network {counter}", "ssid": None}
        nets[fingerprint] = entry
        data["network_counter"] = counter
        changed = True

    if ssid and entry.get("ssid") != ssid:
        entry["ssid"] = ssid
        changed = True

    if changed:
        data["networks"] = nets
        _s.save(data)

    # Cache what we now know so subsequent ticks skip disk entirely.
    _NET_REG_CACHE[fingerprint] = {"ssid": ssid, "entry": dict(entry)}

    if entry.get("name"):
        return entry["name"]
    if _names_enabled() and entry.get("ssid"):
        return f"Wi-Fi: {entry['ssid']}"
    return entry.get("auto") or "Network"


def warm_fingerprint(attempts=5, delay=0.5):
    """Populate the shared fingerprint cache BEFORE sampling starts, so neither
    sampler ever records under a transient 'ip:' fallback (which creates a
    phantom duplicate network). Safe to call once at app startup — a brief wait
    here is fine because no sampling has begun yet. Nudges ARP with a ping if
    the first read misses.
    Returns the fingerprint, or None if there's genuinely no network."""
    import time as _t
    gw = _gateway_ip()
    if not gw:
        return None
    for i in range(max(1, attempts)):
        fp = network_fingerprint()          # populates _FP_CACHE on success
        if fp and fp.startswith("gw:"):
            return fp
        # Miss — nudge ARP (only at startup, never on the hot path) and retry.
        try:
            subprocess.run(["ping", "-c", "1", "-t", "1", gw],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=2)
        except Exception:
            pass
        _t.sleep(delay)
    # Give back whatever we have (may be an 'ip:' key if ARP never resolved).
    return network_fingerprint()


def _invalidate_net_cache():
    """Clear the in-memory registration cache so the next resolve re-reads fresh
    state from settings (call after a rename or a network_names toggle)."""
    _NET_REG_CACHE.clear()


def set_network_excluded(fingerprint, excluded):
    """Mark a network as excluded from (or included in) the totals. Excluded
    networks keep their data but are left out of the big total and menu number.
    Reversible."""
    if not fingerprint:
        return
    _s, data, nets, counter = _load_networks()
    entry = nets.get(fingerprint)
    if entry is None:
        counter += 1
        entry = {"name": None, "auto": f"Network {counter}", "ssid": None}
        data["network_counter"] = counter
    entry["excluded"] = bool(excluded)
    nets[fingerprint] = entry
    data["networks"] = nets
    _s.save(data)
    _invalidate_net_cache()


def delete_network(fingerprint, con=None):
    """Permanently delete a network: remove its name entry AND all its usage and
    per-app rows from the database. Not reversible."""
    if not fingerprint:
        return
    _s, data, nets, counter = _load_networks()
    if fingerprint in nets:
        del nets[fingerprint]
        data["networks"] = nets
        _s.save(data)
        _invalidate_net_cache()
    # Remove this network's rows from every month file (and the live store).
    try:
        _store().delete_network_everywhere(fingerprint)
    except Exception:
        pass
    try:
        c = con or connect()
        c.execute("DELETE FROM usage WHERE network = ?", (fingerprint,))
        c.commit()
    except Exception:
        pass


def erase_all_data(con=None):
    """Permanently wipe ALL recorded usage: every row in `usage` and
    `app_usage`, and the entire known-networks registry (names, auto labels,
    exclusions). Not reversible. Settings other than the network registry are
    left untouched. Returns True on success."""
    ok = True
    # Clear the data rows.
    try:
        _store().erase_all()
    except Exception:
        ok = False
    # Reset the network registry so old fingerprints/names don't linger.
    try:
        _s, data, nets, counter = _load_networks()
        data["networks"] = {}
        data["network_counter"] = 0
        _s.save(data)
        _invalidate_net_cache()
    except Exception:
        ok = False
    return ok


def excluded_fingerprints():
    """Set of fingerprints the user has excluded from totals."""
    _s, data, nets, counter = _load_networks()
    return {fp for fp, e in nets.items() if e.get("excluded")}


def set_network_name(fingerprint, name):
    """Set (or clear, if name is falsy) the user's custom name for a network."""
    if not fingerprint:
        return
    _s, data, nets, counter = _load_networks()
    entry = nets.get(fingerprint)
    if entry is None:
        counter += 1
        entry = {"name": None, "auto": f"Network {counter}", "ssid": None}
        data["network_counter"] = counter
    entry["name"] = name.strip() if name else None
    nets[fingerprint] = entry
    data["networks"] = nets
    _s.save(data)
    _invalidate_net_cache()  # so the sampler/UI pick up the new name at once


def known_networks():
    """Return a list of known networks for the Settings UI:
        [{"fp":..., "display":"Home", "custom":"Home", "auto":"Network 1",
          "has_ssid":True, "excluded":False}]
    'display' already respects the permission rule (no SSID leak)."""
    _s, data, nets, counter = _load_networks()
    out = []
    for fp, entry in nets.items():
        display = resolve_network_name(fp, entry.get("ssid"))
        out.append({
            "fp": fp,
            "display": display,
            "custom": entry.get("name"),
            "auto": entry.get("auto"),
            "has_ssid": bool(entry.get("ssid")),
            "excluded": bool(entry.get("excluded")),
        })
    return out



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


import time as _time
_LABEL_CACHE = {"iface": None, "label": None, "t": 0.0}
_LABEL_TTL = 30.0  # seconds — SSID rarely changes; avoid the costly lookup each tick


def clear_label_cache():
    """Force the next network_label() call to recompute (e.g. after the user
    toggles 'Show network names')."""
    _LABEL_CACHE.update(iface=None, label=None, t=0.0)
    try:
        _invalidate_net_cache()
    except Exception:
        pass


def _names_enabled():
    """Whether the user wants Wi-Fi names shown (needs Location). Defaults to
    True if settings can't be read, matching the historical behaviour."""
    try:
        from . import settings as _s
        return bool(_s.load().get("network_names", True))
    except Exception:
        try:
            import settings as _s
            return bool(_s.load().get("network_names", True))
        except Exception:
            return True


def network_label(interface, _cache=True, allow_compute=True):
    """Friendly label: Wi-Fi SSID, or hardware port name (Ethernet, iPhone USB).

    The SSID lookup can be SLOW — on macOS 26 without Location permission it
    falls through to `system_profiler`, which can take ~5s. We cache the result
    and, crucially, callers on the UI path pass allow_compute=False so they NEVER
    trigger the slow lookup — they get the cached value (even if slightly stale)
    or a safe placeholder. Only the background sampler passes allow_compute=True,
    so the expensive call always happens off the UI path."""
    if not interface:
        return "unknown"

    now = _time.time()
    fresh = (_LABEL_CACHE["label"] is not None
             and _LABEL_CACHE["iface"] == interface
             and (now - _LABEL_CACHE["t"]) < _LABEL_TTL)
    if _cache and fresh:
        return _LABEL_CACHE["label"]

    if not allow_compute:
        # UI path: never run the slow lookup. Return the last known label for
        # this interface if we have one (even if stale), else a safe generic.
        if (_LABEL_CACHE["label"] is not None
                and _LABEL_CACHE["iface"] == interface):
            return _LABEL_CACHE["label"]
        return "Wi-Fi"  # placeholder until the background thread fills it in

    label = _compute_network_label(interface)
    _LABEL_CACHE.update(iface=interface, label=label, t=now)
    return label


def _compute_network_label(interface):
    """Actual (possibly slow) label computation — see network_label for caching."""
    # If the user has turned OFF network names, don't read the SSID at all
    # (skips the Location-dependent, possibly-slow lookup) and just label by
    # connection type.
    if not _names_enabled():
        try:
            out = subprocess.check_output(
                ["networksetup", "-listallhardwareports"],
                text=True, stderr=subprocess.DEVNULL)
            for block in out.split("Hardware Port:"):
                if f"Device: {interface}" in block:
                    name = block.strip().splitlines()[0].strip()
                    # Generic type label, no SSID.
                    if "Wi-Fi" in name or "AirPort" in name:
                        return "Wi-Fi"
                    return name or interface
        except Exception:
            pass
        return "Wi-Fi"

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
    """Compatibility shim: callers that used to get the single usage.db now get
    the live (raw per-second) connection. Every aggregate read goes through the
    month files instead and ignores the connection argument entirely."""
    return _store().connect_live()


def record(con, network, interface, down, up):
    con.execute("INSERT INTO usage VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"),
                 network, interface, down, up))
    con.commit()


# ---------- queries for the UI / reports ----------

def _store():
    """Lazy import of the split-by-month store (avoids a circular import)."""
    try:
        from . import store as _s
    except Exception:
        import store as _s
    return _s


def _rows_for_months(months=None):
    """[(day, network, down, up)] from the month files."""
    return _store().daily_rows(months)


def _months_in_year(year):
    s = _store()
    return [m for m in s.available_months() if m.startswith(str(year))]


def month_breakdown(con, month):
    """List of (network, down, up) for a YYYY-MM month, busiest first."""
    agg = {}
    for _d, net, dn, up in _store().month_daily(month):
        c = agg.get(net, (0, 0))
        agg[net] = (c[0] + dn, c[1] + up)
    out = [(n, d, u) for n, (d, u) in agg.items()]
    out.sort(key=lambda r: r[1] + r[2], reverse=True)
    return out


def day_total(con, day):
    """(down, up) totals for a YYYY-MM-DD day, excluding user-excluded networks."""
    ex = excluded_fingerprints()
    d = u = 0
    for dy, net, dn, up in _store().month_daily(day[:7]):
        if dy != day or net in ex:
            continue
        d += dn or 0
        u += up or 0
    return (d, u)


def refresh_view(con):
    """No-op retained for call-site compatibility.

    The old design kept one long-lived reader connection on a WAL database that
    a background thread was writing to, which could pin the reader to a stale
    snapshot; a commit() before each hot read refreshed it. The month files are
    now opened per read and the totals are pre-aggregated, so there is no
    long-lived snapshot to go stale."""
    try:
        if con is not None:
            con.commit()
    except Exception:
        pass


def months_available(con=None):
    return _store().available_months()


def years_available(con=None):
    return _store().available_years()


def year_breakdown(con, year):
    """(network, down, up) for a whole year, busiest first."""
    agg = {}
    for _d, net, dn, up in _rows_for_months(_months_in_year(year)):
        c = agg.get(net, (0, 0))
        agg[net] = (c[0] + dn, c[1] + up)
    out = [(n, d, u) for n, (d, u) in agg.items()]
    out.sort(key=lambda r: r[1] + r[2], reverse=True)
    return out


def all_time_total():
    """(down, up, first_day, months_count) across all recorded history,
    exclusions applied — the same rule range_total()/year_months() use.

    Scans every month file once. Cheap by this store's own design (tens of
    rows per month, not the old per-second design), but still something to
    call ON DEMAND, not on the ~1s live refresh tick — callers should cache
    the result rather than recomputing it every time the panel redraws.
    """
    ex = excluded_fingerprints()
    d = u = 0
    first_day = None
    months_seen = set()
    for day, net, dn, up in _rows_for_months():
        if net in ex:
            continue
        d += dn or 0
        u += up or 0
        if dn or up:
            months_seen.add(day[:7])
            if first_day is None or day < first_day:
                first_day = day
    return (d, u, first_day, len(months_seen))


def all_breakdown(con=None):
    """(network, down, up) across all history, busiest first."""
    agg = {}
    for _d, net, dn, up in _rows_for_months():
        c = agg.get(net, (0, 0))
        agg[net] = (c[0] + dn, c[1] + up)
    out = [(n, d, u) for n, (d, u) in agg.items()]
    out.sort(key=lambda r: r[1] + r[2], reverse=True)
    return out


def month_daily_rows(con, month):
    """Per-day, per-network rows for a month: (day, network, down, up)."""
    rows = list(_store().month_daily(month))
    rows.sort(key=lambda r: (r[0], -(r[2] + r[3])))
    return rows


def daily_totals(con, month):
    """{day_number: (down, up)} for every day in a YYYY-MM that has data."""
    ex = excluded_fingerprints()
    out = {}
    for day, net, dn, up in _store().month_daily(month):
        if net in ex:
            continue
        n = int(day[8:10])
        c = out.get(n, (0, 0))
        out[n] = (c[0] + dn, c[1] + up)
    return out


def display_network(netkey):
    """Turn a stored network key into a display name. New data stores the
    fingerprint (e.g. 'gw:50:2b:...'); resolve it by the priority rule. Old
    data stored a plain label (e.g. 'Wi-Fi: Home' or 'Wi-Fi') — but the user
    may have given it a custom name too, so honour that first."""
    if not netkey:
        return "Network"
    if netkey.startswith("gw:") or netkey.startswith("ip:"):
        return resolve_network_name(netkey)
    # Legacy label key. Check for a user custom name attached to this key.
    _s, data, nets, counter = _load_networks()
    entry = nets.get(netkey)
    if entry and entry.get("name"):
        return entry["name"]
    # No custom name. If it embeds an SSID ("Wi-Fi: Name") and names are off
    # now, generalise it so we don't leak a name the user later chose to hide.
    if netkey.startswith("Wi-Fi: ") and not _names_enabled():
        return "Wi-Fi"
    return netkey


def _exclude_clause(prefix="AND"):
    """Return an SQL fragment + params tuple that excludes user-excluded
    networks from a query, e.g. ('AND network NOT IN (?,?)', (fp1, fp2)).
    Returns ('', ()) when nothing is excluded."""
    ex = excluded_fingerprints()
    if not ex:
        return "", ()
    marks = ",".join("?" for _ in ex)
    return f"{prefix} network NOT IN ({marks})", tuple(ex)


def _resolve_rows(rows):
    """Given [(network, down, up), ...] with fingerprint keys, resolve names
    and RE-AGGREGATE (several fingerprints/labels can map to one display name,
    e.g. all renamed 'Home'). Returns [(display, down, up), ...] busiest first."""
    agg = {}
    for netkey, d, u in rows:
        name = display_network(netkey)
        cur = agg.get(name, (0, 0))
        agg[name] = (cur[0] + (d or 0), cur[1] + (u or 0))
    out = [(n, d, u) for n, (d, u) in agg.items()]
    out.sort(key=lambda r: (r[1] + r[2]), reverse=True)
    return out





def _range_rows(start_day, end_day):
    s = _store()
    months = sorted({start_day[:7], end_day[:7]} |
                    {m for m in s.available_months()
                     if start_day[:7] <= m <= end_day[:7]})
    for day, net, dn, up in s.daily_rows(months):
        if start_day <= day <= end_day:
            yield day, net, dn, up


def range_daily(con, start_day, end_day):
    """{YYYY-MM-DD: (down, up)} per day in [start_day, end_day], exclusions applied."""
    ex = excluded_fingerprints()
    out = {}
    for day, net, dn, up in _range_rows(start_day, end_day):
        if net in ex:
            continue
        c = out.get(day, (0, 0))
        out[day] = (c[0] + dn, c[1] + up)
    return out


def range_total(con, start_day, end_day):
    """(down, up) across a day range, exclusions applied."""
    ex = excluded_fingerprints()
    d = u = 0
    for _day, net, dn, up in _range_rows(start_day, end_day):
        if net in ex:
            continue
        d += dn or 0
        u += up or 0
    return (d, u)


def range_network(con, start_day, end_day):
    """(network, down, up) across a day range, busiest first (raw keys)."""
    agg = {}
    for _day, net, dn, up in _range_rows(start_day, end_day):
        c = agg.get(net, (0, 0))
        agg[net] = (c[0] + dn, c[1] + up)
    out = [(n, d, u) for n, (d, u) in agg.items()]
    out.sort(key=lambda r: r[1] + r[2], reverse=True)
    return out


def year_months(con, year):
    """{month_number: (down, up)} for a year, exclusions applied."""
    ex = excluded_fingerprints()
    out = {}
    for day, net, dn, up in _rows_for_months(_months_in_year(year)):
        if net in ex:
            continue
        m = int(day[5:7])
        c = out.get(m, (0, 0))
        out[m] = (c[0] + dn, c[1] + up)
    return out


def recent_speed(con, seconds=8):
    """(down_bps, up_bps) from the raw live store — the ONLY per-second read."""
    try:
        return _store().recent_speed(con, seconds=seconds)
    except Exception:
        return (0.0, 0.0)


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
            self._own = _store().connect_live()
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
        # Identify the network by its stable fingerprint (gateway MAC). Store
        # the fingerprint as the network key so renaming later relabels ALL of
        # this network's history. This must NEVER break recording — wrap it so a
        # fingerprint/naming failure falls back gracefully instead of losing data
        # or flip-flopping the key (which used to create a stray 'Wi-Fi' entry
        # at startup before the ARP cache warmed up).
        try:
            fp = network_fingerprint()
            ssid = _wifi_ssid() if _names_enabled() else None
            if fp:
                resolve_network_name(fp, ssid)   # registers + remembers SSID
                netkey = fp
                self._last_fp = fp               # remember for transient misses
            elif getattr(self, "_last_fp", None):
                # Fingerprint momentarily unavailable — reuse the last good one
                # so we don't tag this sample with a throwaway 'Wi-Fi' label.
                netkey = self._last_fp
            else:
                netkey = network_label(iface, allow_compute=False)
        except Exception:
            netkey = getattr(self, "_last_fp", None) or (
                network_label(iface, allow_compute=False) if iface else "Network")
        result = None
        if iface in self._last:
            pin, pout = self._last[iface]
            d_down = cin - pin if cin >= pin else 0
            d_up = cout - pout if cout >= pout else 0
            if d_down or d_up:
                st = _store()
                # 1. raw row -> live.db, read only by the live-speed window
                try:
                    st.record_live(self._con(), netkey, iface, d_down, d_up)
                except Exception:
                    pass
                # 2. add onto TODAY's running total in this month's file. This is
                #    the whole point of the redesign: the number is computed as
                #    the data arrives, so nothing ever re-adds a million rows.
                try:
                    st.add_daily(st.today(), netkey, d_down, d_up)
                except Exception:
                    pass
                self._ticks = getattr(self, "_ticks", 0) + 1
                # Housekeeping on a slow cadence (~ every 30 min at 1s ticks):
                # seal any finished month and prune raw rows already rolled up.
                if self._ticks % 1800 == 0:
                    try:
                        st.seal_old_months()
                        st.prune_live(self._con())
                    except Exception:
                        pass
            result = (netkey, d_down, d_up)
        self._last[iface] = (cin, cout)
        return result
