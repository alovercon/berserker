"""
Twemoji PNG downloader for berserker GUI.

Downloads Twitter Emoji (Twemoji) 72x72 PNGs for use in wxPython.
PNG is used instead of SVG because wxPython 4.2 does not support SVG natively.

Usage:
    python -m berserker.tools.twemoji_sync
"""

import os
import sys
import json
import urllib.request
import urllib.error

REQUIRED_EMOJI = {
    "1f464": "user-avatar",
    "1f916": "bot-avatar",
    "1f527": "tool",
    "1f4c1": "folder",
    "1f9d0": "thinking",
    "2705": "check",
    "274c": "cross",
    "23f3": "hourglass",
    "1f7e2": "green",
    "1f534": "red",
    "1f7e1": "yellow",
    "1f4ac": "chat",
    "1f4ad": "thinking-bubble",
    "1f50d": "search",
    "2795": "plus",
    "1f5d1": "clear",
    "1f6a7": "construction",
    "2139": "info",
    "2757": "exclamation",
    "23f9": "stop",
    "2699": "settings",
    "27a1": "send",
    "2b05": "left-arrow",
    "2b06": "up-arrow",
    "2b07": "down-arrow",
}


def get_asset_dir():
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(script_dir, "assets", "twemoji")


def download_png(codepoint, png_dir):
    code = codepoint.lower()
    url = "https://raw.githubusercontent.com/jdecked/twemoji/main/assets/72x72/{}.png".format(code)
    dest = os.path.join(png_dir, "{}.png".format(code))
    if os.path.exists(dest):
        return "skip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "berserker/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            with open(dest, "wb") as f:
                f.write(resp.read())
        return "ok"
    except urllib.error.HTTPError as e:
        return "HTTP {}".format(e.code)
    except Exception as e:
        return str(e)


def main():
    png_dir = os.path.join(get_asset_dir(), "72x72")
    os.makedirs(png_dir, exist_ok=True)

    print("Twemoji PNG directory: {}".format(png_dir))
    print("Downloading {} emoji...".format(len(REQUIRED_EMOJI)))
    print()

    ok = skip = fail = 0
    for codepoint, name in sorted(REQUIRED_EMOJI.items()):
        result = download_png(codepoint, png_dir)
        if result == "ok":
            print("  + {}.png  ({})".format(codepoint, name))
            ok += 1
        elif result == "skip":
            skip += 1
        else:
            print("  ! {}.png  ({}): {}".format(codepoint, name, result))
            fail += 1

    print()
    print("Done: {} new, {} existing, {} failed  |  {} total".format(
        ok, skip, fail, len(REQUIRED_EMOJI)))

    manifest = {name: "{}.png".format(codepoint) for codepoint, name in REQUIRED_EMOJI.items()}
    manifest_path = os.path.join(get_asset_dir(), "emoji_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print("Manifest written: {}".format(manifest_path))

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
