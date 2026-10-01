#!/usr/bin/env python3
"""NUM test snapshot -- read-only. Compares three layers between runs:
  1. macOS's own interface counters (netstat)  -> ground truth
  2. NUM's recorded Day total (daily_usage)
  3. NUM's recorded per-app totals (app_usage)

Usage:  python3 num_snap.py "label"        e.g. "before chrome"
Run it before and after each test; it prints what changed since the last run.
Never writes to NUM's database (opened read-only).
"""
import json, os, sqlite3, subprocess, sys, time
from datetime import date

DATA = os.path.expanduser("~/.netmonitor-v2/months")
STATE = "/tmp/num_snap_last.json"
IFACES = ("en0", "awdl0")   # awdl0 = AirDrop's peer-to-peer link


def netstat():
    out = subprocess.run(["netstat", "-ibn"], capture_output=True, text=True).stdout
    res = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 10 and p[0] in IFACES and p[2].startswith("<Link"):
            # Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes
            res[p[0]] = [int(p[6]), int(p[9])]
    return res


def num_db():
    path = os.path.join(DATA, date.today().strftime("%Y-%m") + ".db")
    if not os.path.exists(path):
        return {"day": [0, 0], "apps": {}}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    today = date.today().isoformat()
    d = con.execute("SELECT COALESCE(SUM(down),0), COALESCE(SUM(up),0) "
                    "FROM daily_usage WHERE day=?", (today,)).fetchone()
    apps = {a: [dn, up] for a, dn, up in con.execute(
        "SELECT app, SUM(down), SUM(up) FROM app_usage WHERE day=? GROUP BY app",
        (today,))}
    con.close()
    return {"day": list(d), "apps": apps}


def mb(n):
    return f"{n / 1024**2:10.1f} MB"


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    now = {"t": time.time(), "label": label, "net": netstat(), **num_db()}
    prev = None
    if os.path.exists(STATE):
        with open(STATE) as f:
            prev = json.load(f)
    with open(STATE, "w") as f:
        json.dump(now, f)

    print(f"\n=== {label}  ({time.strftime('%H:%M:%S')}) ===")
    if not prev:
        print("Baseline saved. Run again after the test to see the changes.")
        return
    secs = now["t"] - prev["t"]
    print(f"Since '{prev['label']}' ({secs/60:.1f} min ago)\n")
    print(f"{'':28}{'DOWN':>13}{'UP':>13}")
    for i in IFACES:
        a, b = now["net"].get(i, [0, 0]), prev["net"].get(i, [0, 0])
        print(f"{'macOS ' + i:28}{mb(a[0]-b[0])}{mb(a[1]-b[1])}")
    dd = [now["day"][k] - prev["day"][k] for k in (0, 1)]
    print(f"{'NUM Day total':28}{mb(dd[0])}{mb(dd[1])}")

    deltas, tot = [], [0, 0]
    for app, (dn, up) in now["apps"].items():
        p = prev["apps"].get(app, [0, 0])
        x = [dn - p[0], up - p[1]]
        tot = [tot[0] + x[0], tot[1] + x[1]]
        if x[0] + x[1] > 0:
            deltas.append((app, x))
    print(f"{'NUM all apps':28}{mb(tot[0])}{mb(tot[1])}")
    print("\nTop apps in this window:")
    for app, x in sorted(deltas, key=lambda r: -(r[1][0] + r[1][1]))[:8]:
        print(f"  {app:26}{mb(x[0])}{mb(x[1])}")


if __name__ == "__main__":
    main()
