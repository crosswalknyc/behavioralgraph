-- CookieDonate.applescript
--
-- macOS URL-scheme handler for cwcookie:// links.
--
-- When the cookie-gap notifier emails "cwcookie://donate/max.com",
-- clicking the link in Apple Mail (or any macOS app that honors
-- system URL schemes) launches this app, which opens Terminal.app
-- and runs the cookie-donation script for that domain.
--
-- One-time install:
--   bash scripts/trends_scrapers/macos_url_handler/install.sh
--
-- The install script substitutes {{REPO_ROOT}} below with the actual
-- absolute path to bg-webapp on this Mac before compiling with
-- osacompile.

on open location theURL
    -- theURL looks like:
    --   cwcookie://donate/max.com
    --   cwcookie://amctheatres.com
    --   cwcookie:donate/max.com          (rare, from some mail clients)
    -- Strip the scheme + optional "donate/" verb + any trailing slash / query.
    set theDomain to theURL

    if theDomain starts with "cwcookie://" then
        set theDomain to text 12 thru -1 of theDomain
    else if theDomain starts with "cwcookie:" then
        set theDomain to text 10 thru -1 of theDomain
    end if

    if theDomain starts with "//" then
        set theDomain to text 3 thru -1 of theDomain
    end if

    if theDomain starts with "donate/" then
        set theDomain to text 8 thru -1 of theDomain
    end if

    -- Strip anything after the first / or ?
    set AppleScript's text item delimiters to "/"
    set theDomain to text item 1 of theDomain
    set AppleScript's text item delimiters to "?"
    set theDomain to text item 1 of theDomain
    set AppleScript's text item delimiters to ""

    -- Very light domain validation: must contain at least one dot
    -- and only allowed characters. Reject anything else outright
    -- rather than shell-inject.
    if theDomain is "" or theDomain does not contain "." then
        display dialog "Cookie Donate: invalid URL." & return & return & theURL buttons {"OK"} default button "OK" with icon caution
        return
    end if
    considering case
        set validChars to "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    end considering
    repeat with c in the characters of theDomain
        if validChars does not contain (c as string) then
            display dialog "Cookie Donate: invalid domain characters." & return & return & (theDomain as string) buttons {"OK"} default button "OK" with icon caution
            return
        end if
    end repeat

    set theRepo to "{{REPO_ROOT}}"

    -- Compose the shell command. quoted form escapes both values so
    -- an attacker crafting a wildly hostile URL still can't break
    -- out of the two arguments (defense-in-depth after the char
    -- allowlist above).
    set theCmd to "cd " & quoted form of theRepo & " && python3 scripts/trends_scrapers/donate_cookies.py " & quoted form of theDomain

    tell application "Terminal"
        activate
        do script theCmd
    end tell
end open location

-- If a user double-clicks the .app bundle instead of clicking a URL
-- from mail, show a hint rather than doing nothing.
on run
    display dialog "Cookie Donate URL handler is installed." & return & return & "It responds to cwcookie:// links from operator emails. There's nothing to launch directly here." buttons {"OK"} default button "OK"
end run
