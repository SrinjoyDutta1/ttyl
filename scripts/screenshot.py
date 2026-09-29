"""Render the TUI (and the menu bar panel, if gui/ is built) on demo data to docs/*.png.

    .venv/bin/python scripts/screenshot.py
"""

import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ttyl.demo import DemoStore
from ttyl.tui import TtylApp

DOCS = Path(__file__).resolve().parent.parent / "docs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


async def shoot(name: str, size: tuple[int, int], keys: list[str]) -> Path:
    app = TtylApp(DemoStore(), interval=3600)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        for key in keys:
            await pilot.press(key)
        await pilot.pause(0.3)
        return Path(app.save_screenshot(filename=f"{name}.svg", path=str(DOCS)))


def to_png(svg: Path) -> None:
    chrome = CHROME if Path(CHROME).exists() else shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        print("no Chrome found, skipping PNG", file=sys.stderr)
        return
    w, h = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text()).groups())
    html = svg.with_suffix(".html")
    html.write_text(f'<html><body style="margin:0;background:transparent">{svg.read_text()}</body></html>')
    subprocess.run([chrome, "--headless=new", "--hide-scrollbars", "--force-device-scale-factor=2",
                    "--default-background-color=00000000", f"--window-size={int(w)},{int(h)}",
                    f"--screenshot={svg.with_suffix('.png')}", html.as_uri()], check=True, capture_output=True)
    html.unlink()
    svg.unlink()  # the PNG is what the README uses


async def main() -> None:
    DOCS.mkdir(exist_ok=True)
    shots = [await shoot("ttyl", (132, 42), []), await shoot("ttyl-closed", (132, 42), ["down"] * 6)]
    bar = Path(__file__).resolve().parent.parent / "gui" / "build" / "ttyl.app" / "Contents" / "MacOS" / "ttyl-bar"
    if bar.exists():  # the menu bar panel, drawn by the app itself
        subprocess.run([str(bar), "--snapshot", str(DOCS / "menubar.png")], check=True, capture_output=True)
        print(DOCS / "menubar.png")
    for svg in shots:
        to_png(svg)
        print(svg.with_suffix(".png") if svg.with_suffix(".png").exists() else svg)


if __name__ == "__main__":
    asyncio.run(main())
