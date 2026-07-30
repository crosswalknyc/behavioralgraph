# macOS URL handler: `cwcookie://`

One-time setup that makes the cookie-gap operator emails **clickable**.

After running `install.sh`, clicking

```
cwcookie://donate/max.com
```

from Apple Mail (or anywhere on the Mac) opens Terminal.app and runs

```
cd <bg-webapp>
python3 scripts/trends_scrapers/donate_cookies.py max.com
```

The daily `cookie_gap_notify.py` email already includes both the
`cwcookie://` link and a copy-pasteable command, so users with the
handler installed get one-click behavior and everyone else can still
paste the command into any Terminal.

## Install

```bash
bash scripts/trends_scrapers/macos_url_handler/install.sh
```

That builds `/Applications/Cookie Donate.app`, wires up the
`cwcookie://` scheme in its `Info.plist`, ad-hoc signs the bundle so
Gatekeeper doesn't quarantine it, and registers it with
LaunchServices.

On the first `cwcookie://` click, macOS will ask "Do you want to allow
'Cookie Donate' to open?". Click **Allow**. macOS remembers the choice
per URL scheme so every subsequent click is one-tap.

## Verify

```bash
open 'cwcookie://donate/max.com'
```

A Terminal window should open at the repo root and start the cookie
donation script for max.com.

## Uninstall

```bash
bash scripts/trends_scrapers/macos_url_handler/install.sh --uninstall
```

## Notes

- The repo path is baked into the compiled `.app` at install time
  (via `{{REPO_ROOT}}` substitution). If you move the repo, re-run
  `install.sh`.
- The AppleScript validates the domain (must contain a dot,
  characters restricted to `[A-Za-z0-9.-]`) before executing anything.
  Combined with `quoted form of` shell escaping, a hostile URL cannot
  break out of the two arguments.
- The `.app` bundle is hidden from the Dock and Cmd-Tab
  (`LSUIElement = true`) so it never appears as a foreground app.
- Not committed as a pre-built binary — every operator compiles their
  own copy so the repo-root path is correct for their machine.
