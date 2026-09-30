#!/usr/bin/env python3
"""
diagnose_apps.py — is the per-app sampler actually working?

Runs the SAME AppSampler the v2 app uses, in isolation, and prints what it
finds. This separates three questions that the full app tangles together:
  1. Does nettop run and return per-process data on your Mac?
  2. Does the AppSampler compute non-zero deltas between beats?
  3. Does it store them and read them back from the v2 database?

If this prints real apps, the ENGINE works and any "nothing shows" problem is
in the UI layer. If this prints nothing, the problem is here and the output
tells us exactly where.

HOW TO RUN (from the OUTER NetMonitor folder, the one with run.py):
    NUM_PROFILE=v2 python3 diagnose_apps.py

It takes about 75 seconds (needs a few sampler beats). Use your Mac / browse
while it runs so there's traffic to attribute. Nothing is uploaded anywhere.
"""

import os
import sys
import time

# Force the v2 profile so we read/write the SAME database the v2 app uses.
os.environ.setdefault("NUM_PROFILE", "v2")

# Import the package the same way the app does.
try:
    from netmonitor import core, apps
except Exception as e:
    print("!! Could not import the netmonitor package.")
    print("   Make sure you run this from the OUTER NetMonitor folder")
    print("   (the one containing run.py and the netmonitor/ subfolder).")
    print("   Error:", e)
    sys.exit(1)


def show_raw_nettop_once():
    print("-" * 60)
    print("STEP 1: does nettop return per-process data at all?")
    print("-" * 60)
    data = apps._read_nettop()
    if not data:
        print("  !! nettop returned NOTHING. Either nettop isn't available")
        print("     or the flags failed. This is the root problem.")
        return False
    print(f"  OK — nettop reported {len(data)} processes. Sample:")
    for i, (name, (bin_, bout)) in enumerate(sorted(
            data.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))):
        if i >= 5:
            break
        print(f"     {name[:28]:28} in={bin_:>12} out={bout:>12}")
    return True


def run_sampler():
    print()
    print("-" * 60)
    print("STEP 2 & 3: run the AppSampler for a few beats and store deltas")
    print("-" * 60)
    print(f"  Data dir: {core.DATA_DIR}")
    print("  Sampling every 15s for ~75s. Browse something to make traffic.")
    print()

    sampler = apps.AppSampler()
    beats = 5
    interval = 15
    for b in range(1, beats + 1):
        moved = sampler.sample()
        print(f"  beat {b}/{beats}: {moved} processes moved data "
              f"{'(baseline — first beat is always 0)' if b == 1 else ''}")
        if b < beats:
            time.sleep(interval)

    print()
    print("-" * 60)
    print("STEP 4: read it back from the database (what the UI would show)")
    print("-" * 60)
    con = core.connect()
    apps.ensure_schema(con)
    from datetime import datetime
    mon = datetime.now().strftime("%Y-%m")
    payload = apps.app_dashboard_payload(con, "month", mon, top=15)
    rows = payload["apps"]
    if not rows:
        print("  !! Database has NO per-app rows for this month.")
        print("     The sampler ran but stored nothing — deltas were all zero,")
        print("     or the write failed. Send me this whole output.")
        return
    print(f"  OK — {len(rows)} apps stored this month:\n")
    print(f"  {'APP':22}{'DOWN':>12}{'UP':>12}")
    print("  " + "-" * 46)
    for a in rows:
        print(f"  {a['friendly'][:22]:22}{core.human(a['down']):>12}"
              f"{core.human(a['up']):>12}")
    print()
    print("  >>> If you see apps above, the ENGINE WORKS and the bug is in the")
    print("      UI/panel layer. If this is empty, the bug is in the sampler.")


if __name__ == "__main__":
    print("=" * 60)
    print("  NUM v2 per-app sampler diagnostic")
    print("=" * 60)
    ok = show_raw_nettop_once()
    if ok:
        run_sampler()
    print()
    print("Done. Copy ALL of this output back to continue.")
