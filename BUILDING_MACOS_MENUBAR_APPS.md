# Building macOS Menu Bar Apps — Lessons Learned

A practical playbook distilled from building NetMonitor on macOS 26 (Tahoe)
with Python. Read this first before starting a similar tool — it captures the
approaches that actually worked and the dead ends to skip, saving time and
tokens.

Target environment for these notes: macOS 26 (Tahoe), Python 3.14 from
python.org, Apple Silicon.

---

## TL;DR — the winning recipe

1. Use **python.org Python**, never the Apple system Python.
2. Build the menu bar item **natively with pyobjc** (`NSStatusItem`), not with
   rumps, if you need a single click to open a popover.
3. Show rich UI in an **`NSPopover` hosting a `WKWebView`** that renders local
   HTML. Do the whole UI as a self-contained HTML string.
4. For **Liquid Glass**, use `NSVisualEffectView` (material 6 = popover) behind
   a **transparent** WKWebView, with a light HTML color scheme.
5. Read the **Wi-Fi SSID** via CoreWLAN or `system_profiler`, and you MUST
   request **Location permission** (macOS 26 hides SSIDs otherwise).
6. Package with **py2app**, excluding Tcl/Tk, and drop `install_requires`.
7. Put app actions (Quit, Export, etc.) as **buttons inside the panel** with a
   JS→native message bridge, so you don't fight the menu system.

---

## Environment setup

### Use python.org Python, not the system one
The Apple system Python (`/Library/Developer/CommandLineTools/...`) is old and
tries to compile pyobjc wheels from source, which fails with compiler errors
(e.g. building `pyobjc-core`). Installing Python 3.14 from python.org gives
prebuilt universal2 wheels that install in seconds and is also the Python you
want for py2app.

Symptom to recognize: `pyobjc-core ... error` during `pip install`, or a
`bad interpreter` error later.

### Watch for special characters in folder paths
A project path containing `#` (e.g. `Documents/#_APP_project/...`) breaks
virtualenv launcher scripts: `bad interpreter: Permission denied`. Build from a
plain path like `~/ProjectName`.

Also: if you copy a project folder that already contains a `.buildenv`, the old
absolute path is baked into it and reused wrongly. Always `rm -rf .buildenv`
after moving a project.

---

## Menu bar item: go native, skip rumps (if you need click→popover)

**rumps** is great for simple menu-bar apps whose icon opens a *menu*. But it is
built around "icon click = show menu," and it will fight you if you want a
single left-click to open a custom popover/panel. We spent many iterations
trying to make rumps do this (detaching the menu, overriding the button action,
event monitors) and none were reliable across versions.

**The correct solution:** drop rumps and create the status item yourself:

```python
from AppKit import (NSApplication, NSStatusBar, NSVariableStatusItemLength,
                    NSApplicationActivationPolicyAccessory)
from Foundation import NSObject
import objc

class AppDelegate(NSObject):
    def initWithState_(self, state):
        self = objc.super(AppDelegate, self).init()
        self.state = state
        return self
    def applicationDidFinishLaunching_(self, note):
        self.state.setup(self)
    def statusClicked_(self, sender):
        self.state.on_click()

# in setup():
bar = NSStatusBar.systemStatusBar()
item = bar.statusItemWithLength_(NSVariableStatusItemLength)
button = item.button()
button.setTitle_("◎")
button.setTarget_(delegate)
button.setAction_("statusClicked:")   # NO menu attached → click fires this

# main():
app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # menu bar only
```

Because no `NSMenu` is attached to the item, a click has nothing to do but fire
your action → open the panel. Keep strong references to the delegate and state
so they aren't garbage-collected (`main._delegate = delegate`).

### Left vs right click — the off-by-one that cost us
To offer a right-click "Quit" safety net while left-click opens the panel, you
check the event type. **The exact numbers matter:**

```
NSEventTypeLeftMouseDown  = 1
NSEventTypeLeftMouseUp    = 2
NSEventTypeRightMouseDown = 3
NSEventTypeRightMouseUp   = 4
```

We wrongly treated `{2,4}` as "right click," which made every *left* click (type
2) show the Quit menu. Right-click is `{3, 4}` only. Also treat control-click as
right:

```python
ev = NSApp.currentEvent()
is_right = ev.type() in (3, 4) or bool(ev.modifierFlags() & NSEventModifierFlagControl)
```

Enable both mouse buttons on the status button:
```python
button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
```

---

## Rich UI: NSPopover + WKWebView + local HTML

Render the whole panel as a **self-contained HTML string** (inline CSS/JS, no
external files) and load it into a `WKWebView` inside an `NSPopover`:

```python
config = WKWebViewConfiguration.alloc().init()
webview = WKWebView.alloc().initWithFrame_configuration_(rect, config)
webview.loadHTMLString_baseURL_(html, None)   # baseURL None is fine

vc = NSViewController.alloc().init()
vc.setView_(container)
popover = NSPopover.alloc().init()
popover.setContentSize_((W, H))
popover.setBehavior_(NSPopoverBehaviorTransient)  # closes on click-away
popover.setContentViewController_(vc)
# anchor under the status button:
popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, 1)  # 1 = below
```

Advantages: you get the full power of HTML/CSS for layout, and the same HTML
doubles as a "open in browser" dashboard.

### Auto-size the popover to content
Fixed heights leave dead space. After load, measure the document and resize:
```python
webview.evaluateJavaScript_completionHandler_(
  "Math.ceil(document.querySelector('.panel').getBoundingClientRect().height)+8",
  handler)  # handler calls popover.setContentSize_((W, measuredHeight))
```
Fire it a few times over the first ~1s (layout settles) and again after tab
switches / data updates.

### Live updates without losing UI state
Don't reload the whole page every tick — it resets the user's selected tab/day.
Instead expose a JS function and push fresh data into it:
```python
webview.evaluateJavaScript_completionHandler_(
  "window.__updateData(" + json.dumps(payload) + ");", None)
```
The JS updates the DOM in place and re-renders the current view.

---

## Liquid Glass (macOS 26 translucent look)

To match system popovers (Bluetooth, Control Center):

1. Put an **`NSVisualEffectView`** behind the web view:
   ```python
   effect = NSVisualEffectView.alloc().initWithFrame_(rect)
   effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
   effect.setState_(NSVisualEffectStateActive)
   effect.setMaterial_(6)   # 6 = NSVisualEffectMaterialPopover (light glass)
   effect.addSubview_(webview)
   ```
   Material 6 = light popover glass. Material 18 = dark HUD glass (we used this
   first; it looked dark/solid — switch to 6 for the light system look).

2. Make the **WKWebView transparent** so the glass shows through. WebKit
   ignores some setters and RESETS the background on every load, so clear it
   aggressively AND re-clear after each load:
   ```python
   webview.setValue_forKey_(False, "drawsBackground")
   webview.setOpaque_(False)
   webview.setBackgroundColor_(NSColor.clearColor())
   webview.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
   ```
   Re-run this in the load-complete path — this was the fix for the panel
   staying grey.

3. In the **HTML**, use a light translucent scheme:
   `html,body { background: transparent }`, cards with
   `background: rgba(255,255,255,0.42); backdrop-filter: blur(24px) saturate(160%)`,
   dark text (`#1d1d1f`), thin light borders. Set the popover appearance to
   `NSAppearanceNameAqua` for the light look.

Note: true wallpaper refraction only happens with the vibrancy view behind a
transparent web view — glass done purely in CSS can't blur the actual desktop.

---

## Wi-Fi SSID on macOS 26 (the important one for per-network tools)

macOS 26 treats the Wi-Fi network *name* as location data and **hides it**
unless your app has **Location permission**. Without it you get a generic
"Wi-Fi" or a redacted value. The old `networksetup -getairportnetwork en0` is
deprecated on Tahoe.

**What works:**
1. Request location authorization at startup and keep the manager alive:
   ```python
   from CoreLocation import CLLocationManager
   mgr = CLLocationManager.alloc().init()
   mgr.requestWhenInUseAuthorization()   # keep `mgr` referenced!
   ```
2. Read the SSID, trying in order:
   - **CoreWLAN**: `CWWiFiClient.sharedWiFiClient().interface().ssid()`
   - **system_profiler**: parse `system_profiler SPAirPortDataType` for the
     line under "Current Network Information:"
   - legacy `networksetup` (pre-26 fallback)
3. **Info.plist MUST include** a usage string or macOS silently denies and never
   even prompts:
   ```
   NSLocationUsageDescription = "…reads the current Wi-Fi network name…"
   NSLocationWhenInUseUsageDescription = "…"
   ```

Critical: the permission prompt only attaches to a real **bundled .app**. When
running loose via `python3 run.py`, the prompt may attach to Terminal/Python or
not appear — so test SSID features in the built app, not from source.

---

## Actions in the panel via a JS→native bridge

Rather than fight the menu system for Quit/Export/etc., put them as buttons in
the HTML panel and bridge taps back to Python:

Native side — register a script message handler:
```python
class _ActionHandler(NSObject):
    def initWithCallback_(self, cb): ...
    def userContentController_didReceiveScriptMessage_(self, ucc, message):
        self._cb(str(message.body()))

ucc = config.userContentController()
ucc.addScriptMessageHandler_name_(handler, "action")
```

HTML side:
```javascript
window.webkit.messageHandlers.action.postMessage("quit");   // or "export" etc.
```

Then dispatch the string to real methods (quit, export CSV, open folder,
open dashboard). This worked reliably first try and is much cleaner than
juggling native menus.

---

## Packaging with py2app

`setup.py` essentials that bit us:
- **Drop `install_requires`** — newer py2app errors with
  "install_requires is no longer supported".
- **Exclude Tcl/Tk and other heavy unused libs** or ad-hoc code signing fails
  with "Cannot sign bundle" (Tk ships unsigned stubs):
  ```python
  "excludes": ["tkinter","Tkinter","tcl","tk","_tkinter","PyQt5","PyQt6",
               "PySide2","PySide6","wx","numpy","scipy","pandas","matplotlib","test"],
  ```
- `"LSUIElement": True` → menu bar only, no Dock icon.
- Include the frameworks you import:
  `"includes": [...,"WebKit","AppKit","Foundation","CoreLocation"]`.
- Add the `NSLocation*UsageDescription` plist strings (see Wi-Fi section).

Build in a clean virtualenv (`python -m venv .buildenv`), install
`pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-CoreLocation
pyobjc-framework-CoreWLAN py2app`, then `python setup.py py2app`. Output:
`dist/YourApp.app`.

"Modules not found" chatter at the end of a py2app build is normal (it lists
optional Windows/Java modules it skipped) — not an error. Look for the final
"Done" / your success message.

### Unsigned app first-launch
A self-built app is unsigned, so Gatekeeper blocks double-click. First launch:
right-click → Open → Open. To distribute without that friction you need an
Apple Developer account ($99/yr) to sign + notarize. If ad-hoc signing during
build fails, you can also sign manually:
`codesign --force --deep --sign - dist/YourApp.app`.

---

## Distribution: DMG

macOS's built-in `hdiutil` makes a drag-to-install DMG, no extra tools:
```bash
# stage app + an Applications symlink, then:
hdiutil create -volname "YourApp" -srcfolder staging -ov -format UDZO YourApp.dmg
```
Opening the DMG shows your app next to an Applications shortcut → drag to
install. (Recipients still do the one-time right-click→Open for unsigned apps.)

---

## Data / measurement notes (network monitor specifics)

- Read interface byte counters from `netstat -ibn` (the line where the
  interface has a `<Link…>` address carries cumulative in/out bytes).
- Find the active interface via `route get default` → `interface:`.
- Sample on a timer and store the **delta** since the last reading, tagged with
  the current network label, into SQLite (`~/.netmonitor/usage.db`).
- Guard against counter resets (reconnect/reboot): if the new counter is lower
  than the last, treat the delta as 0.
- A short sample interval (2s) gives responsive live numbers; a "speed" readout
  is just recent bytes / seconds over a small window (e.g. last 8s).

---

## Background work: timers and threads (IMPORTANT)

Two traps cost real time here; both look like "the app stopped working" but
have different causes.

### NSTimer doesn't fire reliably in a bundled app
`NSTimer.scheduledTimer...` pauses during menu/popover event tracking and, in a
py2app bundle, sometimes never fires at all. Symptom: the value updates once at
launch then freezes. Even adding the timer in `NSRunLoopCommonModes` wasn't
enough on macOS 26.

**Fix that works:** run periodic work on a **plain Python background thread**
with a `time.sleep(interval)` loop (a `threading.Thread(daemon=True)`). Marshal
any UI updates back to the main thread via
`NSOperationQueue.mainQueue().addOperationWithBlock_(fn)`. This is dead simple
and rock-solid, and it's easy to prove in isolation with a tiny script.

Diagnostic tip: when tracking "freezes," write a standalone loop that reads the
same source every 2s and prints deltas. If the standalone loop moves but the app
doesn't, the bug is the timer, not the reading.

### SQLite WAL: long-lived reader connections see a stale snapshot
If a background thread WRITES on its own connection and the main thread READS on
a separate long-lived connection (WAL mode, which you want so reads don't block
writes), the reader can get **stuck on an old snapshot** and keep returning the
same totals even as the writer commits new rows.

Nasty symptom: *derived* values that recompute from a small recent window (e.g.
a "current speed") appear to update, while *cumulative* totals look frozen —
making it seem like recording stopped when it didn't.

**Fix:** before each read on the UI connection, end its implicit transaction to
refresh its view — a cheap `con.commit()` (or `rollback()`) does it. Do this
right before the hot reads (menu title, panel refresh). Verified: totals then
track the writer correctly.

Design rule: give the writer (sampler) its OWN connection created on the
writer's thread; open reads with `check_same_thread=False`, `PRAGMA
journal_mode=WAL`, and refresh the view before reading.

## Launch at login (SMAppService)

Modern, no-hacks way to add a "launch at login" toggle on macOS 13+:

```python
from ServiceManagement import SMAppService
service = SMAppService.mainAppService()
service.registerAndReturnError_(None)     # enable
service.unregisterAndReturnError_(None)   # disable
int(service.status()) == 1                # 1 = enabled
```

Add `pyobjc-framework-ServiceManagement` to the build and include
`"ServiceManagement"` in py2app `includes`. Works even unsigned on recent
macOS, but is happiest when the app is in `/Applications`. Test from the
INSTALLED app, not `dist/` — macOS registers the app at its real path.

## General workflow lessons

- **Test from source first** (`python3 run.py`) for fast iteration, but know
  that some behaviors (status-item clicks, Location permission, and thus SSID)
  only behave correctly in a **built .app** — don't over-debug them from
  source.
- When a native behavior misfires, run the built app from Terminal to see
  stderr: `./dist/YourApp.app/Contents/MacOS/YourApp`.
- Keep every AppKit/WebKit call defensive (try/except) so one unavailable API
  doesn't crash the whole app; degrade gracefully.
- Prefer putting UI/logic in HTML+JS (portable, testable in a browser) and keep
  the native layer thin (status item, popover, bridge, system queries).
