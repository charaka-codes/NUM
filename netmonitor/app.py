"""
app.py — NUM (Network Usage Monitor) menu bar application (native AppKit, no rumps).

We create the NSStatusItem directly so we own the click completely: a single
left-click on the icon opens the Liquid Glass popover panel — no menu, nothing
competing for the click. All actions (Dashboard, Export, Data folder, Quit)
live as buttons inside the panel.
"""

import csv
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from . import core
from . import dashboard
from . import popover as popover_mod
from . import settings as settings_mod

from AppKit import (NSApplication, NSStatusBar, NSVariableStatusItemLength,
                    NSApp, NSMenu, NSMenuItem, NSApplicationActivationPolicyAccessory)
from Foundation import NSObject
import objc

SAMPLE_INTERVAL = 2  # seconds


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class AppDelegate(NSObject):
    def initWithState_(self, state):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.state = state
        return self

    def applicationDidFinishLaunching_(self, note):
        self.state.setup(self)

    # Status item click target
    def statusClicked_(self, sender):
        self.state.on_click()

    def quitFromMenu_(self, sender):
        self.state.quit_app()

    def colorChanged_(self, panel):
        self.state.color_changed(panel)


class NetMonitorState:
    """Holds the app logic; the delegate forwards events here."""

    def __init__(self):
        self.con = core.connect()
        self.settings = settings_mod.load()
        self.sampler = core.Sampler(self.con)
        self.panel = popover_mod.DashboardPopover(
            self.con, action_cb=self._panel_action,
            transparency=self.settings.get("transparency", 60))
        self._loc_mgr = core.request_location_permission()
        self.status_item = None
        self.delegate = None

    def _load_menubar_icon(self):
        """Load the template menu-bar icon (adapts to light/dark)."""
        try:
            import os
            from AppKit import NSImage
            candidates = []
            try:
                from Foundation import NSBundle
                rp = NSBundle.mainBundle().resourcePath()
                if rp:
                    candidates.append(os.path.join(rp, "menubar_Template.png"))
            except Exception:
                pass
            here = os.path.dirname(os.path.abspath(__file__))
            candidates.append(os.path.join(here, "..", "assets", "menubar_Template.png"))
            for path in candidates:
                if os.path.exists(path):
                    img = NSImage.alloc().initWithContentsOfFile_(path)
                    if img is not None:
                        try:
                            img.setTemplate_(True)
                            img.setSize_((16, 22))
                        except Exception:
                            pass
                        return img
        except Exception:
            pass
        return None

    def setup(self, delegate):
        self.delegate = delegate

        # Create our own status item — we control everything about it.
        bar = NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        # Use our template image for the menu bar icon (adapts to light/dark).
        self._icon_img = self._load_menubar_icon()
        if self._icon_img is not None:
            button.setImage_(self._icon_img)
            button.setImagePosition_(2)  # NSImageLeft: image then title
        else:
            button.setTitle_("◎")
        # Left-align the title so the icon side stays anchored and only the
        # text grows/shrinks to the right — this keeps the popover from
        # jumping horizontally as the number's width changes.
        try:
            from AppKit import NSTextAlignmentLeft
            button.setAlignment_(NSTextAlignmentLeft)
        except Exception:
            pass
        button.setTarget_(delegate)
        button.setAction_("statusClicked:")
        try:
            from AppKit import NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp
            button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        except Exception:
            pass
        # Apply the saved menu-text preference immediately.
        self.refresh_title()

        # Sampling runs on a background thread with a simple sleep loop — the
        # same mechanism proven to work in diagnose.py. NSTimers were
        # unreliable here (they pause during menu/popover tracking and can fail
        # to fire in the bundled app). UI touches are marshalled to the main
        # thread.
        self._running = True
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        # First UI paint immediately.
        self._update_ui_main()

    def _sample_loop(self):
        import time
        while self._running:
            # Only sample when tracking is enabled. When off, we still keep the
            # loop alive (so it resumes instantly when toggled on) but reset the
            # counter baseline so the paused period isn't counted as one big
            # delta when tracking resumes.
            if self.settings.get("tracking", True):
                try:
                    self.sampler.sample()
                except Exception:
                    pass
            else:
                try:
                    self.sampler.pause()
                except Exception:
                    pass
            try:
                self._dispatch_main(self._update_ui_main)
            except Exception:
                pass
            time.sleep(SAMPLE_INTERVAL)

    def _dispatch_main(self, fn):
        """Run fn on the main thread (AppKit UI must not be touched off-main)."""
        try:
            from Foundation import NSOperationQueue
            NSOperationQueue.mainQueue().addOperationWithBlock_(fn)
        except Exception:
            # Fallback: call directly (best effort)
            try:
                fn()
            except Exception:
                pass

    def _update_ui_main(self):
        self.refresh_title()
        try:
            self.panel.refresh()
        except Exception:
            pass

    # ---------- click ----------

    def on_click(self):
        # Distinguish left vs right click. Right-click → tiny Quit menu
        # (safety net). Left-click → open the panel.
        # NSEventType: LeftMouseDown=1, LeftMouseUp=2, RightMouseDown=3,
        # RightMouseUp=4. (The earlier bug treated 2/LeftMouseUp as right.)
        is_right = False
        try:
            ev = NSApp.currentEvent()
            if ev is not None:
                if ev.type() in (3, 4):  # right mouse down / up only
                    is_right = True
                else:
                    try:
                        from AppKit import NSEventModifierFlagControl
                        if ev.modifierFlags() & NSEventModifierFlagControl:
                            is_right = True
                    except Exception:
                        pass
        except Exception:
            pass

        if is_right:
            self._show_quit_menu()
            return

        btn = self.status_item.button() if self.status_item else None
        ok = self.panel.toggle(btn)
        if not ok:
            self.show_dashboard()

    def _show_quit_menu(self):
        """Pop up a minimal menu (Quit) on right-click, then remove it so
        left-click keeps opening the panel."""
        try:
            menu = NSMenu.alloc().init()
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit NUM", "quitFromMenu:", "")
            item.setTarget_(self.delegate)
            menu.addItem_(item)
            self.status_item.setMenu_(menu)
            self.status_item.button().performClick_(None)
            self.status_item.setMenu_(None)
        except Exception:
            pass

    def _panel_action(self, name):
        if name == "dashboard":
            self.show_dashboard()
        elif name == "export" or name.startswith("export:"):
            spec = name.split(":", 1)[1] if ":" in name else None
            self.export_csv(spec)
        elif name == "folder":
            self.open_folder()
        elif name == "quit":
            self.quit_app()
        elif name == "resize":
            try:
                self.panel._resize_to_content()
            except Exception:
                pass
        elif name.startswith("pickcolor:"):
            which = name.split(":", 1)[1]  # "down" or "up"
            self._open_color_panel(which)
        elif name.startswith("set:"):
            self._apply_setting(name)

    def _open_color_panel(self, which):
        """Open the native macOS colour wheel and apply the chosen colour live."""
        try:
            from AppKit import NSColorPanel, NSColor, NSApp
            # Keep our popover from dismissing when the colour panel takes focus.
            try:
                self.panel.hold_open()
            except Exception:
                pass
            panel = NSColorPanel.sharedColorPanel()
            self._color_target = which
            # Seed with the current colour.
            cur = self.settings.get(
                "down_color" if which == "down" else "up_color", "#2e9e5b")
            try:
                r, g, b = _hex_to_rgb(cur)
                panel.setColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                    r/255.0, g/255.0, b/255.0, 1.0))
            except Exception:
                pass
            panel.setTarget_(self.delegate)
            panel.setAction_("colorChanged:")
            panel.setContinuous_(True)
            NSApp.activateIgnoringOtherApps_(True)
            panel.makeKeyAndOrderFront_(None)
        except Exception:
            pass

    def color_changed(self, panel):
        """Called as the user drags in the native colour wheel."""
        try:
            from AppKit import NSColorSpace
            c = panel.color()
            c = c.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
            r = int(round(c.redComponent() * 255))
            g = int(round(c.greenComponent() * 255))
            b = int(round(c.blueComponent() * 255))
            hexc = f"#{r:02x}{g:02x}{b:02x}"
            which = getattr(self, "_color_target", "down")
            key = "down_color" if which == "down" else "up_color"
            self.settings[key] = hexc
            settings_mod.set_value(key, hexc)
            # Push into the webview live.
            try:
                js = ("(function(){DATA.settings." + key + "='" + hexc + "';"
                      "applyColors();})();")
                self.panel.webview.evaluateJavaScript_completionHandler_(js, None)
            except Exception:
                pass
        except Exception:
            pass

    def _apply_setting(self, msg):
        """Handle 'set:key:value' from the settings panel: persist + apply."""
        try:
            _, key, value = msg.split(":", 2)
        except ValueError:
            return
        if key in ("tracking", "show_menu_text", "launch_at_login"):
            val = value in ("1", "true", "True")
        elif key == "transparency":
            try:
                val = int(value)
            except ValueError:
                return
        else:  # colors
            val = value
        self.settings[key] = val
        settings_mod.set_value(key, val)
        # Apply immediately where relevant.
        if key == "show_menu_text":
            self.refresh_title()
        elif key == "transparency":
            try:
                self.panel.set_transparency(val)
            except Exception:
                pass
        elif key == "launch_at_login":
            try:
                core.set_launch_at_login(val)
            except Exception:
                pass

    # ---------- ui refresh ----------

    def refresh_title(self):
        try:
            if self.status_item is None:
                return
            btn = self.status_item.button()
            if self.settings.get("show_menu_text", True):
                core.refresh_view(self.con)
                now = datetime.now()
                down, up = core.day_total(self.con, now.strftime("%Y-%m-%d"))
                btn.setTitle_(f" {core.human(down + up)}")
            else:
                btn.setTitle_("")
        except Exception:
            pass

    # ---------- actions ----------

    def show_dashboard(self):
        out = core.DB_PATH.parent / "dashboard.html"
        try:
            dashboard.write_dashboard(self.con, str(out))
            subprocess.run(["open", str(out)])
        except Exception:
            pass

    def export_csv(self, spec=None):
        """
        Export a CSV report. `spec` is a string from the panel:
          "month:YYYY-MM"  → that month
          "year:YYYY"      → that whole year
          "all"            → all history
        Falls back to the current month if spec is missing.
        """
        core.refresh_view(self.con)
        try:
            if spec and spec.startswith("month:"):
                period = spec.split(":", 1)[1]
                rows = core.month_breakdown(self.con, period)
                label, fname = period, f"num-{period}.csv"
            elif spec and spec.startswith("year:"):
                period = spec.split(":", 1)[1]
                rows = core.year_breakdown(self.con, period)
                label, fname = period, f"num-{period}.csv"
            elif spec == "all":
                rows = core.all_breakdown(self.con)
                label, fname = "all-time", "num-all-time.csv"
            else:
                period = datetime.now().strftime("%Y-%m")
                rows = core.month_breakdown(self.con, period)
                label, fname = period, f"num-{period}.csv"

            out = Path.home() / "Downloads" / fname
            with open(out, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["period", "network", "download_bytes",
                            "upload_bytes", "download", "upload"])
                for net, d, u in rows:
                    w.writerow([label, net, d, u, core.human(d), core.human(u)])
            subprocess.run(["open", "-R", str(out)])
        except Exception:
            pass

    def open_folder(self):
        subprocess.run(["open", str(core.DB_PATH.parent)])

    def quit_app(self):
        try:
            NSApp.terminate_(None)
        except Exception:
            import os
            os._exit(0)


def main():
    app = NSApplication.sharedApplication()
    # Accessory = menu bar only, no Dock icon.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    state = NetMonitorState()
    delegate = AppDelegate.alloc().initWithState_(state)
    app.setDelegate_(delegate)
    # Keep strong references so nothing is garbage-collected while running.
    main._delegate = delegate
    main._state = state
    app.run()


if __name__ == "__main__":
    main()
