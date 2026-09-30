#!/usr/bin/env python3
"""
clear_app_data.py — wipe ONLY per-app usage data (the Apps tab), leaving
interface totals (Day/Month tabs, the `daily_usage` table) completely
untouched.

Use this to zero out per-app tracking so its cumulative total, from this
point forward, can be compared against the (undisturbed) interface total
over the same window — a direct check for any counting drift.

IMPORTANT: quit NUM (and make sure nothing else, like sd.py, is running)
before running this. It opens each month file directly and deletes rows;
doing that while another process holds a write connection open risks a
locked database or a race with an in-flight write.

Usage (run from the project root, with the SAME NUM_PROFILE you actually use):
    NUM_PROFILE=v2 python3 clear_app_data.py                 # all months
    NUM_PROFILE=v2 python3 clear_app_data.py --current-only   # this month only
"""
import sys

from netmonitor import store as store_mod

CURRENT_ONLY = "--current-only" in sys.argv

months = ([store_mod.current_month()] if CURRENT_ONLY
          else store_mod.available_months())

print(f"Data directory : {store_mod.DATA_DIR}")
if not months:
    print("No month files found — nothing to clear.")
    sys.exit(0)

print(f"Will clear per-app data (app_usage + app_snapshot) for: "
      f"{', '.join(months)}")
print("daily_usage (interface totals / Day & Month tabs) will NOT be touched.")
confirm = input("Type 'yes' to continue (this cannot be undone): ").strip().lower()
if confirm != "yes":
    print("Aborted — nothing changed.")
    sys.exit(0)

total_app_rows = 0
total_snapshot_rows = 0
for m in months:
    con = store_mod.month_con(m, create=False)
    if con is None:
        continue
    n1 = con.execute("SELECT COUNT(*) FROM app_usage").fetchone()[0]
    n2 = con.execute("SELECT COUNT(*) FROM app_snapshot").fetchone()[0]
    con.execute("DELETE FROM app_usage")
    con.execute("DELETE FROM app_snapshot")
    con.commit()
    total_app_rows += n1
    total_snapshot_rows += n2
    print(f"  {m}: cleared {n1} app_usage row(s), {n2} app_snapshot row(s)")

print(f"\nDone. Cleared {total_app_rows} app_usage row(s) and "
      f"{total_snapshot_rows} app_snapshot row(s) across {len(months)} "
      f"month file(s).")
print("daily_usage was not touched — Day/Month totals are exactly as before.")
