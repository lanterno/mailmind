"""The .dmg, with its window laid out: MailMind on the left, an arrow, Applications on the right.

    uv run --group release python -I dmg.py APP BACKGROUND_FOLDER OUT

release.sh runs it. dmgbuild writes the window's layout (Finder's .DS_Store and the background's alias) into the
image itself, so nothing drives Finder: it runs the same in a terminal, over SSH and in CI. BACKGROUND_FOLDER holds
background.png and background@2x.png, which Release/dmg-background.swift draws and dmgbuild joins for Retina.
"""

import sys
from pathlib import Path

import dmgbuild

# The window's size is the background's (640 by 400 points), and the icons' centres are where dmg-background.swift
# draws around them.
WIDTH, HEIGHT = 640, 400
APP_AT = (160, 205)
APPLICATIONS_AT = (480, 205)


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: dmg.py APP BACKGROUND_FOLDER OUT", file=sys.stderr)
        sys.exit(1)
    app, backgrounds, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    dmgbuild.build_dmg(
        str(out),
        "MailMind",
        settings={
            "files": [str(app)],
            "symlinks": {"Applications": "/Applications"},
            # LZMA, the smallest of hdiutil's formats; every Mac MailMind runs on opens it.
            "format": "ULMO",
            "filesystem": "HFS+",
            "background": str(backgrounds / "background.png"),
            "window_rect": ((200, 140), (WIDTH, HEIGHT)),
            "default_view": "icon-view",
            "icon_size": 128,
            "text_size": 13,
            "icon_locations": {app.name: APP_AT, "Applications": APPLICATIONS_AT},
            "show_status_bar": False,
            "show_tab_view": False,
            "show_toolbar": False,
            "show_pathbar": False,
            "show_sidebar": False,
            # The background and the volume's own files stay hidden, so the window shows the two icons alone.
            "show_icon_preview": False,
            "include_icon_view_settings": True,
            "include_list_view_settings": False,
        },
    )


if __name__ == "__main__":
    main()
