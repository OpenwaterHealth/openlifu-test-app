"""Headless-ish screenshot helper for the Open-LIFU Test App docs.

Boots the app via main.main() inside a single QGuiApplication, walks the
sidebar tabs by writing to ``window.activeMenu``, grabs each tab with
``QQuickWindow.grabWindow()``, and saves PNGs into ``docs/``.

Run modes
---------
- ``--simulate``  : capture Vet + Controller (the only tabs visible
  under the simulator).
- ``--disconnected`` : run without ``--simulate`` so Console,
  Transmitter, Verification, Settings are visible (in disconnected
  state) and grab those.

Usage::

    python docs/_capture_screenshots.py --simulate
    python docs/_capture_screenshots.py --disconnected

"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Make sure the workspace root is on sys.path when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtQuick import QQuickWindow  # noqa: E402

# Import after sys.path patch.
import main as app_main  # noqa: E402


# Filenames must match the references in the *.md docs.
TAB_TO_FILENAME = {
    "vet":         "vet_tab.png",
    "controller":  "controller_tab.png",
    "transmitter": "transmitter_tab.png",
    "console":     "console_tab.png",
    "testing":     "testing_tab.png",
    "settings":    "settings_tab.png",
}


def _build_argv(mode: str, simulate: bool) -> list[str]:
    argv = ["main.py", "--mode", mode]
    if simulate:
        argv += ["--simulate", "1"]
    return argv


def capture(mode: str, simulate: bool, settle_ms: int = 8000,
            per_tab_ms: int = 3500,
            skip_tabs: set[str] | None = None) -> None:
    """Boot the app, walk visible tabs, save screenshots, then quit."""
    sys.argv = _build_argv(mode, simulate)
    args = app_main.parse_arguments()

    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Material"
    os.environ["QT_QUICK_CONTROLS_MATERIAL_THEME"] = "Dark"

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    # Wire up qasync the same way main.py does so the connector's
    # asyncio.ensure_future(start_monitoring(...)) call has a running
    # event loop to attach to. Without this, the simulator never
    # auto-connects and every screenshot ends up in the disconnected
    # state.
    from qasync import QEventLoop
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    from PyQt6.QtQml import QQmlApplicationEngine
    from lifu.lifu_connector import LIFUConnector
    from lifu.lifu_support import LIFUSupportConnector

    simulating = args.simulate is not None
    if simulating:
        from lifu.simulated_lifu_connector import SimulatedLIFUConnector
        sim_modules = 1 if args.mode == "vet" else args.simulate
        print(f"Initializing simulated connector with {sim_modules} module(s)...")
        connector = SimulatedLIFUConnector(num_modules=sim_modules)
    else:
        connector = LIFUConnector(hv_test_mode=False)
    support = LIFUSupportConnector(interface=connector.interface)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("LIFUConnector", connector)
    engine.rootContext().setContextProperty("LIFUSupportConnector", support)
    engine.rootContext().setContextProperty("appVersion", app_main.APP_VERSION)
    engine.rootContext().setContextProperty("appMode", args.mode)
    engine.rootContext().setContextProperty("appSimulate", simulating)
    tabs = app_main.build_app_tabs(args.mode, simulate=simulating)
    if skip_tabs:
        tabs = [t for t in tabs if t["id"] not in skip_tabs]
    engine.rootContext().setContextProperty("appTabs", tabs)

    engine.load(app_main.resource_path("main.qml"))
    if not engine.rootObjects():
        print("Failed to load main.qml", file=sys.stderr)
        sys.exit(1)

    window = engine.rootObjects()[0]
    print(f"Tabs to capture: {[t['id'] for t in tabs]}")

    state = {"index": 0, "started": time.time()}

    def stop():
        loop.stop()

    def _walk_all_items():
        """Combined QObject + visual childItems() walk so we reach the
        Loader-instantiated page items too (those aren't reachable via
        findChildren on the window alone)."""
        from PyQt6.QtCore import QObject
        out = list(window.findChildren(QObject))
        seen = {id(o) for o in out}
        try:
            content = window.contentItem() if hasattr(window, "contentItem") else None
        except Exception:
            content = None
        if content is None:
            return out
        stack = [content]
        visited = set()
        while stack:
            it = stack.pop()
            if id(it) in visited:
                continue
            visited.add(id(it))
            if isinstance(it, QObject) and id(it) not in seen:
                out.append(it)
                seen.add(id(it))
            if hasattr(it, "childItems"):
                for ch in it.childItems():
                    stack.append(ch)
        return out

    def _find_loader_by_index(idx):
        """Return the loaded item for the StackLayout's Loader at index."""
        from PyQt6.QtCore import QObject
        loaders = [c for c in _walk_all_items()
                    if c.metaObject().className() == "QQuickLoader"]
        if idx < 0 or idx >= len(loaders):
            return None
        return loaders[idx].property("item")

    def _find_loader_for_tab(tab_id):
        """Find the Loader-instantiated page item matching the tab id by
        inspecting each Loader's ``source`` URL (page filename). The
        order returned by ``_walk_all_items()`` doesn't necessarily
        match the Repeater's model order, so indexing into the loader
        list is unreliable."""
        from PyQt6.QtCore import QUrl
        # Map tab id -> page filename used in build_app_tabs().
        tab_to_filename = {t["id"]: os.path.basename(t["page"]) for t in tabs}
        target = tab_to_filename.get(tab_id, "").lower()
        if not target:
            return None
        for c in _walk_all_items():
            if c.metaObject().className() != "QQuickLoader":
                continue
            src = c.property("source")
            src_str = src.toString() if isinstance(src, QUrl) else str(src)
            if src_str.lower().endswith(target):
                return c.property("item")
        return None

    def _items_under(root):
        out = []
        if root is None or not hasattr(root, "childItems"):
            return out
        stack = [root]
        seen = set()
        while stack:
            it = stack.pop()
            if id(it) in seen:
                continue
            seen.add(id(it))
            out.append(it)
            for ch in it.childItems():
                stack.append(ch)
        return out

    def _force_plot_refresh(tab_id):
        """Push the page-specific plot to a known-good state right
        before grabbing.

        - Vet:        invoke the page's QML ``refreshPlot()`` directly via
                      QMetaObject so the proper updateImage path runs
                      against the now-populated ``presetOptions`` model.
        - Controller: clear any active Vet preset (so generate_plot
                      doesn't fall into the preset-validation branch
                      with mismatched module count), then ask the
                      connector to render a plot from the page defaults.
        """
        from PyQt6.QtCore import QMetaObject, Qt
        try:
            page_root = _find_loader_for_tab(tab_id)
            if tab_id == "vet":
                if page_root is None:
                    print("  [refresh] vet page not loaded")
                    return
                ok = QMetaObject.invokeMethod(
                    page_root, "refreshPlot",
                    Qt.ConnectionType.DirectConnection,
                )
                print(f"  [refresh] invoked Vet.refreshPlot() ok={ok}")
            elif tab_id == "controller":
                # The Vet page's Component.onCompleted activates a Vet
                # preset on the connector. Generating the Controller's
                # default-solution plot in that state goes through the
                # preset-validation branch and trips a 1D/2D shape
                # mismatch when num_modules != preset modules. Clear
                # the preset first so we get the plain pinmap path.
                if hasattr(connector, "clearActiveVetPreset"):
                    connector.clearActiveVetPreset()
                connector.generate_plot(
                    "0", "0", "50", "400", "12.0", "100", "1",
                    "1", "1", "200", "buffer"
                )
                print("  [refresh] requested Controller plot generation")
        except Exception as e:
            print(f"  [refresh] {tab_id} failed: {e}")

    def grab_current_tab():
        idx = state["index"]
        if idx >= len(tabs):
            print("All tabs captured. Quitting.")
            QTimer.singleShot(200, stop)
            return
        tab_id = tabs[idx]["id"]
        filename = TAB_TO_FILENAME.get(tab_id)
        if filename is None:
            print(f"  no filename mapping for {tab_id}, skipping")
            state["index"] += 1
            QTimer.singleShot(50, schedule_next)
            return
        # QQuickWindow.grabWindow() needs to be called on the typed
        # QQuickWindow instance. PyQt6's wrapper exposes it on the
        # base class.
        out = os.path.join(_HERE, filename)
        _force_plot_refresh(tab_id)
        # Give the Image element a moment to load the new source.
        QTimer.singleShot(800, lambda: _do_grab(tab_id, out))

    def _do_grab(tab_id, out):
        qq = window if isinstance(window, QQuickWindow) else None
        if qq is None:
            screen = window.screen() if hasattr(window, "screen") else QGuiApplication.primaryScreen()
            img = screen.grabWindow(int(window.winId())).toImage()
        else:
            img = qq.grabWindow()
        ok = img.save(out, "PNG")
        print(f"  [{ 'OK' if ok else 'FAIL' }] {tab_id} -> {out}")
        state["index"] += 1
        QTimer.singleShot(per_tab_ms, schedule_next)

    def schedule_next():
        idx = state["index"]
        if idx >= len(tabs):
            grab_current_tab()
            return
        # Switch the active tab and let the UI render before grabbing.
        window.setProperty("activeMenu", idx)
        QTimer.singleShot(per_tab_ms, grab_current_tab)

    # Initial settle: let the simulator auto-connect (~500 ms) and the
    # first page fully render.
    QTimer.singleShot(settle_ms, schedule_next)

    # Safety net: don't run forever if something hangs.
    QTimer.singleShot(settle_ms + (per_tab_ms * 2 * (len(tabs) + 2)),
                      stop)

    with loop:
        loop.run_forever()


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--simulate", action="store_true",
                   help="Capture Vet+Controller from the simulator.")
    g.add_argument("--disconnected", action="store_true",
                   help="Capture all engineering tabs without simulator "
                        "(disconnected state).")
    args = p.parse_args()
    if args.simulate:
        capture(mode="all", simulate=True)
    else:
        # Skip the tabs already captured in simulate mode so a
        # follow-up disconnected run doesn't overwrite their
        # connected-state screenshots with disconnected ones.
        capture(mode="default", simulate=False, skip_tabs={"vet", "controller"})


if __name__ == "__main__":
    main()
