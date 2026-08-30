#!/usr/bin/env python3
"""Render every mock_*.html / seed_*.html fixture to a sibling PNG.

The platform ingests SCREENSHOTS (images) as evidence artifacts; the HTML
files are the source that generates them. Re-run this script after editing
any mock:

    python3 fixtures/render_screenshots.py

Uses headless Chrome. Window is 600x1200: 600 wide fits the widest mock
(the 560px prescreen email card plus default body margins) without
horizontal cropping; 1200 tall exceeds every mock's content height so
nothing is cut off (mocks are 420-560px-wide cards well under 1200px tall —
trailing whitespace is harmless for eval inputs and keeps output
deterministic). HTML comments (the planted-violation annotations) never
render, so the PNGs are clean-room eval inputs by construction.
"""

import pathlib
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIDTH, HEIGHT = 600, 1200
FX = pathlib.Path(__file__).parent


def main() -> int:
    if not pathlib.Path(CHROME).exists():
        print(f"FAIL: Chrome binary not found at {CHROME}", file=sys.stderr)
        return 1
    sources = sorted(list(FX.glob("mock_*.html")) + list(FX.glob("seed_*.html")))
    if not sources:
        print("FAIL: no mock_*.html / seed_*.html sources found", file=sys.stderr)
        return 1
    failures = []
    for src in sources:
        out = src.with_suffix(".png")
        res = subprocess.run(
            [CHROME, "--headless=new", f"--screenshot={out}",
             f"--window-size={WIDTH},{HEIGHT}", "--hide-scrollbars",
             "--default-background-color=FFFFFFFF", f"file://{src.resolve()}"],
            capture_output=True, text=True, timeout=60,
        )
        if res.returncode != 0 or not out.exists() or out.stat().st_size < 5000:
            failures.append(f"{src.name}: rc={res.returncode} "
                            f"size={out.stat().st_size if out.exists() else 'missing'}")
        else:
            print(f"rendered {out.name} ({out.stat().st_size} bytes)")
    if failures:
        print("FAIL", *failures, sep="\n", file=sys.stderr)
        return 1
    print(f"OK — {len(sources)} PNGs at {WIDTH}x{HEIGHT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
