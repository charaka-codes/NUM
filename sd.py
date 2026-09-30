#!/usr/bin/env python3
"""
sd.py — diagnostic for the per-app under-counting bug.

Drives the REAL netmonitor.apps.AppSampler.sample() directly, exactly as
app.py's _app_sample_loop does. Every number printed here comes straight out
of AppSampler.last_tick / .last_vanished — nothing is recomputed by hand, so
this script can't drift from what the shipped sampler actually decided (that
drift, comparing b >= p against a stored TUPLE, is what crashed the first
version of this script).

Usage:
    python3 sd.py [seconds] [proc_prefix]

    seconds       total run time (default 120)
    proc_prefix   process name prefix to watch (default "qbittorrent")

Run this from the project root (the folder containing the `netmonitor`
package), with the watched app actively transferring data.
"""
import os
import sys
import threading
import time

os.environ.setdefault("NUM_DEBUG", "1")

from netmonitor import apps as apps_mod
from netmonitor import core as core_mod

RUN_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
WATCH = (sys.argv[2] if len(sys.argv) > 2 else "qbittorrent").lower()
READER_INTERVAL = 10  # matches settings.py's app_reader_interval default


def main():
    # The cap in AppSampler._interface_delta() reads real interface totals
    # from live.db — which is normally only written by core.Sampler inside
    # the running app (app.py's _sample_loop). Without that, the cap starves
    # to its 8MB floor and clips almost everything. Run the same 1s sampler
    # here so this script needs nothing else open. Don't run NUM.app at the
    # same time as this — two Sampler instances would double-write the real
    # daily interface totals.
    iface_sampler = core_mod.Sampler()
    iface_running = {"go": True}

    def _iface_loop():
        while iface_running["go"]:
            try:
                iface_sampler.sample()
            except Exception:
                pass
            time.sleep(1)

    iface_thread = threading.Thread(target=_iface_loop, daemon=True)
    iface_thread.start()
    print("Started an internal 1s interface sampler (for the cap) — "
          "don't run NUM.app at the same time as this script.")
    time.sleep(3)  # let a few real interface ticks land before the first cap read

    sampler = apps_mod.AppSampler(interval=READER_INTERVAL)
    sampler.start()

    print("Waiting for first nettop report (this can take a while under "
          "heavy connection load — production never gives up here either)...")
    WAIT_BUDGET = 180  # seconds — several full retry cycles, not one
    waited = 0
    while waited < WAIT_BUDGET:
        if sampler.reader_ready():
            break
        time.sleep(1)
        waited += 1
        if waited % 20 == 0:
            print(f"  ...still waiting ({waited}s so far, "
                  f"{sampler._reader.errors_seen()} timeouts so far)")
    else:
        print(f"nettop never reported in {WAIT_BUDGET}s — that's unusually "
              f"slow even for heavy load. Try `nettop -P -x -s 1 -l 1` on its "
              f"own right now and see how long it takes.")
        sampler.stop()
        iface_running["go"] = False
        return

    print(f"watching processes starting with {WATCH!r} for {RUN_SECONDS}s "
          f"(sampling every {READER_INTERVAL}s)...\n")
    print(f"{'t':>5} {'pid':<28} {'counter_down':>15} {'raw_down':>12} "
          f"{'rec_down':>12} {'cap_down':>12} {'verdict':>10}")

    tot_raw = 0
    tot_recorded = 0
    verdict_counts = {}

    t0 = time.time()
    tick = 0
    while time.time() - t0 < RUN_SECONDS:
        time.sleep(READER_INTERVAL)
        tick += READER_INTERVAL

        sampler.sample()  # <-- the exact call app.py makes every beat

        for pid, info in sampler.last_tick.items():
            if not pid.lower().startswith(WATCH):
                continue
            verdict_counts[info["verdict"]] = verdict_counts.get(info["verdict"], 0) + 1
            tot_raw += info["raw_down"]
            tot_recorded += info["recorded_down"]
            print(f"{tick:>5} {pid:<28} {info['counter_down']:>15,} "
                  f"{info['raw_down']:>12,} {info['recorded_down']:>12,} "
                  f"{(info['cap_down'] or 0):>12,} {info['verdict']:>10}")

        for pid in sampler.last_vanished:
            if pid.lower().startswith(WATCH):
                print(f"{tick:>5} {pid:<28} {'VANISHED':>15} "
                      f"{'—':>12} {'—':>12} {'—':>12} {'GONE':>10}")
                verdict_counts["GONE"] = verdict_counts.get("GONE", 0) + 1

    sampler.stop()
    iface_running["go"] = False

    print("\n--- summary ---")
    for v, c in sorted(verdict_counts.items()):
        print(f"  {v:<10} {c}")
    print(f"\nraw movement (down) : {tot_raw / 1e9:.3f} GB")
    print(f"recorded (down)     : {tot_recorded / 1e9:.3f} GB")
    if tot_raw:
        print(f"captured            : {100 * tot_recorded / tot_raw:.1f}%")


if __name__ == "__main__":
    main()
