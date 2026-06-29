#!/usr/bin/env python3
"""
Parse a BinDefinitions.bdefs file and produce a Crystal Ball CSV.

Usage:
  python parse_bindef_to_crystalball.py --bindef "I:\\...\\BinDefinitions.bdefs" --out "C:\\tp\\workshop\\yield\\crystal_ball_input.csv"

If --out is omitted the output defaults to `crystal_ball_input.csv` in the current folder.
The script will try to preserve the same CSV structure as the attached `51K_bindef.csv`:
 - Header: `B/C,<parent-folder-name> DESCRIPTION`
 - Each mapping row: `FBxxx,DESCRIPTION`
"""
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
import sys


def parse_line(line: str, current_group: str | None = None) -> tuple[str, str] | None:
    s = line.strip()
    if not s:
        return None
    # ignore comment lines
    if s.startswith("#") or s.startswith(";"):
        return None
    # Prefer explicit Bin or LeafBin definitions with numeric id and quoted label:
    # e.g. Bin b101_pass_NAME   101   : "b101_pass_NAME",... or
    #      LeafBin b10000001 10000001 : "b10000001_..."
    m = re.match(r"^(?:Bin|LeafBin)\s+([^\s]+)\s+(\d+)\s*:\s*\"([^\"]+)\"", s, re.IGNORECASE)
    if m:
        name_token = m.group(1).strip()
        num = m.group(2).strip()
        quoted = m.group(3).strip()
        # Section-specific behavior:
        group = (current_group or "").lower()
        if group == "softbins" or group == "soft_bins" or group == "passfailbins":
            key = f"FB{num}"
            val = name_token.upper()
            return key, val
        elif group == "databins" or group == "data_bins" or group == "leafbins":
            key = f"DB{num}"
            # use token name uppercased (convert leading 'b' to 'B')
            val = name_token.upper()
            return key, val
        else:
            # default to FB to preserve previous behavior
            key = f"FB{num}"
            val = quoted.upper()
            return key, val

    # Detect explicit DB keys (e.g., DB20000001) and their labels. Many bindef
    # files list DB identifiers that the yield CSV uses; map those to labels.
    # Prefer quoted labels when present.
    mdb = re.search(r"\b(DB\d{5,})\b", s, re.IGNORECASE)
    if mdb:
        dbkey = mdb.group(1).upper()
        # Try to extract a quoted label on the same line
        mq = re.search(r'"([^\"]+)"', s)
        if mq:
            return dbkey, mq.group(1).upper()
        # If comma-separated, take second field
        if "," in s:
            parts = s.split(",", 1)
            return dbkey, parts[1].strip()
        # Fallback: remove the key token and use the rest of the line
        rest = re.sub(re.escape(mdb.group(0)), "", s).strip(" ,:-")
        if rest:
            return dbkey, rest.strip()

    # If already CSV-like (but not a Bin definition line that contains commas),
    # treat as preformatted CSV. Many Bin lines include a comma after the quoted
    # label (e.g. '"label",Pass;') so avoid splitting those by checking for
    # lines that start with 'Bin '.
    if "," in s and not s.lower().startswith("bin "):
        parts = s.split(",", 1)
        key = parts[0].strip()
        val = parts[1].strip()
        if key:
            return key, val

    # Try whitespace-separated fallbacks (existing behavior)
    m2 = re.match(r"^(FB\d+|B\d+|FB\w+|B\w+)\s+(.+)$", s, re.IGNORECASE)
    if m2:
        return m2.group(1).strip(), m2.group(2).strip()

    # Try colon or equals
    for sep in [":", "=", "-"]:
        if sep in s:
            parts = s.split(sep, 1)
            key = parts[0].strip()
            val = parts[1].strip()
            if re.match(r"^(FB\d+|B\d+|FB\w+|B\w+)$", key, re.IGNORECASE):
                return key, val

    # As a last resort, look for first token as key
    toks = s.split()
    if toks and re.match(r"^(FB\d+|B\d+|FB\w+|B\w+)$", toks[0], re.IGNORECASE):
        return toks[0], " ".join(toks[1:]).strip()
    return None


def build_header(bindef_path: Path) -> str:
    parent = bindef_path.parent.name
    # follow attached file header pattern
    return f"B/C,{parent} DESCRIPTION"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Parse BinDefinitions.bdefs into crystal_ball_input.csv")
    p.add_argument("--bindef", "-b", required=True, help="Path to BinDefinitions.bdefs")
    p.add_argument("--out", "-o", help="Output CSV path (default: crystal_ball_input.csv in current folder)")
    p.add_argument("--log", default="INFO", help="Log level")
    args = p.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    bindef = Path(args.bindef)
    if not bindef.exists():
        logging.error("Bindef not found: %s", bindef)
        return 2

    out_path = Path(args.out) if args.out else Path.cwd() / "crystal_ball_input.csv"

    entries: list[tuple[str, str]] = []
    current_group = None
    with bindef.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # track BinGroup sections
            mgrp = re.match(r"^\s*BinGroup\s*,?\s*([A-Za-z0-9_]+)", line, re.IGNORECASE)
            if mgrp:
                current_group = mgrp.group(1).strip()
                continue
            parsed = parse_line(line, current_group=current_group)
            if parsed:
                k, v = parsed
                # If we're in a DataBins section, normalize keys to DB{num}
                grp = (current_group or "").lower()
                if "data" in grp:
                    # If key already starts with DB keep it
                    if not re.match(r"^DB\d+", k, re.IGNORECASE):
                        num = None
                        # Prefer numeric id inside the value if it starts with B<digits>
                        mv = re.match(r"^[bB](\d{5,})", v)
                        if mv:
                            num = mv.group(1)
                        else:
                            # fallback to numeric inside the key
                            mk = re.search(r"(\d{5,})", k)
                            if mk:
                                num = mk.group(1)
                        if num:
                            k = f"DB{num}"
                entries.append((k, v))

    if not entries:
        logging.error("No valid entries parsed from %s", bindef)
        return 3

    header = build_header(bindef)
    with out_path.open("w", encoding="utf-8", newline="") as out:
        out.write(header + "\n")
        for k, v in entries:
            out.write(f"{k},{v}\n")

    logging.info("Wrote %d entries to %s", len(entries), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
