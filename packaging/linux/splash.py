#!/usr/bin/env python3
"""Minimal GTK splash window for KreuzbergParser (.deb / Linux desktop).

Run with system Python (``/usr/bin/python3`` + ``python3-gi``), not the
bundled venv — the venv may lack PyGObject.

Usage:
    python3 splash.py --image /path/to/logo.png [--title KreuzbergParser]
                      [--message "Iniciando…"] [--ready-file /tmp/ready]

Exits when the ready-file appears, on SIGTERM/SIGINT, or when the window
is closed.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="KreuzbergParser startup splash")
    parser.add_argument("--image", required=True, help="Path to PNG logo")
    parser.add_argument("--title", default="KreuzbergParser")
    parser.add_argument("--message", default="Iniciando…")
    parser.add_argument(
        "--ready-file",
        default="",
        help="Exit when this path exists (created by the main process)",
    )
    args = parser.parse_args()

    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GLib, Gtk, GdkPixbuf  # type: ignore
    except Exception as exc:
        sys.stderr.write(f"splash: GTK unavailable: {exc}\n")
        return 1

    image_path = os.path.abspath(args.image)
    if not os.path.isfile(image_path):
        sys.stderr.write(f"splash: image not found: {image_path}\n")
        return 1

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title(args.title)
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_border_width(24)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    win.add(box)

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            image_path, 160, 160, True
        )
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        box.pack_start(image, False, False, 0)
    except Exception:
        # Still show text if the image fails to load.
        pass

    label = Gtk.Label(label=args.message)
    label.set_justify(Gtk.Justification.CENTER)
    box.pack_start(label, False, False, 0)

    win.connect("delete-event", Gtk.main_quit)
    win.show_all()

    ready = (args.ready_file or "").strip()

    def _poll_ready() -> bool:
        if ready and os.path.exists(ready):
            Gtk.main_quit()
            return False
        return True

    if ready:
        GLib.timeout_add(100, _poll_ready)

    def _on_signal(_signum, _frame) -> None:
        GLib.idle_add(Gtk.main_quit)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
