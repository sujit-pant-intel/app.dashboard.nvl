# Wafer Map Analysis Agent (N2-Safe)

## Purpose
This agent performs **wafer-level spatial analysis** to identify
systematic defect patterns such as **center, edge, donut, and geo-related signatures**.

The agent is designed for **advanced-node (e.g., TSMC N2-class) workflows**, where
wafer-map patterns are used as **early indicators**, not final root-cause conclusions.

---

## Key Distinction (IMPORTANT)

This agent uses two different concepts that must not be confused:

- **wfmap** → analysis and spatial statistics  
- **wafermap** → geometry and visualization only

❗ Pattern detection logic lives in this agent, **not** in either package.

---

## Supported Pattern Types
The agent scores (probabilistically) the following industry-standard wafer patterns:

- Center bias
- Edge bias
- Edge-ring
- Donut / hollow-center
- Near-full / systematic
- Random / noise-dominated

Pattern taxonomy follows **WM‑811K / KLA Klarity-style conventions**, adapted for N2.

---

## Recommended Libraries

### Analysis (primary)
- **wfmap**
  - Wafer heatmaps
  - Radial and spatial aggregation
  - Trend and density analysis
  - Systematic vs random behavior

### Geometry / Visualization (optional)
- **wafermap**
  - Wafer geometry
  - Edge exclusion
  - Notch and orientation handling
  - Plotting and HTML/PNG export

No library is assumed to provide direct “donut / center” labels.

---

## High-Level Workflow

1. Load die-level wafer data (CSV / DataFrame)
2. Normalize die coordinates to wafer center
3. Apply edge exclusion logic
4. Compute spatial metrics:
   - Radial defect density
   - Center vs edge ratio
   - Ring-band variance
5. Convert metrics into **pattern scores**
6. Assign dominant pattern (soft classification)
7. Optionally aggregate results by reticle / field

---

## Output Contract

The agent produces structured, machine-readable results:

```json
{
  "wafer_id": "W12",
  "pattern_scores": {
    "center": 0.71,
    "edge": 0.18,
    "donut": 0.07,
    "systematic": 0.82,
    "random": 0.18
  },
  "primary_pattern": "CENTER",
  "confidence": "MEDIUM",
  "notes": "Weak center-dominant density; likely parametric tail behavior"
}