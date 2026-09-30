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

SAMPLE_INTERVAL = 1  # seconds — main interface sampler (live speed + totals)


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

    def colorPanelWillClose_(self, note):
        # The native colour panel is closing → release the popover hold so it
        # returns to normal click-away-to-close behaviour automatically.
        try:
            self.state.color_panel_closed()
        except Exception:
            pass


class NetMonitorState:
    """Holds the app logic; the delegate forwards events here."""

    def __init__(self):
        # Watch our own bundle path so a plain drag-to-Trash makes the
        # running app quit itself promptly, same as well-behaved menu-bar
        # apps do. Finder gives a running app zero notice that its files
        # are being deleted, so self-checking is the only way to notice at
        # all -- this doesn't touch the separate, OS-level registry entry
        # (that one genuinely can't be cleared by any app), it just stops
        # a now-fileless process from continuing to sit in the menu bar.
        # None (skipped entirely) when running from source, where there's
        # no real .app bundle to watch in the first place.
        self._own_bundle_path = None
        try:
            from Foundation import NSBundle
            bp = NSBundle.mainBundle().bundlePath()
            if bp and bp.endswith(".app"):
                self._own_bundle_path = bp
        except Exception:
            pass

        # One-time conversion of the old single usage.db into month files. This
        # runs before anything reads or writes, is lossless for daily totals,
        # and keeps the original as usage.db.premigration. No-op once done.
        try:
            st = core._store()
            if st.needs_migration():
                st.migrate_legacy()
            st.seal_old_months()
        except Exception:
            pass
        self.con = core.connect()
        self.settings = settings_mod.load()
        self.sampler = core.Sampler(self.con)
        # Per-app (per-process) sampler — v2. Runs on its own slow beat so it
        # never rides the 2s live-speed loop. Gated by the 'app_tracking'
        # setting (see settings.py default).
        try:
            from . import apps as apps_mod
        except Exception:
            apps_mod = None
        self.apps_mod = apps_mod
        # The per-app sampler owns ONE long-lived nettop (see apps.NettopReader).
        # Reading a process that stays alive is what makes the counters
        # comparable between samples; spawning a fresh nettop each time only
        # ever saw the second it was alive for, losing the rest.
        _riv = 10
        try:
            _riv = max(1, min(30, int(
                settings_mod.load().get("app_reader_interval", 10) or 10)))
        except Exception:
            pass
        self.app_sampler = apps_mod.AppSampler(interval=_riv) if apps_mod else None
        self.panel = popover_mod.DashboardPopover(
            self.con, action_cb=self._panel_action,
            transparency=self.settings.get("transparency", 15))
        # Only request Location (needed for Wi-Fi names) if the user wants names.
        if self.settings.get("network_names", True):
            self._loc_mgr = core.request_location_permission()
        else:
            self._loc_mgr = None
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
        # Warm the network fingerprint ONCE before any sampler starts, so both
        # samplers see the same stable gateway-MAC key from their very first
        # sample and never create a phantom 'ip:' or 'Wi-Fi' duplicate network.
        try:
            _fp = core.warm_fingerprint()
            if _fp:
                self.sampler._last_fp = _fp
                if self.app_sampler is not None:
                    self.app_sampler._last_fp = _fp
        except Exception:
            pass
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        # Second, slower beat for per-app sampling (v2). Separate thread so the
        # heavier nettop read never delays the 2s live-speed loop.
        if self.app_sampler is not None:
            self._app_thread = threading.Thread(
                target=self._app_sample_loop, daemon=True)
            self._app_thread.start()
        # First UI paint immediately.
        self._update_ui_main()

    def _sample_loop(self):
        import os
        import time
        # Warm the network-label cache AND the fingerprint immediately on this
        # background thread so the very first recorded sample already uses the
        # stable fingerprint (not a throwaway 'Wi-Fi' label).
        try:
            iface = core.active_interface()
            if iface:
                core.network_label(iface)
            fp = core.network_fingerprint()
            if fp:
                self.sampler._last_fp = fp
        except Exception:
            pass
        while self._running:
            if self._own_bundle_path and not os.path.exists(self._own_bundle_path):
                # We've been dragged to the Trash (or otherwise deleted)
                # while still running. Quit rather than keep running
                # invisibly with nothing left on disk.
                try:
                    self.quit_app()
                except Exception:
                    pass
                break
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

    def _app_sample_loop(self):
        """Slow beat for per-app sampling via nettop. Default 30s. Gated by the
        'app_tracking' setting; when off (or overall tracking off) it idles but
        stays alive so toggling resumes instantly."""
        import time, os
        # Kill switch for isolation testing: NUM_NO_APPSAMPLER=1 disables the
        # nettop sampling entirely (thread still exists but does nothing).
        if os.environ.get("NUM_NO_APPSAMPLER"):
            return
        interval = int(self.settings.get("app_sample_interval", 30) or 30)
        while self._running:
            tracking = self.settings.get("tracking", True)
            app_on = self.settings.get("app_tracking", True)
            if tracking and app_on and self.app_sampler is not None:
                try:
                    self.app_sampler.sample()
                except Exception:
                    if os.environ.get("NUM_DEBUG"):
                        import traceback
                        traceback.print_exc()
            else:
                try:
                    if self.app_sampler is not None:
                        self.app_sampler.pause()
                        self.app_sampler.stop()   # don't hold nettop open when off
                except Exception:
                    pass
            # Re-read interval each loop so a settings change takes effect.
            interval = int(self.settings.get("app_sample_interval", 30) or 30)
            time.sleep(max(5, interval))

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
        # Any fresh click on the icon should clear a leftover colour-picker hold
        # so the panel behaves normally (closes on outside click) again.
        try:
            self.panel.release_hold()
        except Exception:
            pass
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
        elif name == "checkupdate":
            self._check_updates()
        elif name.startswith("openurl:"):
            url = name.split(":", 1)[1]
            if url:
                try:
                    subprocess.run(["open", url])
                except Exception:
                    pass
        elif name.startswith("rename:"):
            # rename:<fingerprint>:<urlencoded name>
            # The fingerprint itself contains colons (gw:50:2b:...), and the
            # name is URL-encoded (so it has no raw colons). So: strip the
            # 'rename:' prefix, then the LAST ':' separates fp from the encoded
            # name.
            try:
                rest = name[len("rename:"):]
                idx = rest.rfind(":")
                fp = rest[:idx]
                enc = rest[idx + 1:]
                from urllib.parse import unquote
                newname = unquote(enc)
                core.set_network_name(fp, newname)
                self._dispatch_main(self._update_ui_main)
            except Exception:
                pass
        elif name.startswith("netexclude:"):
            try:
                rest = name[len("netexclude:"):]
                idx = rest.rfind(":")
                fp = rest[:idx]
                excl = rest[idx + 1:] == "1"
                core.set_network_excluded(fp, excl)
                self.refresh_title()
                self._dispatch_main(self._update_ui_main)
            except Exception:
                pass
        elif name.startswith("netdelete:"):
            try:
                fp = name[len("netdelete:"):]
                core.delete_network(fp, self.con)
                self.refresh_title()
                self._dispatch_main(self._update_ui_main)
            except Exception:
                pass
        elif name.startswith("appmonth:"):
            # Panel navigated the Apps tab to a month; serve it on demand.
            try:
                self.panel.request_app_month(name[len("appmonth:"):])
            except Exception:
                pass
        elif name == "alltime:generate":
            # All-time tab's Generate button. Computed on demand, cached —
            # see core.all_time_total() and popover.request_all_time().
            try:
                self.panel.request_all_time()
            except Exception:
                pass
        elif name == "erasealldata":
            try:
                core.erase_all_data(self.con)
                settings_mod.set_value("alltime_cache", {})
                self.settings["alltime_cache"] = {}
                try:
                    js = ("(function(){DATA.settings.alltime_cache={};"
                          "if(typeof tab!=='undefined'&&tab==='alltime'&&"
                          "typeof renderAllTime==='function'){renderAllTime();}})();")
                    self.panel.webview.evaluateJavaScript_completionHandler_(js, None)
                except Exception:
                    pass
                # Reset both samplers' cumulative baselines so the next tick
                # records fresh deltas rather than a huge phantom jump against
                # the now-empty tables.
                try:
                    self.sampler.pause()
                except Exception:
                    pass
                if self.app_sampler is not None:
                    try:
                        self.app_sampler.pause()
                    except Exception:
                        pass
                self.refresh_title()
                self._dispatch_main(self._update_ui_main)
            except Exception:
                pass
        elif name == "uninstall_app":
            try:
                self.uninstall_self()
            except Exception:
                pass
        elif name == "quit":
            self.quit_app()
        elif name == "resize":
            try:
                self.panel._resize_to_content()
            except Exception:
                pass
        elif name.startswith("pickcolor:"):
            which = name.split(":", 1)[1]  # "down_light" | "up_light" | "down_dark" | "up_dark"
            self._open_color_panel(which)
        elif name == "resetcolors":
            self._reset_colors()
        elif name.startswith("set:"):
            self._apply_setting(name)

    # which -> the settings.py key it edits. Centralised so the picker, the
    # seed colour, and color_changed()'s write-back all agree on the same
    # mapping — this is exactly the kind of thing that drifts if duplicated.
    _COLOR_KEYS = {
        "down_light": "down_color_light", "up_light": "up_color_light",
        "down_dark": "down_color_dark", "up_dark": "up_color_dark",
    }

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
            key = self._COLOR_KEYS.get(which, "down_color_light")
            cur = self.settings.get(key, "#2e9e5b")
            try:
                r, g, b = _hex_to_rgb(cur)
                panel.setColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                    r/255.0, g/255.0, b/255.0, 1.0))
            except Exception:
                pass
            panel.setTarget_(self.delegate)
            panel.setAction_("colorChanged:")
            panel.setContinuous_(True)
            # Observe the colour panel closing so we can auto-release the hold
            # (so the user doesn't have to click the menu icon to un-stick it).
            try:
                from Foundation import NSNotificationCenter
                nc = NSNotificationCenter.defaultCenter()
                # Remove any prior registration to avoid duplicates.
                try:
                    nc.removeObserver_name_object_(
                        self.delegate, "NSWindowWillCloseNotification", panel)
                except Exception:
                    pass
                nc.addObserver_selector_name_object_(
                    self.delegate, "colorPanelWillClose:",
                    "NSWindowWillCloseNotification", panel)
            except Exception:
                pass
            NSApp.activateIgnoringOtherApps_(True)
            panel.makeKeyAndOrderFront_(None)
        except Exception as e:
            print(f"[apps] _open_color_panel({which!r}) FAILED: {e!r}")

    def color_panel_closed(self):
        """Colour panel closed → release the popover hold and close the panel
        too (matching normal click-away behaviour)."""
        try:
            self.panel.release_hold()
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
            which = getattr(self, "_color_target", "down_light")
            key = self._COLOR_KEYS.get(which, "down_color_light")
            self.settings[key] = hexc
            settings_mod.set_value(key, hexc)
            print(f"[apps] color_changed: {which} -> {key} = {hexc}")
            # Push into the webview live.
            try:
                js = ("(function(){DATA.settings." + key + "='" + hexc + "';"
                      "applyColors();})();")
                self.panel.webview.evaluateJavaScript_completionHandler_(js, None)
            except Exception as e:
                print(f"[apps] color_changed JS push FAILED: {e!r}")
        except Exception as e:
            print(f"[apps] color_changed FAILED: {e!r}")

    def _reset_colors(self):
        """Settings' small reset control — restores all four colours (both
        themes) to the shipped defaults in one action."""
        try:
            defaults = {
                "down_color_light": "#248a3d", "up_color_light": "#e0342a",
                "down_color_dark": "#34c759", "up_color_dark": "#ffd60a",
            }
            for key, val in defaults.items():
                self.settings[key] = val
                settings_mod.set_value(key, val)
            print(f"[apps] _reset_colors: wrote {defaults}")
            try:
                import json
                js = ("(function(){Object.assign(DATA.settings," + json.dumps(defaults)
                      + ");applyColors();if(view==='settings'){renderSettings();}})();")
                self.panel.webview.evaluateJavaScript_completionHandler_(js, None)
            except Exception as e:
                print(f"[apps] _reset_colors JS push FAILED: {e!r}")
        except Exception as e:
            print(f"[apps] _reset_colors FAILED: {e!r}")

    def _apply_setting(self, msg):
        """Handle 'set:key:value' from the settings panel: persist + apply."""
        try:
            _, key, value = msg.split(":", 2)
        except ValueError:
            return
        if key in ("tracking", "show_menu_text", "launch_at_login",
                   "app_tracking", "network_names", "show_system_apps"):
            val = value in ("1", "true", "True")
        elif key in ("transparency", "app_sample_interval"):
            try:
                val = int(value)
            except ValueError:
                return
            if key == "app_sample_interval":
                val = max(5, min(300, val))
        elif key == "menu_number":
            val = value if value in ("down", "up", "total") else "total"
        elif key == "theme":
            val = value if value in ("system", "light", "dark") else "system"
        else:  # colors
            val = value
        self.settings[key] = val
        settings_mod.set_value(key, val)
        # Apply immediately where relevant.
        if key in ("show_menu_text", "menu_number"):
            self.refresh_title()
        elif key == "transparency":
            try:
                self.panel.set_transparency(val)
            except Exception:
                pass
        elif key == "theme":
            try:
                self.panel.set_theme(val)
            except Exception:
                pass
        elif key == "launch_at_login":
            try:
                core.set_launch_at_login(val)
            except Exception:
                pass
        elif key == "network_names":
            # Turning names ON requests Location (needed for SSIDs) and clears
            # the cached label so the next sample re-reads the real name. We also
            # kick an immediate recompute on a background thread so the name
            # updates promptly rather than waiting for the next 30s beat.
            try:
                core.clear_label_cache()
                if val and self._loc_mgr is None:
                    self._loc_mgr = core.request_location_permission()

                def _warm():
                    try:
                        iface = core.active_interface()
                        if iface:
                            core.network_label(iface, allow_compute=True)
                        self._dispatch_main(self._update_ui_main)
                    except Exception:
                        pass
                threading.Thread(target=_warm, daemon=True).start()
            except Exception:
                pass
        elif key == "app_tracking":
            # Sampler loop reads this each beat; nothing else needed here.
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
                which = self.settings.get("menu_number", "total")
                if which == "down":
                    val = down
                elif which == "up":
                    val = up
                else:
                    val = down + up
                text = " " + core.human(val)
                # Monospaced (tabular) figures keep the width stable as digits
                # change, so the menu-bar icon stops shifting left/right.
                self._set_mono_title(btn, text)
            else:
                btn.setTitle_("")
        except Exception:
            pass

    def _set_mono_title(self, btn, text):
        """Set the title with monospaced-digit figures so its width doesn't
        jitter as the number changes (fixes menu-bar icon shifting)."""
        try:
            from AppKit import (NSAttributedString, NSFont, NSFontAttributeName)
            size = 12.0
            try:
                font = NSFont.monospacedDigitSystemFontOfSize_weight_(size, 0.0)
            except Exception:
                font = NSFont.systemFontOfSize_(size)
            astr = NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font})
            btn.setAttributedTitle_(astr)
        except Exception:
            try:
                btn.setTitle_(text)
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

    def _check_updates(self):
        """Run the manual update check on a background thread (it's a network
        request) and push the result into the panel. Only ever called from the
        user's explicit 'Check for updates' click."""
        def _work():
            try:
                from . import updates
                from . import __version__ as ver
            except Exception:
                try:
                    from . import updates
                    ver = dashboard.VERSION
                except Exception:
                    return
            try:
                res = updates.check_for_updates(ver)
            except Exception:
                res = {"status": "error",
                       "message": "Couldn't check for updates."}
            import json as _json

            def _push():
                try:
                    js = ("window.__updateResult && window.__updateResult("
                          + _json.dumps(res) + ");")
                    self.panel.webview.evaluateJavaScript_completionHandler_(
                        js, None)
                except Exception:
                    pass
            self._dispatch_main(_push)
        threading.Thread(target=_work, daemon=True).start()

    def _stop_app_reader(self):
        try:
            if self.app_sampler is not None:
                self.app_sampler.stop()
        except Exception:
            pass

    def uninstall_self(self):
        """Full self-uninstall, triggered from Settings: unregister the
        login item, clear WebKit/cache/preferences/sandbox-container data,
        remove the whole usage-data folder, move the .app to Trash, then
        quit.

        This exists because it's the ONLY correct way to unregister the
        login item at all (SMAppService is bundle-identity-bound — see
        core.set_launch_at_login's docstring and uninstall.sh's header
        comment for the full explanation) — a separate uninstaller script
        or a third-party tool can delete files, but can never do this one
        part correctly. Mirrors uninstall.sh's per-app cleanup for
        everything else, so the two stay equivalent.

        Removes the usage-data folder (2026-09-23) — Uninstall and "Erase
        all data" are now deliberately different actions: Erase all data
        wipes history while keeping the app installed (a fresh start);
        Uninstall means the app is leaving the Mac entirely, and a real
        user expects that to mean nothing is left behind, settings.json
        included. Before this change a new install after Uninstall would
        silently inherit the previous install's entire history and
        settings — not what "uninstall" means to someone using it.

        Still deliberately does NOT touch: the shared Background Task
        Management registry (no app, including this one, can clear its own
        entry from that — see uninstall.sh's notes).

        Every step is logged to ~/Library/Logs/NUM/last_uninstall.log —
        deliberately OUTSIDE the data folder this method deletes, so the
        log survives to actually be read afterward, instead of erasing its
        own diagnostic trail along with everything else. An earlier
        version of this method swallowed every exception with a bare
        `except Exception: pass` around each step — the same anti-pattern
        this project already burned real time on once before (see
        [[Lessons - macOS Menu Bar Apps]] → "Testing and diagnosis") —
        which is exactly how a partial failure could go unnoticed.
        """
        import subprocess
        import time

        log_lines = []

        def _log(msg):
            log_lines.append(msg)

        def _work():
            _log(f"Uninstall started {time.strftime('%Y-%m-%d %H:%M:%S')}")

            try:
                ok = core.set_launch_at_login(False)
                _log(f"set_launch_at_login(False) -> {ok}")
            except Exception as e:
                _log(f"set_launch_at_login FAILED: {e!r}")

            bundle_id = None
            try:
                from Foundation import NSBundle
                bundle_id = NSBundle.mainBundle().bundleIdentifier()
                _log(f"bundleIdentifier() -> {bundle_id!r}")
            except Exception as e:
                _log(f"bundleIdentifier() FAILED: {e!r}")

            if bundle_id:
                def _rm(p):
                    try:
                        r = subprocess.run(["rm", "-rf", p], check=False,
                                            capture_output=True, text=True)
                        _log(f"rm -rf {p} -> exit {r.returncode} "
                             f"{r.stderr.strip()}")
                    except Exception as e:
                        _log(f"rm -rf {p} FAILED TO RUN: {e!r}")
                _rm(str(Path.home() / "Library" / "WebKit" / bundle_id))
                _rm(str(Path.home() / "Library" / "Caches" / bundle_id))
                _rm(str(Path.home() / "Library" / "Preferences"
                        / f"{bundle_id}.plist"))
                for var in ("DARWIN_USER_CACHE_DIR", "DARWIN_USER_TEMP_DIR"):
                    try:
                        root = subprocess.check_output(
                            ["getconf", var], text=True).strip()
                        _log(f"getconf {var} -> {root!r}")
                        if root:
                            _rm(root + bundle_id)
                    except Exception as e:
                        _log(f"getconf {var} FAILED: {e!r}")
            else:
                _log("No bundle_id — skipped WebKit/Caches/Preferences/"
                     "container cleanup entirely (this is the bug to find "
                     "if those are still showing up after uninstall).")

            # Move the .app to Trash — same as dragging it there in Finder,
            # just triggered from inside itself. NOT a hard delete, so it's
            # reversible the same way any other Trash item is.
            try:
                if self._own_bundle_path:
                    from Foundation import NSURL, NSFileManager
                    fm = NSFileManager.defaultManager()
                    url = NSURL.fileURLWithPath_(self._own_bundle_path)
                    ok, result_url, err = \
                        fm.trashItemAtURL_resultingItemURL_error_(
                            url, None, None)
                    _log(f"trash {self._own_bundle_path} -> "
                         f"ok={ok} err={err}")
                else:
                    _log("No _own_bundle_path — could not trash the app.")
            except Exception as e:
                _log(f"Trash FAILED: {e!r}")

            # Remove the whole usage-data folder LAST, after everything
            # above (including the app-bundle trash) has already
            # succeeded-or-failed and been logged — this is a hard delete
            # (not Trash), matching "uninstall means nothing is left", and
            # deliberately the final step since nothing after this point
            # can depend on DATA_DIR existing.
            try:
                data_dir = core.DATA_DIR
                r = subprocess.run(["rm", "-rf", str(data_dir)],
                                    check=False, capture_output=True, text=True)
                _log(f"rm -rf {data_dir} (usage data) -> exit "
                     f"{r.returncode} {r.stderr.strip()}")
            except Exception as e:
                _log(f"Removing usage-data folder FAILED: {e!r}")

            # Write the log to a location OUTSIDE the data folder just
            # deleted above, so it actually survives to be read.
            try:
                log_dir = Path.home() / "Library" / "Logs" / "NUM"
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "last_uninstall.log").write_text(
                    "\n".join(log_lines) + "\n")
            except Exception:
                pass

            # Brief pause so the "Uninstalling…" message is actually seen
            # before the app vanishes, then quit on the main thread.
            time.sleep(0.6)
            try:
                self._dispatch_main(self.quit_app)
            except Exception:
                try:
                    self.quit_app()
                except Exception:
                    pass

        threading.Thread(target=_work, daemon=True).start()

    def quit_app(self):
        try:
            NSApp.terminate_(None)
        except Exception:
            import os
            os._exit(0)


def main():
    import os
    # Quiet cleanup mode for uninstall.sh: SMAppService is bundle-identity-
    # bound, so unregistering "launch at login" can only be done correctly by
    # this app's OWN code running from its OWN bundle — an external script
    # can't fake that. uninstall.sh launches the real binary with this env
    # var set, purely so this one call happens with the right identity,
    # then exits immediately with no status item, no popover, no run loop.
    if os.environ.get("NUM_UNINSTALL_CLEANUP") == "1":
        try:
            core.set_launch_at_login(False)
        except Exception:
            pass
        return

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
