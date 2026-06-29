# =============================================================================
# csv_utils.py  -  Large-file CSV helpers shared across the yield pipeline
# =============================================================================
# Provides:
#   CHUNK_SIZE          default rows per chunk (100 000)
#   detect_encoding()   try encodings in order, return first that works
#   sniff_columns()     read only the header row; return column-name list
#   read_csv_smart()    read with optional usecols (column selection)
#   iter_chunks()       generator that yields DataFrames in CHUNK_SIZE slices
#
# All functions accept an optional encoding= argument; when omitted the
# encoding is auto-detected via detect_encoding().
# =============================================================================

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Generator, Iterable

import pandas as pd

# Default number of rows loaded into RAM at a time for streaming operations.
# Callers can override per-call.  Adjust with env var CSV_CHUNK_SIZE for
# system-wide tuning without code changes.
CHUNK_SIZE: int = int(os.environ.get('CSV_CHUNK_SIZE', 100_000))

_ENCODINGS = ('utf-8-sig', 'utf-8', 'utf-16', 'latin-1')


def _resolve_csv_from_path(path: Path) -> tuple[Path | None, bytes | None]:
    """If *path* is a .zip, extract the first CSV inside and return its bytes.

    Returns ``(None, bytes)`` for zip, ``(path, None)`` for plain CSV.
    """
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as zf:
            csvs = [n for n in zf.namelist() if n.lower().endswith('.csv') and not os.path.basename(n).startswith('.')]
            if not csvs:
                raise ValueError(f'No CSV found inside zip: {path}')
            return None, zf.read(csvs[0])
    return path, None


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

def detect_encoding(path: str | Path) -> str | None:
    """Return the first encoding that successfully reads the file header.

    Tries ``utf-8-sig``, ``utf-8``, ``utf-16``, ``latin-1`` in that order.
    Falls back to ``latin-1`` (which never raises a decode error).
    Returns ``None`` for ``.gz`` and ``.zip`` files — pandas infers compression
    and encoding automatically, so no pre-detection is needed.
    """
    path = Path(path)
    if path.suffix.lower() in ('.gz', '.zip'):
        return None   # pandas auto-handles both compressions; TextIOWrapper sniff would read raw bytes
    for enc in _ENCODINGS:
        try:
            with open(path, encoding=enc, errors='strict') as fh:
                fh.readline()   # only need to parse one line
            return enc
        except (UnicodeDecodeError, Exception):
            continue
    return 'latin-1'


# ---------------------------------------------------------------------------
# Header-only sniff
# ---------------------------------------------------------------------------

def sniff_columns(path: str | Path, encoding: str | None = None) -> list[str]:
    """Return the list of column names without loading any data rows.

    Peak RAM is proportional to the header row length only.
    Transparently handles .zip files containing a CSV.
    """
    path = Path(path)
    resolved, data = _resolve_csv_from_path(path)
    if data is not None:
        try:
            df_header = pd.read_csv(io.BytesIO(data), nrows=0, low_memory=False)
            return list(df_header.columns)
        except Exception:
            return []
    enc = encoding or detect_encoding(resolved)
    try:
        df_header = pd.read_csv(resolved, nrows=0, encoding=enc, low_memory=False)
        return list(df_header.columns)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Smart full-load (column selection, no chunking)
# ---------------------------------------------------------------------------

def read_csv_smart(
    path: str | Path,
    usecols: list[str] | None = None,
    encoding: str | None = None,
) -> pd.DataFrame:
    """Load a CSV into a single DataFrame with optional column selection.

    Parameters
    ----------
    path:
        CSV file (or .zip containing a CSV) to read.
    usecols:
        Subset of columns to load.  Columns not present in the file are
        silently ignored so callers can pass a superset.
    encoding:
        File encoding.  Auto-detected when omitted.  Ignored for zip files
        (pandas detects encoding from the bytes stream).
    """
    path = Path(path)
    resolved, data = _resolve_csv_from_path(path)
    if data is not None:
        # zip path — read from in-memory bytes
        effective_usecols: list[str] | None = None
        if usecols is not None:
            all_cols = list(pd.read_csv(io.BytesIO(data), nrows=0, low_memory=False).columns)
            effective_usecols = [c for c in usecols if c in all_cols] or None
        return pd.read_csv(io.BytesIO(data), usecols=effective_usecols, low_memory=False)

    enc = encoding or detect_encoding(resolved)

    # Intersect requested columns with those actually in the file
    effective_usecols = None
    if usecols is not None:
        all_cols = sniff_columns(resolved, encoding=enc)
        effective_usecols = [c for c in usecols if c in all_cols] or None

    return pd.read_csv(
        resolved,
        usecols=effective_usecols,
        encoding=enc,
        low_memory=False,
    )


# ---------------------------------------------------------------------------
# Chunked iterator
# ---------------------------------------------------------------------------

def iter_chunks(
    path: str | Path,
    usecols: list[str] | None = None,
    chunksize: int = CHUNK_SIZE,
    encoding: str | None = None,
) -> Generator[pd.DataFrame, None, None]:
    """Yield successive DataFrames of at most *chunksize* rows.

    Each chunk contains only the columns listed in *usecols* (after
    intersecting with the actual column names in the file).

    Parameters
    ----------
    path:
        CSV file to read.
    usecols:
        Columns to include in every chunk.  Pass ``None`` to keep all.
    chunksize:
        Maximum rows per yielded DataFrame.
    encoding:
        File encoding.  Auto-detected when omitted.
    """
    path = Path(path)
    enc = encoding or detect_encoding(path)

    effective_usecols: list[str] | None = None
    if usecols is not None:
        all_cols = sniff_columns(path, encoding=enc)
        effective_usecols = [c for c in usecols if c in all_cols] or None

    reader = pd.read_csv(
        path,
        usecols=effective_usecols,
        encoding=enc,
        chunksize=chunksize,
        low_memory=False,
    )
    for chunk in reader:
        yield chunk
