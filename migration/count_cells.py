"""People counts are whole numbers in the file (2026-11-12).

A profile file carries two count columns: the people in the data on a
row and the projected US people. Both are whole numbers and are written
as whole numbers ('192535'). A frame read back with the default types
turns a count column that has a blank cell into decimals, and writing
that frame out prints every count with a trailing '.0' ('192535.0',
'6351730.0'). 665 files in the bucket carried that form, most from two
in-place passes that read with the default types.

``whole_counts(df)`` is the one normalizer. Every path that serializes a
profile frame calls it on the frame it is about to write. Nothing else
in the frame is touched; a blank stays blank; a cell that is not a whole
number is left exactly as written; it never raises.

``whole_count_text(cell)`` is the same rule for one cell of text, for
tools that edit the stored bytes in place.
"""
from __future__ import annotations

import re

RAW = "Original Raw Numbers"
PROJ = "US Gen Pop Projection"
COUNT_COLUMNS = (RAW, PROJ)

_FLOAT_WHOLE = re.compile(r"^(-?\d+)\.0+$")


def whole_count_text(cell):
    """'192535.0' -> '192535'; anything else comes back unchanged."""
    s = str(cell)
    m = _FLOAT_WHOLE.match(s.strip())
    return m.group(1) if m else s


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:                       # a blank read back as not-a-number
            return ""
        if v.is_integer():
            return str(int(v))
        return v
    try:
        import numpy as _np
        if isinstance(v, _np.integer):
            return str(int(v))
        if isinstance(v, _np.floating):
            f = float(v)
            if f != f:
                return ""
            return str(int(f)) if f.is_integer() else v
    except Exception:
        pass
    if isinstance(v, str):
        return whole_count_text(v)
    return v


def _is_numeric(series) -> bool:
    try:
        import pandas as pd
        return bool(pd.api.types.is_numeric_dtype(series))
    except Exception:
        return str(getattr(series, "dtype", "")) not in ("object", "str", "string")


def whole_counts(df):
    """The frame with its two count columns written as whole numbers.
    Returns a shallow copy when anything changes, the frame itself when
    nothing does. Never raises."""
    try:
        cols = [c for c in COUNT_COLUMNS if c in getattr(df, "columns", [])]
        if not cols:
            return df
        out = None
        for c in cols:
            new = df[c].map(_cell)
            try:
                # a text column that is already whole is left as it is, whichever text type the frame gives it
                # ('object' on pandas 2, 'str' on pandas 3: the build server read a clean frame as changed and
                # copied it every time); only a numeric column, which writes '192535.0', is rewritten as text
                same = bool((new.astype(str) == df[c].astype(str)).all()) and not _is_numeric(df[c])
            except Exception:
                same = False
            if same:
                continue
            if out is None:
                out = df.copy()
            out[c] = new.astype(object)
        return df if out is None else out
    except Exception:
        return df


def float_count_cells(df) -> int:
    """How many count cells in the frame are written with a decimal
    point (the form this module removes)."""
    n = 0
    try:
        for c in COUNT_COLUMNS:
            if c in df.columns:
                n += int(df[c].astype(str).str.match(r"^-?\d+\.0+$").sum())
    except Exception:
        return n
    return n


__all__ = ["whole_counts", "whole_count_text", "float_count_cells", "COUNT_COLUMNS", "RAW", "PROJ"]
