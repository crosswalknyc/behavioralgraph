"""A download's name is one a link can carry.

Cara Dorfman at Amazon, 2026-11-16: "the CSV links came through broken".
One was. A read answered on the Subscriber IQ screen was saved as
"<the screen's label>.csv", the label opens with an emoji, and the
download link names the file in a header the storage service only takes
in plain Latin characters. The link was signed and handed over, and
opening it returned an error page instead of the file.

safe_name(name)     the name with anything a header cannot carry taken
                    out (emoji, control characters, quotes), accents
                    folded to their letters, the extension kept
disposition(name)   the attachment header for a signed link

Pure; never raises.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ['safe_name', 'disposition']


def safe_name(name, default='file'):
    try:
        raw = str(name or '').strip()
        m = re.match(r"^(.*?)(\.[A-Za-z0-9]{1,5})?$", raw, re.S)
        stem, ext = (m.group(1), m.group(2) or '') if m else (raw, '')
        stem = unicodedata.normalize('NFKD', stem).encode('ascii', 'ignore').decode('ascii')
        stem = re.sub(r'[\x00-\x1f\x7f"\\\\]+', ' ', stem)
        stem = ' '.join(stem.split()).strip(' .')
        if not stem.strip(' .-_'):
            stem = default
        return stem + ext
    except Exception:
        return default


def disposition(name, default='file'):
    return f'attachment; filename="{safe_name(name, default)}"'
