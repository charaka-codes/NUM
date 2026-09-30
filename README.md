# NUM — Network Usage Monitor

A lightweight macOS **menu bar app** that tracks how much data you download and
upload, and attributes it to the network you're on — home Wi-Fi, office Wi-Fi,
mobile hotspot, ethernet — so you get a clear per-network breakdown with daily,
monthly, and yearly totals.

A single click on the menu bar icon opens a translucent glass panel with live
speeds, a calendar, totals, and per-network usage. Everything stays on your
Mac: only usage totals are stored locally (`~/.netmonitor/usage.db`). No packet
contents, no browsing history, nothing leaves your machine.

![NUM panel](docs/screenshot.png)

## Features

- **Menu bar at a glance** — today's total data usage next to the icon (which
  you can hide for an icon-only look)
- **One-click glass panel** — live download/upload speed, a Day/Year
  calendar, the selected period's total, and a per-network breakdown
- **Real network names** — shows your actual Wi-Fi SSID per network, so you can
  tell home from office from hotspot (needs Location permission — see below)
- **Day & Year views** — see usage per day, or per month across a year
- **Flexible CSV export** — export any single month, a whole year, or your
  entire history to `~/Downloads`
- **Customisable** — pick any download/upload colours (full colour wheel),
  adjust panel transparency, all persisted between launches
- **Launch at login** — optional toggle to start NUM automatically
- **Pause anytime** — a tracking toggle stops/resumes recording
- Native AppKit, no Dock icon, minimal footprint

## Requirements

- macOS 13 or newer (built and tested on macOS 26)
- Python 3 from [python.org](https://www.python.org/downloads/macos/)
  (recommended over the system Python — it ships prebuilt wheels)
- Xcode command line tools for the build toolchain:
  ```bash
  xcode-select --install
  ```

## Build the app

```bash
git clone https://github.com/charaka-codes/NUM.git
cd NUM
./build.sh
```

This creates a virtualenv, installs the dependencies (pyobjc frameworks +
py2app), and produces **`dist/NUM.app`**.

## Make a drag-and-drop installer (DMG)

For the standard Mac experience — a `.dmg` that opens to show the app next to
an Applications folder, so you just drag one onto the other:

```bash
./make-dmg.sh
```

This packages **`NUM.dmg`**. Share that file with anyone; they open it and drag
NUM onto Applications. (`./release.sh` does the build and the DMG in one step.)

## Install

Open `NUM.dmg` and drag **NUM** onto the Applications folder, then launch it.

On first launch macOS may warn it's from an unidentified developer — this is
expected for an unsigned app. Right-click the app -> **Open** -> **Open**, or
allow it under System Settings -> Privacy & Security.

### Wi-Fi network names (important)

macOS treats the Wi-Fi network *name* as location data, so it hides it until
you grant Location permission. Without it, networks show as a generic "Wi-Fi".
To see real SSIDs:

System Settings -> Privacy & Security -> Location Services -> enable for **NUM**.

The permission prompt appears the first time you run the installed app.

### Launch at login

Open the panel -> **Settings** (gear) -> toggle **Launch at login**. Or add it
manually via System Settings -> General -> Login Items.

## Using NUM

- **Left-click** the menu bar icon to open the panel.
- **Day tab**: a week strip — tap any day to see its total and per-network
  breakdown. Use the arrows to move between weeks.
- **Year tab**: twelve month tiles for the year, with a year total.
- **Settings** (gear button): tracking on/off, show/hide the menu bar number,
  launch at login, transparency, download/upload colours, and the export tools.
- **Export**: in Settings, choose Month / Year / All time, pick the period, and
  export a CSV to your Downloads.
- **Quit** from the button in the panel.

## Uninstall

```bash
./uninstall.sh
```

Removes the app and asks whether to delete your usage data. Or drag the app to
the Trash and delete `~/.netmonitor` manually.

## Run from source (development)

```bash
pip install pyobjc-framework-Cocoa pyobjc-framework-WebKit \
  pyobjc-framework-CoreLocation pyobjc-framework-CoreWLAN \
  pyobjc-framework-ServiceManagement
python3 run.py                        # launches the menu bar app
python3 -m netmonitor.report          # this month's report in the terminal
python3 -m netmonitor.report 2026-07  # a specific month
python3 -m netmonitor.report --all    # every recorded month
```

Note: some features (single-click open, the real Wi-Fi name via Location
permission) only behave correctly in the built `.app`, not when running loose
from source.

## How it works

On a short interval the app reads macOS's own interface byte counters
(`netstat -ibn`), identifies the active network (`route get default` plus the
Wi-Fi SSID via CoreWLAN / system_profiler), and writes the *delta* since the
last reading into SQLite, tagged with the network label. Views and reports
group those rows by network and period.

Because it measures interface throughput, totals include background and system
traffic too — which is what you want for a data-cap style report.

## Project structure

```
NUM/
├── netmonitor/            # (internal package name)
│   ├── core.py            # network detection, byte counters, SQLite storage
│   ├── app.py             # native AppKit menu bar app
│   ├── popover.py         # NSPopover + WKWebView glass panel
│   ├── dashboard.py       # the panel UI (HTML/CSS/JS)
│   ├── settings.py        # persistent preferences
│   └── report.py          # CLI report tool
├── assets/                # app icon + menu bar template
├── run.py                 # entry point
├── setup.py               # py2app build config
├── build.sh               # one-command build
├── make-dmg.sh            # build the DMG installer
├── uninstall.sh           # remove app + data
└── README.md
```

## License

MIT (c) Charaka (@charaka-codes)
