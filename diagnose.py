#!/usr/bin/env python3
"""
diagnose.py — figure out why network tracking is stuck.

Run from the NetMonitor project folder:
    python3 diagnose.py

It prints, every 2 seconds:
  - the active interface
  - the raw (bytes_in, bytes_out) counters it reads
  - the delta since last reading
  - the network label (Wi-Fi name)

Use the network (open a website, download something) while it runs and watch
whether the numbers move. Ctrl-C to stop. Paste the output back.
"""

import time
import subprocess
import re


def run(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)


def active_interface():
    try:
        out = run(["route", "get", "default"])
        m = re.search(r"interface:\s*(\S+)", out)
        return m.group(1) if m else None
    except Exception as e:
        return f"ERR:{e}"


def raw_netstat_lines(interface):
    """Return the raw netstat lines for this interface, for inspection."""
    try:
        out = run(["netstat", "-ibn"])
    except Exception as e:
        return [f"ERR running netstat: {e}"]
    lines = out.splitlines()
    hdr = lines[0] if lines else ""
    matches = [l for l in lines[1:] if l.split() and l.split()[0] == interface]
    return [hdr] + matches


def counters(interface):
    try:
        out = run(["netstat", "-ibn"])
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
        shift = ncols - len(parts)
        try:
            ib = int(parts[i_ib - shift])
            ob = int(parts[i_ob - shift])
        except (ValueError, IndexError):
            continue
        if best is None or (ib + ob) > (best[0] + best[1]):
            best = (ib, ob)
    return best


def main():
    print("=== NetMonitor tracking diagnostic ===")
    print("Use the network while this runs; watch if numbers move. Ctrl-C to stop.\n")

    iface = active_interface()
    print(f"Active interface: {iface}\n")
    print("Raw netstat lines for this interface:")
    for l in raw_netstat_lines(iface):
        print("   ", l)
    print()

    last = None
    for i in range(1000):
        iface = active_interface()
        c = counters(iface)
        if c is None:
            print(f"[{i}] iface={iface}  counters=NONE (parse failed)")
        else:
            cin, cout = c
            if last is None:
                print(f"[{i}] iface={iface}  in={cin:,}  out={cout:,}  (baseline)")
            else:
                din = cin - last[0]
                dout = cout - last[1]
                flag = "  <-- MOVING" if (din or dout) else "  (no change)"
                print(f"[{i}] iface={iface}  in={cin:,}  out={cout:,}  "
                      f"Δin={din:,}  Δout={dout:,}{flag}")
            last = (cin, cout)
        time.sleep(2)


if __name__ == "__main__":
    main()
