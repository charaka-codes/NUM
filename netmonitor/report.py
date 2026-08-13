#!/usr/bin/env python3
"""CLI report tool. Usage: python3 -m netmonitor.report [YYYY-MM] [--all]"""
import argparse
from datetime import datetime
from . import core


def print_report(month, rows):
    print(f"\n{'='*56}\n  DATA USAGE REPORT — {month}\n{'='*56}")
    if not rows:
        print("  No data recorded.\n"); return
    print(f"  {'Network':<26}{'Download':>12}{'Upload':>12}")
    print(f"  {'-'*50}")
    td = tu = 0
    for net, d, u in rows:
        print(f"  {net:<26}{core.human(d):>12}{core.human(u):>12}"); td += d; tu += u
    print(f"  {'-'*50}")
    print(f"  {'TOTAL':<26}{core.human(td):>12}{core.human(tu):>12}")
    print(f"  {'COMBINED':<26}{core.human(td+tu):>12}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("month", nargs="?", default=datetime.now().strftime("%Y-%m"))
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    con = core.connect()
    if a.all:
        for m in core.months_available(con):
            print_report(m, core.month_breakdown(con, m))
    else:
        print_report(a.month, core.month_breakdown(con, a.month))


if __name__ == "__main__":
    main()
