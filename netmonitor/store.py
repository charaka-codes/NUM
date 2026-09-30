"""
store.py — split-by-month storage for NUM.

Replaces the single ever-growing usage.db. One rule underpins the whole design:

    A byte is counted ONCE, when it arrives. After that we only read the count.

The old design kept every per-second reading forever and re-added all of them
on every UI refresh — 1.1M rows scanned once a second to produce ~30 numbers,
which is why the app got slower every week. Here the sampler adds each delta
onto today's running total as it arrives, so the totals are already computed by
the time anything reads them.

Layout (inside DATA_DIR, e.g. ~/.netmonitor-v2/):

    live.db              raw per-second rows — LIVE SPEED ONLY, pruned after 2 days
    months/2026-08.db    sealed: a finished month, never written again
    months/2026-09.db    the current month, still accumulating
    settings.json        unchanged (network registry lives there)

The dividing line is TODAY. Today is live; every earlier day is a finished
number that can never change, so it is read and never recomputed.

Networks are stored by FINGERPRINT (gateway MAC), never by display name, so
renaming a network relabels all of its history with no migration. Exclusion and
name resolution happen in Python at read time — the row counts are tiny now
(tens, not millions), so there is no reason to push that into SQL.
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Reuse core's profile-aware data dir so v1/v2 stay isolated.
try:
    from .core import DATA_DIR
except Exception:  # pragma: no cover - standalone use
    try:
        from core import DATA_DIR
    except Exception:
        _P = os.environ.get("NUM_PROFILE", "v2").strip()
        DATA_DIR = Path.home() / (".netmonitor" if _P == "v1" else f".netmonitor-{_P}")

LIVE_PATH = DATA_DIR / "live.db"
MONTHS_DIR = DATA_DIR / "months"
LEGACY_DB = DATA_DIR / "usage.db"

SCHEMA_VERSION = "1"
LIVE_KEEP_DAYS = 2          # raw rows older than this are pruned

_LOCK = threading.RLock()
_MONTH_CONS = {}            # month -> connection (write side, current month)
_SEALED_CACHE = {}          # month -> dict payload, cached forever (immutable)


# ---------------------------------------------------------------- helpers

def current_month():
    return datetime.now().strftime("%Y-%m")


def today():
    return datetime.now().strftime("%Y-%m-%d")


def month_of(day):
    """'2026-08-17' -> '2026-08'."""
    return day[:7]


def month_path(month):
    return MONTHS_DIR / f"{month}.db"


def available_months():
    """Months that have a file on disk, newest first."""
    try:
        MONTHS_DIR.mkdir(parents=True, exist_ok=True)
        out = [p.stem for p in MONTHS_DIR.glob("*.db")
               if len(p.stem) == 7 and p.stem[4] == "-"]
        return sorted(out, reverse=True)
    except Exception:
        return []


def available_years():
    return sorted({m[:4] for m in available_months()}, reverse=True)


# ---------------------------------------------------------------- live db

LIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage(
  ts        TEXT NOT NULL,
  network   TEXT NOT NULL,
  interface TEXT NOT NULL,
  down      INTEGER NOT NULL,
  up        INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_ts ON usage(ts);
"""


def connect_live():
    """Connection to the raw per-second store. Small by design."""
    LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LIVE_PATH, check_same_thread=False, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(LIVE_SCHEMA)
    con.commit()
    return con


def record_live(con, network, interface, down, up):
    """Append one raw sample. Only the live-speed readout ever reads these."""
    con.execute("INSERT INTO usage VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"),
                 network, interface, down, up))
    con.commit()


def recent_speed(con, seconds=8):
    """(down_bps, up_bps) over the last N seconds of raw rows."""
    cutoff = (datetime.now() - timedelta(seconds=seconds)).isoformat(
        timespec="seconds")
    row = con.execute(
        "SELECT COALESCE(SUM(down),0), COALESCE(SUM(up),0) FROM usage "
        "WHERE ts >= ?", (cutoff,)).fetchone()
    d, u = row or (0, 0)
    return (d / float(seconds), u / float(seconds))


def prune_live(con, keep_days=LIVE_KEEP_DAYS):
    """Drop raw rows already folded into a month file. Keeps live.db flat."""
    try:
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat(
            timespec="seconds")
        con.execute("DELETE FROM usage WHERE ts < ?", (cutoff,))
        con.commit()
    except Exception:
        pass


# ---------------------------------------------------------------- month db

MONTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_usage(
  day     TEXT NOT NULL,
  network TEXT NOT NULL,
  down    INTEGER NOT NULL DEFAULT 0,
  up      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, network));
CREATE TABLE IF NOT EXISTS app_usage(
  day     TEXT NOT NULL,
  network TEXT NOT NULL,
  app     TEXT NOT NULL,
  down    INTEGER NOT NULL DEFAULT 0,
  up      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, network, app));
CREATE TABLE IF NOT EXISTS app_snapshot(
  app     TEXT NOT NULL PRIMARY KEY,
  down    INTEGER NOT NULL DEFAULT 0,
  up      INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_du_day ON daily_usage(day);
CREATE INDEX IF NOT EXISTS idx_au_day ON app_usage(day);
CREATE INDEX IF NOT EXISTS idx_au_app ON app_usage(app);
"""


def _open_month(month, create=True):
    path = month_path(month)
    if not create and not path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(MONTH_SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES ('schema_version',?)",
                (SCHEMA_VERSION,))
    con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES ('month',?)", (month,))
    con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES ('sealed','0')")
    con.commit()
    return con


def month_con(month, create=True):
    """Cached write connection for a month file (used by the samplers)."""
    with _LOCK:
        con = _MONTH_CONS.get(month)
        if con is None:
            con = _open_month(month, create=create)
            if con is not None:
                _MONTH_CONS[month] = con
        return con


def seal_old_months(keep=None):
    """Mark every month before `keep` (default: current) as finished. A sealed
    month is never written again, so readers may cache it forever."""
    keep = keep or current_month()
    for m in available_months():
        if m >= keep:
            continue
        try:
            con = month_con(m, create=False)
            if con is None:
                continue
            cur = con.execute(
                "SELECT value FROM meta WHERE key='sealed'").fetchone()
            if not cur or cur[0] != "1":
                con.execute(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES ('sealed','1')")
                con.commit()
                collapse_month(m)      # finished month -> one row per app
        except Exception:
            pass


def collapse_month(month):
    """Collapse a finished month's per-day app rows into ONE row per app.

    A finished month's totals can never change, and nothing in the UI shows
    per-app detail finer than a month — the Apps tab only navigates by month. So
    keeping a row per (day, network, app) is storage with no reader: August held
    898 rows to answer a question asked at the level of ~30.

    This sums each app across the month into app_snapshot and drops the daily
    rows. Interface data (daily_usage) is untouched — the Day view needs it.
    """
    con = month_con(month, create=False)
    if con is None:
        return 0
    try:
        done = con.execute(
            "SELECT value FROM meta WHERE key='apps_collapsed'").fetchone()
        if done and done[0] == "1":
            return 0
        rows = con.execute(
            "SELECT app, SUM(down), SUM(up) FROM app_usage GROUP BY app").fetchall()
        if rows:
            con.executemany(
                "INSERT INTO app_snapshot(app,down,up) VALUES (?,?,?) "
                "ON CONFLICT(app) DO UPDATE SET down=excluded.down, up=excluded.up",
                rows)
            con.execute("DELETE FROM app_usage")
        con.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES ('apps_collapsed','1')")
        con.commit()
        _invalidate_month_cache(month)
        return len(rows)
    except Exception:
        return 0


def month_app_totals(month):
    """[(app, down, up)] for a month — from the snapshot if collapsed, else
    summed live from the daily rows. Callers need not know which."""
    con = month_con(month, create=False)
    if con is None:
        return []
    try:
        snap = con.execute(
            "SELECT app, down, up FROM app_snapshot ORDER BY down+up DESC").fetchall()
        if snap:
            return snap
    except Exception:
        pass
    try:
        return con.execute(
            "SELECT app, SUM(down), SUM(up) FROM app_usage GROUP BY app "
            "ORDER BY SUM(down)+SUM(up) DESC").fetchall()
    except Exception:
        return []


def is_sealed(month):
    try:
        con = month_con(month, create=False)
        if con is None:
            return False
        r = con.execute("SELECT value FROM meta WHERE key='sealed'").fetchone()
        return bool(r and r[0] == "1")
    except Exception:
        return False


# ---------------------------------------------------------------- writes

def add_daily(day, network, down, up):
    """Add an interface delta onto today's running total (UPSERT)."""
    if not (down or up):
        return
    con = month_con(month_of(day))
    if con is None:
        return
    con.execute("""
        INSERT INTO daily_usage(day,network,down,up) VALUES (?,?,?,?)
        ON CONFLICT(day,network) DO UPDATE SET
          down = down + excluded.down, up = up + excluded.up
    """, (day, network, down, up))
    con.commit()


def add_app(day, network, app, down, up):
    """Add a per-app delta onto today's running total (UPSERT)."""
    if not (down or up):
        return
    con = month_con(month_of(day))
    if con is None:
        return
    con.execute("""
        INSERT INTO app_usage(day,network,app,down,up) VALUES (?,?,?,?,?)
        ON CONFLICT(day,network,app) DO UPDATE SET
          down = down + excluded.down, up = up + excluded.up
    """, (day, network, app, down, up))
    con.commit()


def _invalidate_month_cache(month=None):
    with _LOCK:
        if month is None:
            _SEALED_CACHE.clear()
        else:
            _SEALED_CACHE.pop(month, None)


# ---------------------------------------------------------------- reads
#
# Everything below returns FINGERPRINT-keyed rows. Name resolution and
# exclusion happen in the caller (core), where the registry lives.

def month_daily(month):
    """[(day, network, down, up)] for one month. Sealed months are cached."""
    with _LOCK:
        if month in _SEALED_CACHE:
            return _SEALED_CACHE[month]["daily"]
    con = month_con(month, create=False)
    if con is None:
        return []
    rows = con.execute(
        "SELECT day, network, down, up FROM daily_usage ORDER BY day").fetchall()
    if is_sealed(month):
        with _LOCK:
            entry = _SEALED_CACHE.setdefault(month, {})
            entry["daily"] = rows
    return rows


def month_apps(month):
    """[(day, network, app, down, up)] for one month. Sealed months cached."""
    with _LOCK:
        cached = _SEALED_CACHE.get(month, {}).get("apps")
        if cached is not None:
            return cached
    con = month_con(month, create=False)
    if con is None:
        return []
    rows = con.execute(
        "SELECT day, network, app, down, up FROM app_usage").fetchall()
    if is_sealed(month):
        with _LOCK:
            entry = _SEALED_CACHE.setdefault(month, {})
            entry["apps"] = rows
    return rows


def daily_rows(months=None):
    """[(day, network, down, up)] across the given months (default: all)."""
    out = []
    for m in (months if months is not None else available_months()):
        out.extend(month_daily(m))
    return out


def year_daily(year):
    return daily_rows([m for m in available_months() if m.startswith(str(year))])


def days_with_data():
    return sorted({d for d, _n, _dn, _u in daily_rows()})


# ---------------------------------------------------------------- migration

def needs_migration():
    """True if the old single-file DB exists and hasn't been converted yet."""
    try:
        return LEGACY_DB.exists() and not MONTHS_DIR.exists()
    except Exception:
        return False


def migrate_legacy(legacy_path=None, progress=None):
    """
    One-time conversion of the old usage.db into month files. Lossless for
    daily totals: every per-second row is folded into its day's running total.

    Returns (months_written, days, app_rows) or None if there was nothing to do.
    """
    src_path = Path(legacy_path) if legacy_path else LEGACY_DB
    if not src_path.exists():
        return None
    try:
        src = sqlite3.connect(src_path)
        src.execute("PRAGMA journal_mode=WAL")
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

        months = [r[0] for r in src.execute(
            "SELECT DISTINCT substr(ts,1,7) FROM usage ORDER BY 1")]
        days = app_rows = 0
        for m in months:
            if progress:
                progress(m)
            con = month_con(m)
            rows = src.execute(
                "SELECT substr(ts,1,10), network, SUM(down), SUM(up) FROM usage "
                "WHERE substr(ts,1,7)=? GROUP BY 1,2", (m,)).fetchall()
            con.executemany(
                "INSERT INTO daily_usage(day,network,down,up) VALUES (?,?,?,?) "
                "ON CONFLICT(day,network) DO UPDATE SET "
                "down=daily_usage.down+excluded.down, up=daily_usage.up+excluded.up",
                rows)
            days += len(rows)
            try:
                arows = src.execute(
                    "SELECT day, network, app, SUM(down), SUM(up) FROM app_usage "
                    "WHERE substr(day,1,7)=? GROUP BY 1,2,3", (m,)).fetchall()
                con.executemany(
                    "INSERT INTO app_usage(day,network,app,down,up) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(day,network,app) DO UPDATE SET "
                    "down=app_usage.down+excluded.down, up=app_usage.up+excluded.up",
                    arows)
                app_rows += len(arows)
            except Exception:
                pass
            con.commit()

        # Seed live.db with only the tail of the raw rows (live speed needs
        # seconds, not months).
        try:
            live = connect_live()
            maxts = src.execute("SELECT MAX(ts) FROM usage").fetchone()[0]
            if maxts:
                cut = (datetime.fromisoformat(maxts) - timedelta(hours=2)
                       ).isoformat(timespec="seconds")
                tail = src.execute(
                    "SELECT ts,network,interface,down,up FROM usage WHERE ts>=?",
                    (cut,)).fetchall()
                live.executemany("INSERT INTO usage VALUES (?,?,?,?,?)", tail)
                live.commit()
        except Exception:
            pass

        src.close()
        seal_old_months()
        _invalidate_month_cache()
        # Keep the old file as a backup rather than deleting it.
        try:
            src_path.rename(src_path.with_suffix(".db.premigration"))
        except Exception:
            pass
        return (len(months), days, app_rows)
    except Exception:
        return None


def erase_all():
    """Delete every month file and clear live.db. Settings are untouched."""
    with _LOCK:
        for con in _MONTH_CONS.values():
            try:
                con.close()
            except Exception:
                pass
        _MONTH_CONS.clear()
        _SEALED_CACHE.clear()
    try:
        for p in MONTHS_DIR.glob("*.db*"):
            p.unlink()
    except Exception:
        pass
    try:
        con = connect_live()
        con.execute("DELETE FROM usage")
        con.commit()
    except Exception:
        pass


def delete_network_everywhere(fingerprint):
    """Remove one network's rows from every month file."""
    if not fingerprint:
        return
    for m in available_months():
        try:
            con = month_con(m, create=False)
            if con is None:
                continue
            con.execute("DELETE FROM daily_usage WHERE network=?", (fingerprint,))
            con.execute("DELETE FROM app_usage WHERE network=?", (fingerprint,))
            con.commit()
        except Exception:
            pass
    _invalidate_month_cache()
