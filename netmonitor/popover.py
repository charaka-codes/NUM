"""
popover.py — a native macOS popover that shows the dashboard HTML in a
WKWebView, anchored under the menu bar icon.

This is deliberately defensive: every AppKit import and call is wrapped so that
if the popover can't be created on a given system, the app keeps running and
the normal menu still works. The menu item calls toggle(status_item_button).
"""

from . import core
from . import dashboard

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
        try:
            self.popover.setAppearance_(
                NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
        except Exception:
            pass

    def hold_open(self):
        """Stop the popover auto-closing (used while the native colour panel is
        focused, which would otherwise dismiss this transient popover)."""
        try:
            from AppKit import NSPopoverBehaviorApplicationDefined
            if self.popover is not None:
                self.popover.setBehavior_(NSPopoverBehaviorApplicationDefined)
        except Exception:
            pass

    def release_hold(self):
        """Restore normal click-away-to-close behavior."""
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
        try:
            import json
            from datetime import datetime
            core.refresh_view(self.con)
            down_bps, up_bps = core.recent_speed(self.con, seconds=8)
            iface = core.active_interface()
            net = core.network_label(iface) if iface else "offline"
            payload = {
                "daily": self._daily(),
                "networks": self._networks(),
                "net": net,
                "downBps": down_bps,
                "upBps": up_bps,
                "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            js = "window.__updateData(" + json.dumps(payload) + ");"
            self.webview.evaluateJavaScript_completionHandler_(js, None)
            self._resize_to_content()
        except Exception:
            # Fall back to a full reload if JS injection isn't available
            try:
                self._load_html()
            except Exception:
                pass

    def _daily(self):
        rows = self.con.execute(
            "SELECT substr(ts,1,10) d, SUM(down), SUM(up) FROM usage GROUP BY d"
        ).fetchall()
        return {d: [dn, up] for d, dn, up in rows}

    def _networks(self):
        rows = self.con.execute(
            "SELECT substr(ts,1,10) d, network, SUM(down), SUM(up) FROM usage "
            "GROUP BY d, network ORDER BY d, SUM(down)+SUM(up) DESC").fetchall()
        out = {}
        for d, net, dn, up in rows:
            out.setdefault(d, []).append([net, dn, up])
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
                self.popover.showRelativeToRect_ofView_preferredEdge_(
                    button.bounds(), button, 1)  # 1 = below
                # Give the web view a moment to render, then size to content.
                self._schedule_resize()
            return True
        except Exception:
            return False

    def _schedule_resize(self):
        """Resize shortly after showing (content needs a beat to lay out)."""
        try:
            from Foundation import NSTimer

            class _R:
                pass
            # Fire a few times over the first second to catch layout settling.
            for delay in (0.15, 0.4, 0.8):
                try:
                    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                        delay, False, lambda t: self._resize_to_content())
                except Exception:
                    # block-based timer may be unavailable; do one direct call
                    self._resize_to_content()
                    break
        except Exception:
            self._resize_to_content()
