#!/usr/bin/env python3
"""
check_log.py — summarize qbittorrent's DOWNLOAD-side history from a NUM
NUM_DEBUG log (num_soak.log).

Sums every per-tick raw_down/rec_down the sampler saw for qbittorrent across
the whole file, and separately lists only the ticks where raw_down !=
rec_down — i.e. REAL download-side loss (clipped or dropped), as opposed to
the DROP->0/CLIPPED verdict label, which also fires on upload-side blips
that have nothing to do with the download total.

Usage:
    python3 check_log.py ~/num_soak.log
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "num_soak.log"

pat = re.compile(
    r"\[apps\] (qbittorrent\S*)\s+raw_down=\s*([\d,]+)\s+rec_down=\s*([\d,]+)\s+"
    r"cap_down=\s*([\d,]+)\s+(\S+)"
)

total_raw = 0
total_rec = 0
diffs = []
n_lines = 0

with open(path) as f:
    for line in f:
        m = pat.search(line)
        if not m:
            continue
        n_lines += 1
        proc, raw_s, rec_s, cap_s, verdict = m.groups()
        raw = int(raw_s.replace(",", ""))
        rec = int(rec_s.replace(",", ""))
        cap = int(cap_s.replace(",", ""))
        total_raw += raw
        total_rec += rec
        if raw != rec:
            diffs.append(line.rstrip())

print(f"qbittorrent download ticks found : {n_lines}")
print(f"sum raw_down (real traffic seen) : {total_raw:,} bytes "
      f"({total_raw / 1e9:.3f} GB)")
print(f"sum rec_down (actually recorded) : {total_rec:,} bytes "
      f"({total_rec / 1e9:.3f} GB)")
if total_raw:
    print(f"captured                         : {100 * total_rec / total_raw:.2f}%")

print(f"\nticks with REAL download-side loss (raw_down != rec_down): "
      f"{len(diffs)}")
for line in diffs:
    print(f"  {line}")
if not diffs:
    print("  none — every qbittorrent download tick in this log was "
          "recorded in full.")
