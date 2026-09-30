#!/usr/bin/env python3
"""Convert a Claude artifact's HTML into a PDF with headless Chromium.

Artifacts at https://claude.ai/artifact/<id> are private and need a signed-in
claude.ai session, so this script never fetches them, logs in or touches
cookies. Give it the artifact's HTML source instead:

  * a local .html file you wrote, or
  * the page fetched beforehand with the Claude Code Artifact tool
    (action "read" with the artifact URL, plus path "index.html" to save it
    to a local file).

Artifact HTML is usually a fragment (no doctype, <html>, <head> or <body>).
Fragments are wrapped in the same skeleton claude.ai adds at publish time:
charset meta, viewport meta with viewport-fit=cover, and a small reset.
Full documents are rendered as they are.

The page is served from a virtual origin so relative files next to INPUT
(images, scripts, data) resolve. Network access is limited to the hosts
artifacts may load from (Google Fonts, cdnjs, jsdelivr, unpkg, Tailwind CDN,
jQuery CDN); anything else is blocked and reported on stderr.

Install:
    pip install playwright
    playwright install chromium

Usage:
    artifact2pdf.py INPUT.html [-o OUT.pdf] [--format A4|Letter] [--landscape]
                    [--dark] [--expand-details] [--margin 12mm] [--scale 1.0]

Exit codes: 0 ok, 2 bad arguments or missing input, 3 render timeout,
4 Playwright or Chromium not installed, 1 anything else.
"""

from __future__ import annotations

import argparse
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ORIGIN = "https://artifact.local"
ALLOWED_HOSTS = {
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdnjs.cloudflare.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdn.tailwindcss.com",
    "code.jquery.com",
}

# Page sizes in millimetres, portrait.
PAGE_SIZES_MM = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}
UNIT_TO_PX = {"px": 1.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0, "pt": 96 / 72}

# The skeleton claude.ai wraps a fragment in. The reset is copied from a
# published artifact; the dark body colours are an approximation for --dark.
SKELETON = """<!doctype html><html data-theme="{theme}"><head><meta charset=utf-8>\
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">\
<style>:root{{color-scheme:{theme};box-sizing:border-box;\
padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}\
html{{scroll-padding-top:env(safe-area-inset-top,0px)}}\
body{{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif;\
background:{bg};color:{fg}}}img{{max-width:100%}}\
[hidden]:not([hidden=until-found i]){{display:none!important}}</style></head><body>
{body}
</body></html>"""
BODY_COLOURS = {"light": ("#faf9f5", "#141413"), "dark": ("#262624", "#faf9f5")}

PRINT_CSS = """
*, *::before, *::after {
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
  animation-duration: 0s !important;
  animation-delay: 0s !important;
  transition: none !important;
}
tr, img, svg, canvas, .a2p-keep { break-inside: avoid; }
thead { display: table-header-group; }
h1, h2, h3, h4, h5, h6 { break-after: avoid; }
.a2p-keepnext { break-after: avoid; break-inside: avoid; }
.a2p-unpin { position: static !important; inset: auto !important; }
.a2p-unclip { overflow: visible !important; max-height: none !important; }
.a2p-wrap { white-space: normal !important; min-width: 12em; }
.a2p-hide,
[role="tooltip"], .tooltip, .chartjs-tooltip, .apexcharts-tooltip, .d3-tip,
#vg-tooltip-element, .vg-tooltip, .plotly .hoverlayer, .highcharts-tooltip,
.recharts-tooltip-wrapper, .c3-tooltip-container, .nvtooltip,
.google-visualization-tooltip { display: none !important; }
"""

# Tags elements for PRINT_CSS: figures, tables, blockquotes and cards
# (rounded boxes) that fit on one page keep together, while taller ones break
# between rows instead of leaving a half-empty page. Sticky/fixed elements go
# static, scroll boxes stop clipping, and hover tooltips are hidden. A table
# wider than its column wraps its long text cells, then shrinks to fit (not
# below 60%). Paints the page margins with the
# document background. Returns the outermost elements still wider than the
# page.
PREP_JS = """
(maxKeepHeight) => {
  const clear = 'rgba(0, 0, 0, 0)';
  const keepTags = new Set(['FIGURE', 'TABLE', 'PRE', 'BLOCKQUOTE']);
  for (const el of document.body.querySelectorAll('*')) {
    if (el instanceof SVGElement && el.ownerSVGElement) continue;
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || cs.position === 'sticky') el.classList.add('a2p-unpin');
    const scrolls = v => v === 'auto' || v === 'scroll';
    if (scrolls(cs.overflowX) || scrolls(cs.overflowY)) el.classList.add('a2p-unclip');
    const floating = cs.position === 'absolute' || cs.position === 'fixed';
    if (floating && cs.pointerEvents === 'none' && /tip/i.test(el.getAttribute('class') || '')) {
      el.classList.add('a2p-hide');
    }
    const rounded = parseFloat(cs.borderTopLeftRadius) > 0;
    const boxed = cs.backgroundColor !== clear || parseFloat(cs.borderTopWidth) > 0 || cs.boxShadow !== 'none';
    const card = rounded && boxed && cs.display !== 'inline';
    if (card || keepTags.has(el.tagName)) {
      el.classList.add(el.getBoundingClientRect().height < maxKeepHeight ? 'a2p-keep' : 'a2p-tall');
    }
  }
  // A figure too tall to keep whole still keeps its caption, subtitle and
  // legend on the same page as its first chart.
  for (const fig of document.body.querySelectorAll('.a2p-tall:not(table)')) {
    if (!fig.querySelector('svg, canvas, img')) continue;
    for (const child of fig.children) {
      if (child.matches('svg, canvas, img') || child.querySelector('svg, canvas, img')) break;
      child.classList.add('a2p-keepnext');
    }
  }
  const bgOf = e => getComputedStyle(e).backgroundColor;
  const pageBg = bgOf(document.documentElement) !== clear ? bgOf(document.documentElement) : bgOf(document.body);
  if (pageBg !== clear) {
    const st = document.createElement('style');
    st.textContent = `@page { background: ${pageBg}; }`;
    document.head.appendChild(st);
  }
  if (document.activeElement) document.activeElement.blur();
  const limit = document.documentElement.clientWidth + 1;
  const fitted = [];
  for (const t of document.body.querySelectorAll('table')) {
    const avail = t.parentElement.clientWidth;
    if (t.getBoundingClientRect().width <= avail + 1) continue;
    for (const cell of t.querySelectorAll('th, td')) {
      if (cell.textContent.trim().length > 24) cell.classList.add('a2p-wrap');
    }
    const z = Math.min(1, Math.max(0.6, avail / t.getBoundingClientRect().width));
    t.style.zoom = z;
    fitted.push(Math.round(z * 100));
  }
  const wide = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (el instanceof SVGElement && el.ownerSVGElement) continue;
    if (wide.some(w => w.el.contains(el))) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && (r.right > limit || el.scrollWidth > el.clientWidth + 1 && el.classList.contains('a2p-unclip'))) {
      const id = el.id ? '#' + el.id : '';
      const cls = (el.getAttribute('class') || '').trim().split(/\\s+/).filter(Boolean).map(c => '.' + c).join('');
      wide.push({el, sel: el.tagName.toLowerCase() + id + cls});
    }
  }
  return {wide: wide.map(w => w.sel), fitted};
}
"""

# Resolves once the DOM has been quiet for `quiet` ms, or false after `limit`.
SETTLE_JS = """
([quiet, limit]) => new Promise(resolve => {
  let last = performance.now();
  const start = last;
  const mo = new MutationObserver(() => { last = performance.now(); });
  mo.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  (function tick() {
    const now = performance.now();
    if (now - last >= quiet) { mo.disconnect(); resolve(true); }
    else if (now - start >= limit) { mo.disconnect(); resolve(false); }
    else setTimeout(tick, 50);
  })();
})
"""

TWO_FRAMES_JS = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


def log(msg: str) -> None:
    print(f"artifact2pdf: {msg}", file=sys.stderr)


def die(msg: str, code: int) -> "NoReturn":  # noqa: F821
    log(msg)
    sys.exit(code)


def parse_length(text: str) -> float:
    """Return a CSS length ('12mm', '0.5in', '20px') in CSS pixels."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(px|mm|cm|in|pt)?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError(f"invalid length {text!r}; use e.g. 12mm, 0.5in, 20px")
    return float(m.group(1)) * UNIT_TO_PX[m.group(2) or "px"]


def is_full_document(html: str) -> bool:
    head = re.sub(r"^(﻿|\s|<!--.*?-->)*", "", html[:4096], flags=re.S)
    return bool(re.match(r"<!doctype\b|<html[\s>]", head, re.I))


def set_theme_attribute(html: str, theme: str) -> str:
    """Put data-theme on the <html> tag so scripts see it on first run."""
    def repl(m: re.Match) -> str:
        attrs = re.sub(r"""\sdata-theme\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", m.group(1), flags=re.I)
        return f'<html data-theme="{theme}"{attrs}>'
    return re.sub(r"<html\b([^>]*)>", repl, html, count=1, flags=re.I)


def prepare_html(raw: str, theme: str) -> str:
    if is_full_document(raw):
        return set_theme_attribute(raw, theme)
    bg, fg = BODY_COLOURS[theme]
    return SKELETON.format(theme=theme, bg=bg, fg=fg, body=raw)


def count_pages(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Render a Claude artifact's HTML (fragment or full page) to PDF.",
        epilog="Install: pip install playwright && playwright install chromium",
    )
    p.add_argument("input", type=Path, help="artifact HTML file")
    p.add_argument("-o", "--output", type=Path, help="output PDF (default: INPUT with .pdf suffix)")
    p.add_argument("--format", choices=sorted(PAGE_SIZES_MM), default="A4", help="paper size (default A4)")
    p.add_argument("--landscape", action="store_true", help="landscape orientation")
    p.add_argument("--dark", action="store_true", help="render the dark theme instead of light")
    p.add_argument("--expand-details", action="store_true", help="force every <details> block open")
    p.add_argument("--margin", default="12mm", help="page margin on all sides (default 12mm)")
    p.add_argument("--scale", type=float, default=1.0, help="render scale, 0.1 to 2 (default 1.0)")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for load and fonts (default 30)")
    p.add_argument("--allow-host", action="append", default=[], metavar="HOST",
                   help="extra host the page may load from (repeatable)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 0.1 <= args.scale <= 2:
        parser.error("--scale must be between 0.1 and 2")
    try:
        margin_px = parse_length(args.margin)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    src: Path = args.input.expanduser()
    if not src.is_file():
        die(f"input file not found: {src}", 2)
    src = src.resolve()
    out: Path = (args.output.expanduser() if args.output else src.with_suffix(".pdf")).resolve()
    theme = "dark" if args.dark else "light"
    html = prepare_html(src.read_text(encoding="utf-8", errors="replace"), theme)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("Playwright is not installed. Run: pip install playwright && playwright install chromium", 4)

    # Lay the page out at the printable width so JS-drawn charts are sized
    # for the paper, not for an arbitrary window.
    w_mm, h_mm = PAGE_SIZES_MM[args.format]
    if args.landscape:
        w_mm, h_mm = h_mm, w_mm
    mm = UNIT_TO_PX["mm"]
    print_w = (w_mm * mm - 2 * margin_px) / args.scale
    print_h = (h_mm * mm - 2 * margin_px) / args.scale
    if print_w < 100 or print_h < 100:
        parser.error("--margin leaves no room on the page")

    allowed = ALLOWED_HOSTS | {h.lower() for h in args.allow_host}
    root = src.parent
    timeout_ms = args.timeout * 1000

    def handle_route(route) -> None:
        url = route.request.url
        if url.startswith(ORIGIN + "/"):
            path = unquote(urlsplit(url).path).lstrip("/")
            if path in ("", "index.html"):
                return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            target = (root / path).resolve()
            if target.is_file() and target.is_relative_to(root):
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                return route.fulfill(status=200, content_type=ctype, body=target.read_bytes())
            log(f"not found next to input: {path}")
            return route.fulfill(status=404, body="")
        host = (urlsplit(url).hostname or "").lower()
        if host in allowed:
            return route.continue_()
        log(f"blocked request to {host or url} (use --allow-host to permit)")
        return route.abort("blockedbyclient")

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except PlaywrightError as e:
                die(f"could not launch Chromium; run: playwright install chromium\n{e}", 4)
            context = browser.new_context(
                viewport={"width": round(print_w), "height": round(print_h)},
                device_scale_factor=2,
                color_scheme=theme,
                reduced_motion="reduce",
            )
            page = context.new_page()
            # Chromium also reports each failed load as a console error;
            # requestfailed and response already say which URL, so skip those.
            page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
                    and log(f"console error: {m.text}"))
            page.on("pageerror", lambda e: log(f"page error: {e}"))
            page.on("requestfailed", lambda r: not (r.failure or "").startswith("net::ERR_BLOCKED_BY_CLIENT")
                    and log(f"request failed: {r.url} ({r.failure})"))
            page.on("response", lambda r: r.status >= 400 and not r.url.startswith(ORIGIN + "/")
                    and log(f"HTTP {r.status}: {r.url}"))
            page.route("**/*", handle_route)

            try:
                page.goto(ORIGIN + "/", wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_function("document.fonts.status === 'loaded'", timeout=timeout_ms)
            except PlaywrightTimeout:
                die(f"render timed out after {args.timeout:g}s waiting for the network, "
                    "scripts or fonts to settle (raise --timeout?)", 3)

            # Scripts may have rewritten the theme; set it again (theme-aware
            # pages observe this attribute and redraw).
            page.evaluate("t => { const r = document.documentElement;"
                          " if (r.getAttribute('data-theme') !== t) r.setAttribute('data-theme', t); }", theme)
            if args.expand_details:
                opened = page.evaluate("() => { const d = [...document.querySelectorAll('details:not([open])')];"
                                       " d.forEach(x => x.open = true); return d.length; }")
                log(f"expanded {opened} <details> block(s)")
            if not page.evaluate(SETTLE_JS, [300, min(timeout_ms, 10000)]):
                log("page kept changing after load; printing anyway")

            page.emulate_media(media="print")
            page.add_style_tag(content=PRINT_CSS)
            prep = page.evaluate(PREP_JS, print_h * 0.9)
            page.evaluate(TWO_FRAMES_JS)
            for pct in prep["fitted"]:
                log(f"shrank a wide table to {pct}% to fit the page")
            for sel in prep["wide"][:10]:
                log(f"wider than the page, may be cut off: {sel} (try --landscape or a smaller --scale)")

            margin = f"{margin_px:.2f}px"
            out.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(
                path=str(out),
                format=args.format,
                landscape=args.landscape,
                print_background=True,
                margin={"top": margin, "right": margin, "bottom": margin, "left": margin},
                scale=args.scale,
                outline=True,
                tagged=True,
            )
            browser.close()
    except PlaywrightTimeout as e:
        die(f"render timed out: {e}", 3)

    pages = count_pages(out)
    print(f"{out} ({pages} page{'s' if pages != 1 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
