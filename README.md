# artifact2pdf

Render a Claude artifact's HTML to PDF with headless Chromium.

Claude artifacts are HTML pages published at `claude.ai/artifact/<id>`. They
are private, often draw charts with JavaScript after load, load web fonts,
switch between light and dark themes, and hide data tables inside collapsed
`<details>` blocks. Printing one from a browser tends to lose the charts, clip
the tables, or split cards across pages. `artifact2pdf` loads the page the way
claude.ai serves it, waits for everything to settle, and prints a clean PDF.

This is an unofficial tool. It is not made by or affiliated with Anthropic.

## What it handles

- **Fragments.** Artifact HTML is usually a fragment with no `<html>`,
  `<head>` or `<body>`. It is wrapped in the same skeleton claude.ai adds
  (charset, `viewport-fit=cover` viewport and a small reset). Full documents
  are rendered as they are.
- **Fonts and charts.** Waits for network idle, `document.fonts.ready` and a
  quiet DOM before printing, so Google Fonts and charts drawn with Chart.js,
  D3 or inline SVG are finished.
- **Themes.** Prints light by default (`prefers-color-scheme: light` and
  `data-theme="light"`), or dark with `--dark`. Page margins take the page's
  background colour, so dark PDFs have no white frame.
- **Tables.** `--expand-details` opens collapsed "Show table" blocks. Tables
  wider than the page wrap their long cells and shrink to fit, down to 60%.
  Long tables repeat their header row on each page.
- **Page breaks.** Figures, tables and cards that fit on a page are never
  split. Taller ones break between rows, and a chart keeps its caption.
- **Clean output.** Hover tooltips are hidden, sticky and fixed elements are
  unpinned, scroll boxes are expanded, and backgrounds are printed.

## Install

Needs Python 3.9 or newer.

```bash
pip install playwright
playwright install chromium
```

Then download `artifact2pdf.py`, or clone this repository.

## Get the artifact's HTML

The script never signs in to claude.ai, and it does not fetch artifacts or
read cookies. Give it the HTML source as a local file. Two ways to get it:

- **Claude Code:** ask Claude to read the artifact with the Artifact tool,
  using `action: "read"`, the artifact URL, and `path: "index.html"`. That
  saves the page to a local file and prints its path.
- **By hand:** save the HTML you or Claude wrote to a `.html` file.

## Usage

```bash
python3 artifact2pdf.py report.html
```

```bash
python3 artifact2pdf.py report.html -o report.pdf --format Letter --landscape --expand-details
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--output` | input with `.pdf` | Output file |
| `--format` | `A4` | `A4` or `Letter` |
| `--landscape` | off | Landscape pages; use it for wide tables |
| `--dark` | off | Print the dark theme |
| `--expand-details` | off | Open every `<details>` block |
| `--margin` | `12mm` | Page margin, in `mm`, `cm`, `in`, `pt` or `px` |
| `--scale` | `1.0` | Render scale, from 0.1 to 2 |
| `--timeout` | `30` | Seconds to wait for the page, fonts and scripts |
| `--allow-host` | none | Extra host the page may load from; repeatable |

On success it prints the output path and page count. Warnings (blocked
requests, console errors, content wider than the page) go to stderr.

Exit codes: `0` success, `2` bad arguments or missing input, `3` render
timeout, `4` Playwright or Chromium not installed, `1` anything else.

## Network access

The page may load only from the hosts artifacts are allowed to use: Google
Fonts, cdnjs, jsDelivr, unpkg, the Tailwind CDN and the jQuery CDN. Other
requests are blocked and listed on stderr. Add a host with `--allow-host`.
Relative files next to the input, such as images, are served from the
input's folder.

## Security

Treat the input HTML as code: the page's JavaScript runs in a real browser.
The tool is meant for rendering your own artifacts. Read
[SECURITY.md](SECURITY.md) before rendering HTML you did not write, and use it
to report a vulnerability.

## License

[MIT](LICENSE)
