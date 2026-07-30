#!/usr/bin/env bash
# ==========================================================================
# install.sh - build + register the cwcookie:// URL handler on this Mac.
#
# One-time setup. After this runs, clicking a `cwcookie://donate/<domain>`
# link (from the daily cookie-gap operator email, or from anywhere on the
# Mac) opens Terminal.app and runs
#
#   cd <bg-webapp> && python3 scripts/trends_scrapers/donate_cookies.py <domain>
#
# Usage:
#   bash scripts/trends_scrapers/macos_url_handler/install.sh
#
# Verify:
#   open "cwcookie://donate/hbomax.com"
#
# Uninstall:
#   bash scripts/trends_scrapers/macos_url_handler/install.sh --uninstall
# ==========================================================================
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "install.sh: this URL handler is macOS-only; skipping." >&2
    exit 0
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"     # -> bg-webapp/
APP_NAME="Cookie Donate.app"
APP_PATH="/Applications/${APP_NAME}"
SRC_APPLESCRIPT="${HERE}/CookieDonate.applescript"
TMP_APPLESCRIPT="$(mktemp -t CookieDonate.XXXXXX.applescript)"
trap 'rm -f "$TMP_APPLESCRIPT"' EXIT

if [[ "${1:-}" == "--uninstall" ]]; then
    if [[ -d "$APP_PATH" ]]; then
        rm -rf "$APP_PATH"
        echo "removed ${APP_PATH}"
        # Refresh LaunchServices so the scheme is fully forgotten.
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
            -kill -r -domain local -domain system -domain user >/dev/null 2>&1 || true
        echo "LaunchServices refreshed."
    else
        echo "no app installed at ${APP_PATH}"
    fi
    exit 0
fi

echo "install.sh: building Cookie Donate URL handler for repo:"
echo "  ${REPO_ROOT}"

# Substitute {{REPO_ROOT}} in the AppleScript source. Using # as the sed
# delimiter so a repo path containing / doesn't have to be escaped.
sed "s#{{REPO_ROOT}}#${REPO_ROOT}#g" "$SRC_APPLESCRIPT" > "$TMP_APPLESCRIPT"

# Remove any previous install so the fresh compile isn't merged.
if [[ -d "$APP_PATH" ]]; then
    echo "removing previous ${APP_PATH} ..."
    rm -rf "$APP_PATH"
fi

echo "compiling AppleScript -> ${APP_PATH} ..."
osacompile -o "$APP_PATH" "$TMP_APPLESCRIPT"

# Splice a URL-scheme handler entry into the app bundle's Info.plist.
# osacompile writes a minimal plist; we append CFBundleURLTypes so
# LaunchServices routes cwcookie:// to us.
PLIST="${APP_PATH}/Contents/Info.plist"
if [[ ! -f "$PLIST" ]]; then
    echo "install.sh: expected Info.plist not found at ${PLIST}" >&2
    exit 1
fi

# Set a stable bundle identifier so re-installs replace cleanly.
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.crosswalknyc.cookie-donate" "$PLIST" \
    2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.crosswalknyc.cookie-donate" "$PLIST"

# Nuke any pre-existing CFBundleURLTypes so we don't accumulate duplicates
# on subsequent installs, then add the single entry we want.
/usr/libexec/PlistBuddy -c "Delete :CFBundleURLTypes" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy \
    -c "Add :CFBundleURLTypes array" \
    -c "Add :CFBundleURLTypes:0 dict" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLName string com.crosswalknyc.cookie-donate" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string cwcookie" \
    "$PLIST"

# LSUIElement=true keeps the .app out of the Dock / Cmd-Tab list; it
# should only ever appear when responding to a URL.
/usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST"

# Re-sign so Gatekeeper doesn't quarantine the freshly-mutated bundle.
# Ad-hoc signing (-) is enough for a locally-built helper app.
codesign --force --sign - "$APP_PATH" 2>/dev/null || true

# Register with LaunchServices so `open cwcookie://...` routes here.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP_PATH"

# Some macOS versions cache scheme handlers aggressively; a targeted
# domain-scoped kill is the fastest way to force a refresh without
# nuking every user handler.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -R "$APP_PATH" 2>/dev/null || true

echo
echo "✓ installed:  ${APP_PATH}"
echo "✓ scheme:     cwcookie://"
echo
echo "Test:  open 'cwcookie://donate/hbomax.com'"
echo "       (a Terminal window should open and start the cookie donation.)"
echo
echo "On the first click, macOS may ask 'Do you want to allow"
echo "\"Cookie Donate\" to open?'  Click Allow. macOS remembers the"
echo "choice per URL scheme, so subsequent clicks are one-tap."
