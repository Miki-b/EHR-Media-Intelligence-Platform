"""Render docs/writeup.md to docs/writeup.pdf (one A4 page) with headless Chrome or Edge.

    python scripts/render_writeup.py [--browser PATH]

The write-up uses a small Markdown subset (# and ## headings, "- " bullets, paragraphs,
**bold** and `code`), converted here without extra dependencies. The browser is taken from
--browser, the CHROME environment variable, or the usual install locations. The script
fails if the PDF is longer than one page, since the brief asks for a one-page write-up.
"""

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "writeup.md"
TARGET = ROOT / "docs" / "writeup.pdf"
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]

CSS = """
@page { size: A4; margin: 14mm 16mm; }
body { font-family: Georgia, "Times New Roman", serif; font-size: 9.6pt; line-height: 1.34;
       color: #1a1a1a; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 4pt; }
h2 { font-size: 11pt; margin: 9pt 0 3pt; padding-bottom: 1pt; border-bottom: 0.6pt solid #888; }
p { margin: 0 0 4pt; text-align: justify; }
ul { margin: 0 0 4pt; padding-left: 13pt; }
li { margin: 0 0 2.5pt; text-align: justify; }
code { font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 0.86em; }
"""


def inline(text: str) -> str:
    """Escape HTML, then apply `code` and **bold** (code first, so its contents stay literal)."""
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
        else:
            out.append(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(part)))
    return "".join(out)


def to_html(markdown: str) -> str:
    body: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    if in_list:
        body.append("</ul>")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Write-up</title><style>{CSS}</style></head><body>{''.join(body)}</body></html>"
    )


def find_browser(explicit: str | None) -> str:
    for candidate in [explicit, os.environ.get("CHROME"), *BROWSERS]:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return shutil.which(candidate) or candidate
    raise SystemExit("No Chrome/Edge found; pass --browser or set CHROME.")


def page_count(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf.read_bytes()))


def render(browser: str, source: Path = SOURCE, target: Path = TARGET) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "writeup.html"
        page.write_text(to_html(source.read_text(encoding="utf-8")), encoding="utf-8")
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={target}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    return page_count(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--browser", help="path to Chrome or Edge")
    args = parser.parse_args()
    pages = render(find_browser(args.browser))
    print(f"wrote {TARGET.relative_to(ROOT)} ({pages} page{'s' if pages != 1 else ''})")
    if pages != 1:
        print("the write-up must fit on one page: shorten docs/writeup.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
