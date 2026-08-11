"""
scorer.py — pure-Python wafer spatial pattern scoring.

Algorithm is the exact Python mirror of _wmScorePattern / _wmScoreReticle
defined in _wpa_js.py.  Keep thresholds in sync.

Last verified in sync with _pipeline_html.py: 2026-05-16
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple


@dataclass
class WaferPattern:
    center:     float   # 0–1
    edge:       float   # 0–1
    donut:      float   # 0–1
    systematic: float   # 0–1
    reticle:    float   # 0–1
    random:     float   # 0–1
    primary:    str     # CENTER | EDGE | DONUT | SYSTEMATIC | RETICLE | RANDOM
    confidence: str     # LOW | MEDIUM | HIGH
    n_fail:     int
    edge_pct:   float   # % of fail dies in outer ~40% X radius (|x| >= 0.6 * max_abs_x)
    summary:    str     # one human-readable sentence


# Pattern display colors — must match _pColors in _wpa_js.py
PATTERN_COLORS: Dict[str, str] = {
    'CENTER':     '#c0392b',
    'EDGE':       '#e67e22',
    'DONUT':      '#8e44ad',
    'SYSTEMATIC': '#2471a3',
    'RETICLE':    '#1f618d',
    'RANDOM':     '#27ae60',
}


def score_wafer(
    xs: List[int],
    ys: List[int],
    fail_thr: int = 3,
    edge_exclude_rows: int = 1,
) -> WaferPattern:
    """
    Score spatial pattern of a set of dies.

    xs, ys       -- SORT_X / SORT_Y coordinates of the dies to score.
                    Pass only the dies you want scored (e.g. fail dies for a
                    specific IB; or all dies from a target population).
    fail_thr     -- IB >= fail_thr is considered a fail.  Not used for scoring
                    here (caller filters), but stored for reference.
    edge_exclude_rows -- rows/cols excluded from pattern scoring (same default
                    as the WPA "Excl. edge rows" control; default=1).

    Returns WaferPattern.
    """
    xs = [int(x) for x in xs]
    ys = [int(y) for y in ys]
    N = len(xs)

    if N == 0:
        return WaferPattern(
            center=0, edge=0, donut=0, systematic=0, reticle=0, random=0,
            primary='RANDOM', confidence='LOW', n_fail=0,
            edge_pct=0.0, summary='No dies',
        )

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # Edge exclusion
    if edge_exclude_rows > 0:
        scoring_xs = []
        scoring_ys = []
        for x, y in zip(xs, ys):
            if (x < x_min + edge_exclude_rows or x > x_max - edge_exclude_rows or
                    y < y_min + edge_exclude_rows or y > y_max - edge_exclude_rows):
                continue
            scoring_xs.append(x)
            scoring_ys.append(y)
    else:
        scoring_xs, scoring_ys = xs, ys

    N_score = len(scoring_xs)

    # edge_pct: % in outer ~40% of X radius (abs), using all (unexcluded) dies
    all_x_abs = [abs(x) for x in xs] or [1]
    max_abs_x = max(all_x_abs)
    edge_thr  = int(max_abs_x * 0.6)
    edge_n    = sum(1 for x in xs if abs(x) >= edge_thr)
    edge_pct  = edge_n / N * 100 if N else 0.0

    if N_score < 3:
        _summary = _make_summary('RANDOM', 0.0, edge_pct, edge_thr, N_score)
        return WaferPattern(
            center=0, edge=0, donut=0, systematic=0, reticle=0,
            random=1.0 if N_score > 0 else 0.0,
            primary='RANDOM', confidence='LOW', n_fail=N_score,
            edge_pct=edge_pct, summary=_summary,
        )

    x_ctr = (x_min + x_max) / 2
    y_ctr = (y_min + y_max) / 2
    x_rad = (x_max - x_min) / 2 or 1.0
    y_rad = (y_max - y_min) / 2 or 1.0

    # Normalize to unit disk
    xn = [(x - x_ctr) / x_rad for x in scoring_xs]
    yn = [(y - y_ctr) / y_rad for y in scoring_ys]

    B1 = B2 = B3 = B4 = B5 = B6 = 0
    q = [0, 0, 0, 0]
    for xi, yi in zip(xn, yn):
        r = math.sqrt(xi * xi + yi * yi)
        if   r < 0.15: B1 += 1
        elif r < 0.40: B2 += 1
        elif r < 0.60: B3 += 1
        elif r < 0.75: B4 += 1
        elif r < 0.90: B5 += 1
        else:          B6 += 1
        if   xi >= 0 and yi >= 0: q[0] += 1
        elif xi <  0 and yi >= 0: q[1] += 1
        elif xi <  0 and yi <  0: q[2] += 1
        else:                     q[3] += 1

    fC = (B1 + B2) / N_score
    fE = (B5 + B6) / N_score
    fM = (B3 + B4) / N_score
    eC, eE, eM = 0.16, 0.4375, 0.4025

    center_score     = _clamp((fC - eC) / (1 - eC))
    edge_score       = _clamp((fE - eE) / (1 - eE))
    mid_enrich       = max(0.0, (fM - eM) / (1 - eM))
    donut_score      = min(1.0, mid_enrich * 2 * (1 - max(center_score, edge_score) * 0.7))
    sample_conf      = min(1.0, N_score / 20)
    q_imbal          = (max(q) - min(q)) / N_score
    systematic_score = min(1.0, q_imbal * 2.5) * sample_conf
    dominated        = max(center_score, edge_score, donut_score, systematic_score)
    random_score     = _clamp(1.0 - dominated)

    confidence = 'LOW' if N_score < 20 else 'MEDIUM' if N_score < 50 else 'HIGH'

    scores = {
        'center':     round(center_score, 2),
        'edge':       round(edge_score, 2),
        'donut':      round(donut_score, 2),
        'systematic': round(systematic_score, 2),
        'reticle':    0.0,
        'random':     round(random_score, 2),
    }
    primary = _pick_primary(scores)
    summary = _make_summary(primary, scores[primary.lower()], edge_pct, edge_thr, N_score)

    return WaferPattern(
        n_fail=N_score,
        edge_pct=edge_pct,
        confidence=confidence,
        primary=primary,
        summary=summary,
        **scores,
    )


def score_wafer_reticle(
    xs: List[int],
    ys: List[int],
    reticle_map: Dict[Tuple[int, int], Tuple[int, int, int]],
    site_totals: Dict[str, int],
    edge_exclude_rows: int = 1,
) -> WaferPattern:
    """
    Like score_wafer() but also computes the RETICLE score.

    reticle_map   -- {(sort_x, sort_y): (site_x, site_y, shot_idx)}
                     same format as load_reticle_map() returns.
    site_totals   -- {"site_x,site_y": total_shots_at_site}
    """
    pat = score_wafer(xs, ys, edge_exclude_rows=edge_exclude_rows)
    ret_score = _score_reticle(xs, ys, reticle_map, site_totals)

    dominated = max(pat.center, pat.edge, pat.donut, pat.systematic, ret_score)
    random_score = _clamp(1.0 - dominated)

    scores = {
        'center':     pat.center,
        'edge':       pat.edge,
        'donut':      pat.donut,
        'systematic': pat.systematic,
        'reticle':    round(ret_score, 2),
        'random':     round(random_score, 2),
    }
    primary = _pick_primary(scores)
    summary = _make_summary(primary, scores[primary.lower()], pat.edge_pct,
                            int(max((abs(x) for x in xs), default=1) * 0.6),
                            pat.n_fail)

    return WaferPattern(
        n_fail=pat.n_fail,
        edge_pct=pat.edge_pct,
        confidence=pat.confidence,
        primary=primary,
        summary=summary,
        **scores,
    )


# ── internal helpers ──────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _pick_primary(scores: dict) -> str:
    order = ['center', 'edge', 'donut', 'systematic', 'reticle', 'random']
    best = max(order, key=lambda k: scores.get(k, 0))
    return best.upper()


def _score_reticle(
    xs: List[int],
    ys: List[int],
    reticle_map: Dict[Tuple[int, int], Tuple[int, int, int]],
    site_totals: Dict[str, int],
) -> float:
    """Mirror of _wmScoreReticle in _wpa_js.py."""
    if not reticle_map or not site_totals or not xs:
        return 0.0
    site_shots: Dict[str, set] = {}
    site_cnt: Dict[str, int] = {}
    N = len(xs)
    for x, y in zip(xs, ys):
        info = reticle_map.get((x, y))
        if info is None:
            continue
        sx, sy, shot_idx = info
        sk = f'{sx},{sy}'
        site_shots.setdefault(sk, set()).add(shot_idx)
        site_cnt[sk] = site_cnt.get(sk, 0) + 1
    if not site_shots:
        return 0.0
    weighted_sum = 0.0
    total_mapped = 0
    max_site_score = 0.0
    for sk, shots in site_shots.items():
        total_shots = site_totals.get(sk, 1)
        score = len(shots) / total_shots
        cnt = site_cnt[sk]
        total_mapped += cnt
        weighted_sum += score * cnt
        if score > max_site_score:
            max_site_score = score
    if not total_mapped:
        return 0.0
    raw = (weighted_sum / total_mapped) * 0.4 + max_site_score * 0.6
    sample_conf = min(1.0, N / 15)
    return min(1.0, raw * sample_conf)


def _make_summary(primary: str, score: float, edge_pct: float,
                  edge_thr: int, n: int) -> str:
    pct_s = f'{score * 100:.0f}%'
    if primary == 'EDGE':
        return f'Edge-biased ({edge_pct:.0f}% in outer cols, |X|\u2265{edge_thr})'
    if primary == 'CENTER':
        return f'Center-concentrated ({pct_s} score, n={n})'
    if primary == 'DONUT':
        return f'Donut/ring pattern ({pct_s} score, n={n})'
    if primary == 'SYSTEMATIC':
        return f'Systematic/quadrant imbalance ({pct_s} score, n={n})'
    if primary == 'RETICLE':
        return f'Reticle-correlated ({pct_s} score, n={n})'
    return f'Random / no dominant pattern (n={n})'
