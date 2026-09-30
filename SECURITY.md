# Security

This covers `artifact2pdf.py`, a script that renders a Claude artifact's HTML
to PDF with headless Chromium through Playwright.

## Supported versions

Only the latest commit on the default branch is supported. There are no
release branches and fixes are not backported.

## Reporting a vulnerability

Please report privately. Do not open a public issue or pull request for a
security problem.

- Use the repository host's private vulnerability reporting (on GitHub:
  **Security > Report a vulnerability**).
- Include the version (commit hash), your OS, Python and Playwright versions,
  a minimal HTML file that reproduces the problem, the exact command you ran,
  and what you expected versus what happened.
- Do not include real secrets, private artifacts or personal data in the
  report. A synthetic reproduction is enough.

This is a personal project maintained on a best-effort basis. Reports are
read and acknowledged when possible, but there is no guaranteed response
time. Please allow a reasonable window for a fix before disclosing publicly.

## What the script does and does not do

It does:

- Read one local HTML file (the input) and write one PDF (the output).
- Launch a local headless Chromium, serve the HTML to it from a virtual
  origin (`https://artifact.local`), run the page's JavaScript, and print it.
- Let the page load over HTTP(S) from a fixed list of CDN hosts: Google Fonts
  (`fonts.googleapis.com`, `fonts.gstatic.com`), `cdnjs.cloudflare.com`,
  `cdn.jsdelivr.net`, `unpkg.com`, `cdn.tailwindcss.com` and `code.jquery.com`,
  plus any host you add with `--allow-host`.
- With `--local-files` only, serve files from the input's folder to the page.

It does not:

- Sign in to claude.ai, fetch artifacts, or read browser cookies, sessions or
  saved passwords.
- Read environment variables, config files, API keys or tokens.
- Send telemetry or contact any server of its own. The only network traffic
  is what the rendered page requests (limited as above) and, at install time,
  Playwright's own download of Chromium.

## Threat model

**Treat the input HTML as code.** The script runs the page's JavaScript in a
real browser. It was written to render your own artifacts, which you or
Claude authored. It is not hardened for rendering HTML from strangers.

Protections that are in place:

- **Chromium sandbox on.** Chromium is launched with its sandbox enabled
  (Playwright's default is off). `--no-sandbox` disables it for environments
  where it cannot start, such as running as root in a container.
- **HTTP(S) allowlist.** Requests to hosts outside the list are blocked and
  reported on stderr.
- **WebSockets blocked.** Every WebSocket is intercepted and never connected
  to its server. The page may see the socket as open, but nothing it sends
  leaves the machine.
- **No local files by default.** The page cannot read files from disk unless
  you pass `--local-files`. With it, files are served only from the input's
  folder and subfolders, only if their resolved path (after following
  symlinks) stays inside that folder, and never if any path component starts
  with `.` (so `.ssh/`, `.env` and similar are refused).
- **No `file://` access.** The page runs on an `https` virtual origin, so it
  cannot open `file://` URLs.
- **Render timeout.** Loading is bounded by `--timeout` (default 30 seconds).

## Known limitations

These apply to the current version. Keep them in mind before rendering HTML
you did not write.

1. **`--local-files` exposes the whole folder tree.** With the flag, the page
   can read any non-hidden file in the input's folder and its subfolders and
   draw it into the PDF. Only use it when the input sits in a folder of its
   own with just the files the page needs.
2. **`--no-sandbox` removes a layer.** Without the sandbox, a browser exploit
   in a malicious page would run with your user's permissions.
3. **Allowlisted hosts serve arbitrary code.** jsDelivr, unpkg and cdnjs host
   any published npm or GitHub package. The allowlist limits where requests
   go; it does not vouch for what a page loads from them. Redirects from an
   allowlisted host are not re-checked against the list.
4. **DNS lookups can leak.** `<link rel="dns-prefetch">` and similar hints may
   resolve hostnames outside the allowlist even though the requests
   themselves are blocked.

## Safe use

- Render only HTML you trust. For anything else, run the script inside a
  disposable container or VM with no access to your home folder.
- Leave `--local-files` off unless the page needs its own images or data,
  and then keep the input in a folder of its own (see limitation 1).
- Keep `--allow-host` to the hosts the page actually needs.
- Check the PDF before sharing it. It contains everything the page rendered,
  including tables inside collapsed `<details>` blocks when you pass
  `--expand-details`. The page's `<title>` goes into the PDF's metadata.
- A PDF made from a private artifact is no longer private. It is a file that
  can be forwarded to anyone, so share it with the same care as the artifact.
- Don't commit rendered PDFs or artifact HTML to a public repository. The
  included `.gitignore` excludes `*.html` and `*.pdf` for this reason.
