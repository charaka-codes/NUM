"""
popover.py — a native macOS popover that shows the dashboard HTML in a
WKWebView, anchored under the menu bar icon.

This is deliberately defensive: every AppKit import and call is wrapped so that
if the popover can't be created on a given system, the app keeps running and
the normal menu still works. The menu item calls toggle(status_item_button).
"""

from . import core
from . import dashboard
from . import settings as settings_mod

# All AppKit/WebKit imports are attempted lazily so a failure here never
# crashes the whole app — the menu bar keeps working without the panel.
_AVAILABLE = True
try:
    from AppKit import (NSPopover, NSPopoverBehaviorTransient, NSViewController,
                        NSView, NSVisualEffectView, NSAppearance,
                        NSVisualEffectBlendingModeBehindWindow,
                        NSVisualEffectStateActive)
    from WebKit import (WKWebView, WKWebViewConfiguration,
                        WKUserContentController)
    from Foundation import NSMakeRect, NSObject
    import objc
    _HAS_VIBRANCY = True
except Exception:
    _HAS_VIBRANCY = False
    try:
        from AppKit import (NSPopover, NSPopoverBehaviorTransient,
                            NSViewController, NSView)
        from WebKit import (WKWebView, WKWebViewConfiguration,
                            WKUserContentController)
        from Foundation import NSMakeRect, NSObject
        import objc
    except Exception:
        _AVAILABLE = False


if _AVAILABLE:
    class _ActionHandler(NSObject):
        """Receives button messages from the web page and runs a callback."""

        def initWithCallback_(self, cb):
            self = objc.super(_ActionHandler, self).init()
            if self is None:
                return None
            self._cb = cb
            return self

        def userContentController_didReceiveScriptMessage_(self, ucc, message):
            try:
                self._cb(str(message.body()))
            except Exception:
                pass
else:
    _ActionHandler = None


class DashboardPopover:
    """Wraps an NSPopover containing a web view that renders the dashboard."""

    WIDTH = 380
    HEIGHT = 560

    def __init__(self, con, action_cb=None, transparency=60):
        self.con = con
        self.action_cb = action_cb  # called with a string when a panel button is tapped
        self.transparency = transparency
        self.popover = None
        self.webview = None
        self._handler = None
        self._ok = _AVAILABLE
        if self._ok:
            try:
                self._build()
            except Exception:
                self._ok = False

    def set_transparency(self, value):
        """Adjust the vibrancy material alpha to match the user's setting.
        The HTML also tints itself; here we just keep a reference so future
        builds use it. Live effect comes mostly from the HTML --outer var."""
        self.transparency = value

    def _apply_theme_appearance(self, theme):
        """Set the popover's own NSAppearance. Material 6 (popover glass,
        set in _build()) is ADAPTIVE — it already renders its own light or
        dark vibrancy depending on the effective appearance it's drawn
        under, the same way the real system popovers (Bluetooth, Control
        Center) do. So this appearance is the only native lever needed:
        force Aqua/DarkAqua for an explicit choice, or clear it (None) to
        just inherit from the app's effective appearance, i.e. follow the
        system automatically."""
        try:
            if theme == "light":
                appearance = NSAppearance.appearanceNamed_("NSAppearanceNameAqua")
            elif theme == "dark":
                appearance = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            else:
                appearance = None
            self.popover.setAppearance_(appearance)
        except Exception:
            pass

    def set_theme(self, theme):
        """Called live when the user changes the Theme setting (or once at
        startup with the persisted value). Updates the native vibrancy
        appearance AND pushes the choice into the page so its CSS matches
        immediately rather than waiting for the next ~1s live refresh."""
        self.theme = theme
        self._apply_theme_appearance(theme)
        try:
            import json
            js = ("(function(){DATA.settings.theme=" + json.dumps(theme) + ";"
                  "if(typeof applyTheme==='function'){applyTheme();}"
                  "if(typeof applyColors==='function'){applyColors();}})();")
            self.webview.evaluateJavaScript_completionHandler_(js, None)
        except Exception:
            pass

    def available(self):
        return self._ok

    def _make_transparent(self):
        """Aggressively clear the web view's background. Called at build and
        again after each load, because WKWebView resets it when content loads."""
        wv = self.webview
        for setter in ("setDrawsBackground_", "_setDrawsBackground_"):
            try:
                getattr(wv, setter)(False)
            except Exception:
                pass
        try:
            wv.setValue_forKey_(False, "drawsBackground")
        except Exception:
            pass
        try:
            wv.setOpaque_(False)
        except Exception:
            pass
        # Clear the backing layer + the enclosing scroll/clip views that
        # actually paint the grey.
        try:
            from AppKit import NSColor
            clear = NSColor.clearColor()
            try:
                wv.setBackgroundColor_(clear)
            except Exception:
                pass
            try:
                wv.layer().setBackgroundColor_(clear.CGColor())
            except Exception:
                pass
            try:
                sv = wv.enclosingScrollView()
                if sv is not None:
                    sv.setDrawsBackground_(False)
                    sv.setBackgroundColor_(clear)
            except Exception:
                pass
            # WKWebView hosts a scroll view internally; walk subviews and
            # disable any that draw a background.
            try:
                for v in wv.subviews():
                    try:
                        v.setDrawsBackground_(False)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

    def _build(self):
        rect = NSMakeRect(0, 0, self.WIDTH, self.HEIGHT)

        config = WKWebViewConfiguration.alloc().init()

        # Bridge: let the web page send button taps back to Python via
        # window.webkit.messageHandlers.action.postMessage("quit") etc.
        if _ActionHandler is not None and self.action_cb is not None:
            try:
                self._handler = _ActionHandler.alloc().initWithCallback_(
                    self.action_cb)
                ucc = config.userContentController()
                ucc.addScriptMessageHandler_name_(self._handler, "action")
            except Exception:
                self._handler = None

        self.webview = WKWebView.alloc().initWithFrame_configuration_(rect, config)
        self._make_transparent()

        # ONE native frosted layer provides the outer glass background. The HTML
        # no longer draws its own panel frost (that was the redundant middle
        # layer), so the structure is now just: this vibrancy view → cards.
        if _HAS_VIBRANCY:
            effect = NSVisualEffectView.alloc().initWithFrame_(rect)
            effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(NSVisualEffectStateActive)
            try:
                effect.setMaterial_(6)  # popover glass, light
            except Exception:
                pass
            effect.setAutoresizingMask_(18)
            self.webview.setFrame_(effect.bounds())
            self.webview.setAutoresizingMask_(18)
            effect.addSubview_(self.webview)
            container = effect
        else:
            container = NSView.alloc().initWithFrame_(rect)
            try:
                container.setWantsLayer_(True)
                from AppKit import NSColor
                container.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
            except Exception:
                pass
            self.webview.setFrame_(container.bounds())
            self.webview.setAutoresizingMask_(18)
            container.addSubview_(self.webview)

        vc = NSViewController.alloc().init()
        vc.setView_(container)

        self.popover = NSPopover.alloc().init()
        self.popover.setContentSize_((self.WIDTH, self.HEIGHT))
        self.popover.setBehavior_(NSPopoverBehaviorTransient)
        self.popover.setContentViewController_(vc)
        # Match the user's Theme setting (System/Light/Dark) at startup.
        # 'system' means leave this alone entirely, letting the popover just
        # inherit its effective appearance the normal way.
        self.theme = "system"
        try:
            self.theme = settings_mod.load().get("theme", "system")
        except Exception:
            pass
        self._apply_theme_appearance(self.theme)

    def hold_open(self):
        """Stop the popover auto-closing (used while the native colour panel is
        focused, which would otherwise dismiss this transient popover)."""
        self._held = True
        try:
            from AppKit import NSPopoverBehaviorApplicationDefined
            if self.popover is not None:
                self.popover.setBehavior_(NSPopoverBehaviorApplicationDefined)
        except Exception:
            pass

    def release_hold(self):
        """Restore normal click-away-to-close behavior."""
        self._held = False
        try:
            if self.popover is not None:
                self.popover.setBehavior_(NSPopoverBehaviorTransient)
        except Exception:
            pass

    def _load_html(self):
        """Regenerate the dashboard HTML and load it into the web view."""
        html = dashboard.build_html(self.con)
        # baseURL None is fine — the HTML is fully self-contained (no external files)
        self.webview.loadHTMLString_baseURL_(html, None)
        # WKWebView resets its background when new content loads, so clear it
        # again right after kicking off the load.
        self._make_transparent()

    def is_open(self):
        try:
            return bool(self.popover is not None and self.popover.isShown())
        except Exception:
            return False

    def refresh(self):
        """Push fresh data into the open popover without a full reload, so the
        user's current tab/selection is preserved."""
        if not self.is_open():
            return
        import os
        _dbg = os.environ.get("NUM_DEBUG")
        try:
            import json, time
            from datetime import datetime
            _t0 = time.perf_counter()
            core.refresh_view(self.con)
            down_bps, up_bps = core.recent_speed(self.con, seconds=8)
            iface = core.active_interface()
            net = core.network_label(iface, allow_compute=False) if iface else "offline"
            _t1 = time.perf_counter()
            payload = {
                "daily": self._daily(),
                "networks": self._networks(),
                "net": net,
                "downBps": down_bps,
                "upBps": up_bps,
                "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # Ship the network list on every refresh so Settings reflects a
            # rename/exclude/delete immediately instead of only after a restart.
            try:
                try:
                    from . import dashboard as _dash
                except Exception:
                    import dashboard as _dash
                payload["knownNetworks"] = _dash._networks_with_totals(self.con)
            except Exception:
                pass
            _t2 = time.perf_counter()
            # Per-app data cached; recomputed at most every ~20s.
            try:
                from . import apps as apps_mod
                from datetime import datetime as _dt
                now_t = time.time()
                last = getattr(self, "_appdata_t", 0)
                # ALWAYS keep the current month fresh — it is the only live one,
                # and the panel reads it straight from this payload (never from
                # its cache). Past months are sealed and are served once, on
                # demand, by request_app_month() below.
                #
                # Recompute a little faster than the per-app sampler writes, so
                # a new reading shows up promptly instead of waiting out a
                # second full interval on top of the sampling one.
                try:
                    from . import settings as _st
                except Exception:
                    import settings as _st
                _iv = int(_st.load().get("app_sample_interval", 30) or 30)
                every = max(5, min(10, _iv))
                mon = _dt.now().strftime("%Y-%m")
                if (now_t - last) > every or getattr(self, "_appdata", None) is None:
                    self._appdata = apps_mod.app_dashboard_payload(
                        self.con, "month", mon, top=25)
                    self._appdata_t = now_t
                if getattr(self, "_appdata", None) is not None:
                    payload["appData"] = self._appdata
            except Exception:
                pass
            _t3 = time.perf_counter()
            js = "window.__updateData(" + json.dumps(payload) + ");"
            self.webview.evaluateJavaScript_completionHandler_(js, None)
            _t4 = time.perf_counter()
            self._resize_to_content()
            _t5 = time.perf_counter()
            if _dbg:
                print(f"[refresh] net/speed={ (_t1-_t0)*1000:.0f}ms "
                      f"daily/networks={ (_t2-_t1)*1000:.0f}ms "
                      f"appdata={ (_t3-_t2)*1000:.0f}ms "
                      f"evalJS={ (_t4-_t3)*1000:.0f}ms "
                      f"resize={ (_t5-_t4)*1000:.0f}ms", flush=True)
        except Exception:
            # Fall back to a full reload if JS injection isn't available
            try:
                self._load_html()
            except Exception:
                pass

    def request_all_time(self):
        """All-time tab's Generate button. Scans every month file once (cheap
        by this store's design — see core.all_time_total()), caches the
        result to settings so it survives a relaunch, and pushes it to the
        panel. Deliberately NOT part of the ~1s live refresh loop — that's
        the whole point of a Generate button rather than a live number."""
        try:
            import json
            from datetime import datetime as _dt
            try:
                from . import settings as settings_mod
            except Exception:
                import settings as settings_mod
            d, u, first_day, months = core.all_time_total()
            cache = {
                "down": d, "up": u, "first_day": first_day, "months": months,
                "generated_at": _dt.now().isoformat(timespec="seconds"),
            }
            settings_mod.set_value("alltime_cache", cache)
            js = ("(function(){DATA.settings.alltime_cache=" + json.dumps(cache)
                  + ";if(typeof tab!=='undefined'&&tab==='alltime'&&"
                    "typeof renderAllTime==='function'){renderAllTime();}})();")
            self.webview.evaluateJavaScript_completionHandler_(js, None)
        except Exception:
            pass

    def request_app_month(self, month):
        """Panel asked for a specific month's per-app data.

        The current month is live and refreshed on the slow beat. Any earlier
        month is sealed — its numbers can never change — so it is read once,
        pushed to the panel, and the panel keeps it. No timer ever touches it
        again."""
        try:
            import json
            from datetime import datetime as _dt
            try:
                from . import apps as apps_mod
            except Exception:
                import apps as apps_mod
            self._appmonth = month
            payload = apps_mod.app_dashboard_payload(
                self.con, "month", month, top=25)
            payload["sealed"] = (month != _dt.now().strftime("%Y-%m"))
            js = "window.__appMonthData(" + json.dumps(payload) + ");"
            self.webview.evaluateJavaScript_completionHandler_(js, None)
        except Exception:
            pass

    def _daily(self):
        """Per-day totals straight from the month rollups (pre-aggregated)."""
        st = core._store()
        ex = core.excluded_fingerprints()
        out = {}
        for day, net, dn, up in st.daily_rows():
            if net in ex:
                continue
            cur = out.get(day, [0, 0])
            cur[0] += dn or 0
            cur[1] += up or 0
            out[day] = cur
        return out

    def _networks(self):
        st = core._store()
        rows = [(day, net, dn, up) for day, net, dn, up in st.daily_rows()]
        # Group by day, then resolve fingerprint keys to display names and
        # re-aggregate (several fingerprints can map to one name, e.g. 'Home').
        byday = {}
        for d, net, dn, up in rows:
            byday.setdefault(d, []).append((net, dn, up))
        out = {}
        for d, day_rows in byday.items():
            resolved = core._resolve_rows(day_rows)
            out[d] = [[n, dn, up] for (n, dn, up) in resolved]
        return out

    def _resize_to_content(self):
        """Size the popover to the actual content height. Measures the panel
        element (not body scrollHeight, which can stay stuck at the old larger
        value when shrinking, e.g. Year->Day)."""
        try:
            def handler(result, error):
                try:
                    h = float(result)
                    if h and h > 80:
                        maxh = 1100.0
                        try:
                            from AppKit import NSScreen
                            vh = NSScreen.mainScreen().visibleFrame().size.height
                            maxh = max(300.0, vh * 0.9)
                        except Exception:
                            pass
                        h = max(160.0, min(h, maxh))
                        # Remember for this session AND persist, so the panel
                        # opens at the right size next launch too — however many
                        # networks the user has. Fully dynamic; this is just the
                        # opening size, still measured and re-fitted every time.
                        if abs(getattr(self, "_last_height", 0) - h) > 4:
                            self._last_height = h
                            try:
                                settings_mod.set_value("panel_height", int(h))
                            except Exception:
                                pass
                        self.popover.setContentSize_((self.WIDTH, h))
                except Exception:
                    pass
            # Measure the panel's real rendered height + body padding. Using
            # the element's bounding box (not scrollHeight) lets the panel
            # shrink when switching to a shorter view.
            self.webview.evaluateJavaScript_completionHandler_(
                "(function(){var p=document.querySelector('.panel');"
                "var b=document.body;"
                "var ph=p?Math.ceil(p.getBoundingClientRect().height):b.scrollHeight;"
                "var pad=parseFloat(getComputedStyle(b).paddingTop)+parseFloat(getComputedStyle(b).paddingBottom);"
                "return ph+pad;})()",
                handler)
        except Exception:
            pass

    def toggle(self, button):
        """Show the popover under `button`, or hide it if already shown."""
        if not self._ok or self.popover is None or button is None:
            return False
        try:
            if self.popover.isShown():
                self.popover.performClose_(None)
            else:
                self._load_html()
                # Open at the last known good height (if we have one) so the
                # panel appears at the right size immediately instead of opening
                # tall (560) and visibly shrinking. First-ever open uses a
                # conservative default close to typical content.
                try:
                    h0 = getattr(self, "_last_height", None)
                    if not h0:
                        # First open this session: use the persisted height from
                        # a previous launch if we have one, else a default.
                        try:
                            h0 = int(settings_mod.load().get("panel_height", 0)) or 420
                        except Exception:
                            h0 = 420
                        self._last_height = h0
                    self.popover.setContentSize_((self.WIDTH, h0))
                except Exception:
                    pass
                self.popover.showRelativeToRect_ofView_preferredEdge_(
                    button.bounds(), button, 1)  # 1 = below
                # Activate the app and make the web view the first responder so
                # the FIRST click inside the panel (e.g. a tab) registers as a
                # real click, instead of just focusing the panel (which caused a
                # "click twice to switch tab" bug).
                try:
                    from AppKit import NSApp
                    NSApp.activateIgnoringOtherApps_(True)
                    if self.webview is not None and self.popover.contentViewController():
                        w = self.webview.window()
                        if w is not None:
                            w.makeFirstResponder_(self.webview)
                except Exception:
                    pass
                # Give the web view a moment to render, then size to content.
                self._schedule_resize()
                # Start watching for clicks outside the popover so it closes like
                # a normal menu-bar panel. Accessory (menu-bar-only) apps don't
                # always get the outside click that would auto-dismiss a transient
                # popover, so we close it ourselves.
                self._install_outside_monitor()
            return True
        except Exception:
            return False

    def _install_outside_monitor(self):
        """Install a global mouse-down monitor that closes the popover on an
        outside click. Removed automatically when the popover closes."""
        try:
            from AppKit import (NSEvent, NSEventMaskLeftMouseDown,
                                NSEventMaskRightMouseDown)
            # Remove any prior monitor first.
            self._remove_outside_monitor()

            def _on_click(event):
                try:
                    # Respect hold_open (e.g. during native colour picking) —
                    # don't close while explicitly held.
                    if getattr(self, "_held", False):
                        return
                    if self.popover is not None and self.popover.isShown():
                        # Any click that this global monitor sees is outside our
                        # app's popover (global monitors only fire for events
                        # going to OTHER apps), so close.
                        self.popover.performClose_(None)
                        self._remove_outside_monitor()
                except Exception:
                    pass

            mask = NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown
            self._outside_monitor = \
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    mask, _on_click)
        except Exception:
            self._outside_monitor = None

    def _remove_outside_monitor(self):
        try:
            from AppKit import NSEvent
            mon = getattr(self, "_outside_monitor", None)
            if mon is not None:
                NSEvent.removeMonitor_(mon)
                self._outside_monitor = None
        except Exception:
            pass

    def _schedule_resize(self):
        """Resize shortly after showing (content needs a beat to lay out)."""
        try:
            from Foundation import NSTimer

            class _R:
                pass
            # Fire the FIRST resize almost immediately so any correction happens
            # before it's visible, then a few more to catch layout settling.
            # Also do one synchronous attempt right now.
            try:
                self._resize_to_content()
            except Exception:
                pass
            for delay in (0.02, 0.06, 0.15, 0.4):
                try:
                    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                        delay, False, lambda t: self._resize_to_content())
                except Exception:
                    self._resize_to_content()
                    break
        except Exception:
            self._resize_to_content()
