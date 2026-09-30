#!/usr/bin/env python3
"""
network_fingerprint_probe.py — what can we read about the current network
WITHOUT Location permission?

The question: if a user won't grant Location (so we can't read the Wi-Fi NAME),
can we still tell one network apart from another when they reconnect? If we can
find any stable per-network fingerprint, then "rename your networks" can work —
the app ties the user's chosen name to that fingerprint and re-applies it
automatically on reconnect.

This probe tries several identifiers that historically DON'T need Location, and
prints what it finds. Run it on one network, then (ideally) on a different
network, and compare — we're looking for something that:
  (a) is readable here without a permission prompt, and
  (b) CHANGES between different networks (so it can distinguish them), and
  (c) STAYS THE SAME on the same network across reconnects.

Run:
    python3 network_fingerprint_probe.py

Nothing is uploaded. This only reads local network config.
"""

import subprocess
import re


def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True,
                                       stderr=subprocess.DEVNULL, timeout=8)
    except Exception as e:
        return f"(error: {e})"


def section(title):
    print("\n" + "=" * 64)
    print("  " + title)
    print("=" * 64)


def active_interface():
    out = run(["route", "get", "default"])
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if isinstance(out, str) else None


def main():
    print("NETWORK FINGERPRINT PROBE — what's readable without Location?")
    iface = active_interface()
    print(f"\nActive interface: {iface}")

    # 1. The default gateway IP (the router). Same router = same IP usually,
    #    but home routers commonly all use 192.168.1.1, so this ALONE is weak.
    section("1. Default gateway IP (router address)")
    out = run(["route", "get", "default"])
    if isinstance(out, str):
        m = re.search(r"gateway:\s*(\S+)", out)
        print("  gateway:", m.group(1) if m else "(not found)")

    # 2. The gateway's MAC address (via ARP). THIS is the interesting one —
    #    the router's hardware MAC is unique per physical router, and reading
    #    the ARP table does NOT require Location permission. If this is present
    #    and differs between networks, it's our fingerprint.
    section("2. Gateway MAC address (ARP) — the promising one")
    gw = None
    if isinstance(out, str):
        m = re.search(r"gateway:\s*(\S+)", out)
        gw = m.group(1) if m else None
    if gw:
        arp = run(["arp", "-n", gw])
        print("  arp entry:", arp.strip() if isinstance(arp, str) else arp)
        if isinstance(arp, str):
            mac = re.search(r"([0-9a-fA-F]{1,2}(:[0-9a-fA-F]{1,2}){5})", arp)
            print("  >>> gateway MAC:", mac.group(1) if mac else "(not parsed)")
            print("      If this is a real MAC and differs per network, we can")
            print("      fingerprint networks with it — no Location needed.")
    else:
        print("  (no gateway found to look up)")

    # 3. SSID via networksetup (the OLD way). On macOS 26 this is usually
    #    redacted/empty WITHOUT Location — we check so we can confirm it's blocked.
    section("3. SSID via networksetup (expected BLOCKED without Location)")
    if iface:
        ns = run(["networksetup", "-getairportnetwork", iface])
        print(" ", ns.strip() if isinstance(ns, str) else ns)

    # 4. IPv6 router / DHCP info that might carry a stable id.
    section("4. Interface details (ipconfig)")
    if iface:
        info = run(["ipconfig", "getsummary", iface])
        if isinstance(info, str):
            # print only a few potentially-useful lines
            for line in info.splitlines():
                if any(k in line for k in ("SSID", "BSSID", "Router",
                                           "ServerIdentifier", "SubnetMask")):
                    print("  " + line.strip())

    section("WHAT TO DO WITH THIS")
    print("""  Run this on your HOME network and note the gateway MAC (section 2).
  Then connect to a DIFFERENT network (phone hotspot, office) and run again.
  Compare the gateway MACs:
    - Different MAC per network  -> we CAN fingerprint & auto-rename. 
    - Same/empty/blocked         -> we can't distinguish without Location.
  Paste both runs back.""")


if __name__ == "__main__":
    main()
