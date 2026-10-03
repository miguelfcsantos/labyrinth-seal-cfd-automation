"""
Labyrinth seal leakage mass-flow scaling study
================================================

Compares CFD-derived mass flow rates for varying fin counts against the
classical scaling law (Eq. 2.3 from the thesis):

    m_dot(z2) = m_dot(z1) * sqrt(z1 / z2)

equivalently:   m_dot(z) * sqrt(z) = constant

for five step-height configurations, each run both WITHOUT and WITH a
prescribed inlet swirl:

    FS   - smooth            (no step,        step height =  0.000)
    FC   - convergent        (full step,      step height = -0.002)
    FCH  - convergent, half  (half step,      step height = -0.001)
    FD   - divergent         (full step,      step height =  0.002)
    FDH  - divergent, half   (half step,      step height =  0.001)

Non-swirl files:  values_<CASE>_F<n>.yml       e.g. values_FC_F3.yml
Swirl files:       values_s<CASE>_F<n>.yml      e.g. values_sFC_F3.yml

NOTE ON Eq. 2.3 ITSELF: the thesis derives this relation from an
incompressible-flow simplification and explicitly states it is only a
first approximation ("in erster Naeherung"), not sufficient on its own
for reliable design work, especially with compressible gases. The more
detailed Stodola/Martin relations (Eq. 2.5 / 2.6) exist precisely because
of that. So this script should be read as "how rough is Eq. 2.3 for our
geometries", not as a pass/fail validation of the equation.

NOTE ON F1: the first fin has no upstream step, so its flow behaviour is
fundamentally different from every following fin (the thesis flags this
directly in Sec. 2.1b as the reason tip-by-tip methods are preferred over
integral scaling for low fin counts). F1 is therefore excluded from every
Eq. 2.3 comparison in this script -> comparisons start at F2.

For every (case, swirl/non-swirl) combination the script:
  1. Reads all available result files (missing fin counts are skipped,
     not treated as an error).
  2. Extracts MassFlow for the chosen averaging-type block (flux / mass /
     area; 'mass' is used by default, see DEFAULT_AVG below).
  3. Builds every unordered pair (z1, z2) of fin counts with data,
     starting at MIN_FIN (default 2), consecutive pairs first (2-3, 3-4,
     ...) since those are the most physically meaningful, then all other
     combinations. Eq. 2.3 is symmetric in z1/z2, so 2-vs-3 and 3-vs-2 are
     literally the same comparison -> only computed once.
  4. Predicts m_dot at the larger fin count from the smaller one via
     Eq. 2.3 and computes absolute & percentage error against the CFD
     ("experimental") value.
  5. Writes one .txt report per (case, swirl/non-swirl).
  6. Produces ONE combined heat map PER CASE: since each pairwise error is
     a single number (not direction dependent), a plain symmetric matrix
     wastes half the plot. Instead, the upper-right triangle shows the
     non-swirl errors and the lower-left triangle shows the swirl errors
     for the SAME case, so you can compare swirl vs. non-swirl fit quality
     at a glance in one square per geometry (5 squares total).
  7. Produces a trend plot per case (m_dot vs fin count, swirl & non-swirl,
     with the Eq. 2.3 curve anchored at the smallest available fin count)
     -- this is often more intuitive to read than an error grid, since you
     directly see how the CFD points sit relative to the 1/sqrt(z) curve.
  8. Produces a combined bar-chart summary (mean |err|) across all
     5 cases, swirl vs. non-swirl side by side.

Usage:
    python laby_eq23_check.py

Everything you're likely to want to tweak (paths, which fins to include,
colors, scaling of the heat maps, ...) lives in the CONFIG block below,
with comments on what each setting does.
"""

import re
import math
import itertools
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

#=============================================================================



# --- paths ------------------------------------------------------------------
# Directory containing the values_*.yml result files, and where outputs go.
# Adjust these to your own setup (relative or absolute paths both work).

VALUES_DIR = Path("./data/fins")
OUT_DIR = VALUES_DIR / "eq23_results"
OUT_DIR.mkdir(exist_ok=True, parents=True)

# --- case / geometry definitions ---------------------------------------------
# Base name (NO "s" prefix) -> human readable label used in titles/reports.
# For every base case the script automatically looks for both variants:
#   non-swirl: values_<CASE>_F<n>.yml
#   swirl:     values_s<CASE>_F<n>.yml
CASES = {
    "FS":  "Smooth Configuration",
    "FC":  "Convergent Configuration",
    "FCH": "Convergent Half Step Configuration",
    "FD":  "Divergent Configuration",
    "FDH": "Divergent Half Step Configuration",
}

# --- fin counts ---------------------------------------------------------------
# F1 is intentionally EXCLUDED (see note at top of file: no upstream step ->
# not comparable to F2+ under Eq. 2.3). Change MIN_FIN back to 1 if you ever
# want to include it again, e.g. for a sanity check of how much worse it is.
MIN_FIN = 2
MAX_FIN = 9
FIN_RANGE = range(MIN_FIN, MAX_FIN + 1)

AVG_TYPES = ["flux", "mass", "area"]
DEFAULT_AVG = "mass"       # which averaging block's MassFlow value is used

# --- heat map appearance ------------------------------------------------------
# Diverging colormap: errors can be positive (Eq. 2.3 over-predicts) or
# negative (under-predicts), so the colormap should be centered on 0.
# Other good diverging options to try: "coolwarm", "bwr", "PuOr", "seismic".
HEATMAP_CMAP = "RdBu_r"

# How the color-scale limits (+/- vmax) are picked:
#   "auto"  -> each of the 5 case-plots gets its own vmax, based on the
#              largest |error| found IN THAT PLOT. Gives the best contrast
#              per plot, but the 5 plots are then NOT directly comparable
#              to each other by color alone (check the colorbar numbers).
#   "fixed" -> all 5 case-plots share the same +/- FIXED_VMAX_PCT range, so
#              colors ARE directly comparable across geometries. Good once
#              you know roughly what error range to expect.
HEATMAP_SCALE_MODE = "auto"
FIXED_VMAX_PCT = 5.0        # only used when HEATMAP_SCALE_MODE == "fixed"

# Font size of the numbers written inside each heat-map cell.
HEATMAP_ANNOT_FONTSIZE = 8

# Figure size (inches) and resolution (dots per inch) of saved PNGs.
HEATMAP_FIGSIZE = (6.5, 5.8)
HEATMAP_DPI = 150

# Labels used to mark the two triangles in the combined plots.
NONSWIRL_LABEL = "no swirl (upper-right)"
SWIRL_LABEL = "swirl (lower-left)"

# --- trend plot (m_dot vs fin count) ------------------------------------------
ENABLE_TREND_PLOTS = True
TREND_FIGSIZE = (6.5, 4.5)
TREND_DPI = 150
TREND_NONSWIRL_COLOR = "tab:blue"
TREND_SWIRL_COLOR = "tab:red"
TREND_CURVE_LINESTYLE = "--"

# --- summary bar chart ---------------------------------------------------------
# Single-panel bar chart of mean |err| per case, swirl vs. non-swirl.
# (The max |err| panel was dropped -- only the mean is of interest here.)
SUMMARY_FIGSIZE = (6.5, 4.5)
SUMMARY_DPI = 150
SUMMARY_NONSWIRL_COLOR = "tab:blue"
SUMMARY_SWIRL_COLOR = "tab:red"


# =============================================================================
# PARSER
# =============================================================================
def parse_result_file(path: Path) -> dict:
    """
    Parses one 'values_*.yml' result file and returns:
        { avg_type: {field_name: value, ...}, ... }

    The files are not strict YAML - they contain repeated
    '# Averaging type: XXX' blocks with 'key:   value' pairs interleaved
    with comment lines, so we parse defensively, line by line.
    """
    data = {}
    current_avg = None
    header_re = re.compile(r"#\s*Averaging type:\s*(\w+)", re.IGNORECASE)
    kv_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^\s#]+)")

    text = path.read_text(errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = header_re.search(line)
        if m:
            current_avg = m.group(1).strip().lower()
            data.setdefault(current_avg, {})
            continue

        if line.startswith("#"):
            continue

        m = kv_re.match(line)
        if m and current_avg is not None:
            key, val = m.group(1), m.group(2)
            try:
                val = float(val)
            except ValueError:
                pass
            data[current_avg][key] = val

    return data


def load_variant_data(case_base: str, swirl: bool) -> dict:
    """
    Loads all available fin-count files for one (case, swirl) variant.
    Returns: { fin_count: {avg_type: {field: value}} }
    """
    prefix = "s" if swirl else ""
    result = {}
    for n in FIN_RANGE:
        fpath = VALUES_DIR / f"values_{prefix}{case_base}_F{n}.yml"
        if fpath.exists():
            result[n] = parse_result_file(fpath)
        else:
            print(f"  [!] missing file: {fpath}")
    return result


def get_mass_flow(case_data: dict, n: int, avg_type: str = DEFAULT_AVG):
    """Safe accessor for MassFlow of a given fin count / averaging type."""
    try:
        return case_data[n][avg_type]["MassFlow"]
    except KeyError:
        return None


# =============================================================================
# EQUATION 2.3
# =============================================================================
def predict_mdot(m_ref: float, z_ref: int, z_target: int) -> float:
    """Eq. 2.3: m_dot(z_target) = m_dot(z_ref) * sqrt(z_ref / z_target)"""
    return m_ref * math.sqrt(z_ref / z_target)


def compare_pair(m1, z1, m2, z2):
    """
    Predicts the larger-z mass flow from the smaller-z one via Eq. 2.3 and
    returns error metrics. Because m*sqrt(z) = const is symmetric, the
    direction of prediction doesn't matter for consistency checking - we
    always predict larger-z from smaller-z for readability.
    """
    if z1 > z2:
        z1, z2 = z2, z1
        m1, m2 = m2, m1

    m2_pred = predict_mdot(m1, z1, z2)
    abs_err = m2_pred - m2
    pct_err = abs_err / m2 * 100.0 if m2 != 0 else float("nan")
    return z1, z2, m1, m2, m2_pred, abs_err, pct_err


# =============================================================================
# PER-VARIANT ANALYSIS (one case, one swirl/non-swirl variant)
# =============================================================================
def analyze_variant(case_base: str, swirl: bool, avg_type: str = DEFAULT_AVG):
    case_data = load_variant_data(case_base, swirl)
    available_fins = sorted(case_data.keys())

    mdots = {n: get_mass_flow(case_data, n, avg_type) for n in available_fins}
    mdots = {n: m for n, m in mdots.items() if m is not None}
    available_fins = sorted(mdots.keys())

    label = f"{'s' if swirl else ''}{case_base}"
    if len(available_fins) < 2:
        print(f"  [!] variant {label}: not enough data to compare (need >=2 fin counts)")
        return None

    all_pairs = list(itertools.combinations(available_fins, 2))
    consecutive_pairs = [(a, b) for a, b in all_pairs if b - a == 1]
    other_pairs = [p for p in all_pairs if p not in consecutive_pairs]

    rows = []
    for z1, z2 in consecutive_pairs + other_pairs:
        rows.append(compare_pair(mdots[z1], z1, mdots[z2], z2))

    # pair -> pct error lookup, used later to build the combined matrix
    pct_err_lookup = {(r[0], r[1]): r[-1] for r in rows}

    return {
        "case_base": case_base,
        "swirl": swirl,
        "label": label,
        "avg_type": avg_type,
        "available_fins": available_fins,
        "mdots": mdots,
        "consecutive_pairs": consecutive_pairs,
        "other_pairs": other_pairs,
        "rows": rows,
        "pct_err_lookup": pct_err_lookup,
    }


# =============================================================================
# TEXT REPORT (one per case/variant)
# =============================================================================
def write_report(result: dict, out_path: Path):
    label = result["label"]
    avg_type = result["avg_type"]
    fins = result["available_fins"]
    mdots = result["mdots"]
    rows = result["rows"]
    n_consec = len(result["consecutive_pairs"])

    lines = []
    lines.append(f"Eq. 3.33 scaling check  -  Variant: {label} ({CASES.get(result['case_base'], result['case_base'])}"
                  f"{', with swirl' if result['swirl'] else ', no swirl'})")
    lines.append(f"Averaging type used:   {avg_type}")
    lines.append(f"Fin counts compared:   {fins}  (F1 excluded on purpose, see script header)")
    lines.append("")
    lines.append("Raw mass flow values (CFD / 'experimental'):")
    for n in fins:
        lines.append(f"    F{n}:  m_dot = {mdots[n]:.6f} kg/s")
    lines.append("")
    lines.append("Eq. 3.33:  m_dot(z2) = m_dot(z1) * sqrt(z1 / z2)")
    lines.append("(symmetric in z1/z2, so direction of prediction does not matter)")
    lines.append("")

    header = (f"{'z1':>3} {'z2':>3} {'m1 (exp)':>12} {'m2 (exp)':>12} "
              f"{'m2 (theo)':>12} {'delta abs':>10} {'delta %':>8}")
    sep = "-" * len(header)

    lines.append("=== Consecutive fin-count pairs (z2 = z1 + 1) ===")
    lines.append(header)
    lines.append(sep)
    for r in rows[:n_consec]:
        z1, z2, m1, m2, m2p, abs_e, pct_e = r
        lines.append(f"{z1:>3} {z2:>3} {m1:12.6f} {m2:12.6f} {m2p:12.6f} {abs_e:10.6f} {pct_e:8.3f}")

    lines.append("")
    lines.append("=== All other unordered fin-count pairs ===")
    lines.append(header)
    lines.append(sep)
    for r in rows[n_consec:]:
        z1, z2, m1, m2, m2p, abs_e, pct_e = r
        lines.append(f"{z1:>3} {z2:>3} {m1:12.6f} {m2:12.6f} {m2p:12.6f} {abs_e:10.6f} {pct_e:8.3f}")

    pct_errors = [r[-1] for r in rows]
    lines.append("")
    lines.append("=== Summary statistics (percentage error, all pairs) ===")
    lines.append(f"  mean(|err|):   {np.mean(np.abs(pct_errors)):.3f} %")
    lines.append(f"  max(|err|):    {np.max(np.abs(pct_errors)):.3f} %")
    lines.append(f"  std(err):      {np.std(pct_errors):.3f} %")

    out_path.write_text("\n".join(lines))
    print(f"  -> wrote {out_path}")


# =============================================================================
# COMBINED (swirl + non-swirl) HEAT MAP -- one square per case
# =============================================================================
def build_combined_matrix(result_noswirl: dict, result_swirl: dict):
    """
    Builds one square error matrix per case:
      - upper-right triangle (col > row) = non-swirl pct error
      - lower-left triangle  (col < row) = swirl pct error
      - diagonal = NaN (a fin compared with itself is meaningless)

    Uses the UNION of fin counts available in either variant so both
    triangles are shown on the same axis labels; missing combinations are
    left as NaN (blank) rather than guessed.
    """
    fins_noswirl = set(result_noswirl["available_fins"]) if result_noswirl else set()
    fins_swirl = set(result_swirl["available_fins"]) if result_swirl else set()
    all_fins = sorted(fins_noswirl | fins_swirl)

    n = len(all_fins)
    matrix = np.full((n, n), np.nan)
    idx = {f: i for i, f in enumerate(all_fins)}

    if result_noswirl:
        for (z1, z2), pct in result_noswirl["pct_err_lookup"].items():
            i, j = idx[z1], idx[z2]   # z1 < z2 always (see compare_pair)
            matrix[i, j] = pct        # upper-right triangle

    if result_swirl:
        for (z1, z2), pct in result_swirl["pct_err_lookup"].items():
            i, j = idx[z1], idx[z2]
            matrix[j, i] = pct         # mirrored -> lower-left triangle

    return all_fins, matrix


def plot_combined_heatmap(case_base: str, result_noswirl: dict, result_swirl: dict, out_path: Path):
    fins, matrix = build_combined_matrix(result_noswirl, result_swirl)
    if len(fins) < 2:
        print(f"  [!] case {case_base}: not enough combined data for a heat map")
        return

    if HEATMAP_SCALE_MODE == "fixed":
        vmax = FIXED_VMAX_PCT
    else:
        vmax = np.nanmax(np.abs(matrix)) if np.any(~np.isnan(matrix)) else 1.0

    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    im = ax.imshow(matrix, cmap=HEATMAP_CMAP, vmin=-vmax, vmax=vmax)

    ax.set_xticks(range(len(fins)))
    ax.set_yticks(range(len(fins)))
    ax.set_xticklabels([f"F{n}" for n in fins])
    ax.set_yticklabels([f"F{n}" for n in fins])
    ax.set_title(f"Error Heatmap (%) - {CASES.get(case_base, case_base)}\n"
                 f"{NONSWIRL_LABEL}  vs.  {SWIRL_LABEL}")

    for i in range(len(fins)):
        for j in range(len(fins)):
            val = matrix[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                        fontsize=HEATMAP_ANNOT_FONTSIZE)

    # thin diagonal guide line, purely visual, to separate the two triangles
    ax.plot([-0.5, len(fins) - 0.5], [-0.5, len(fins) - 0.5], color="black", linewidth=0.8)

    fig.colorbar(im, ax=ax, label="Error (%)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=HEATMAP_DPI)
    plt.close(fig)
    print(f"  -> wrote {out_path}")


# =============================================================================
# TREND PLOT (m_dot vs fin count, with Eq. 2.3 curve) -- one per case
# =============================================================================
def plot_trend(case_base: str, result_noswirl: dict, result_swirl: dict, out_path: Path):
    fig, ax = plt.subplots(figsize=TREND_FIGSIZE)

    for result, color, marker, label in [
        (result_noswirl, TREND_NONSWIRL_COLOR, "o", "CFD, no swirl"),
        (result_swirl, TREND_SWIRL_COLOR, "s", "CFD, swirl"),
    ]:
        if result is None:
            continue
        fins = result["available_fins"]
        mdots = [result["mdots"][f] for f in fins]
        ax.scatter(fins, mdots, color=color, marker=marker, label=label, zorder=3)

        # Eq. 2.3 curve anchored at the smallest available fin count
        z_ref = fins[0]
        m_ref = mdots[0]
        z_curve = np.linspace(fins[0], fins[-1], 200)
        m_curve = m_ref * np.sqrt(z_ref / z_curve)
        ax.plot(z_curve, m_curve, color=color, linestyle=TREND_CURVE_LINESTYLE,
                 alpha=0.7, label=f"Eq. 3.33 curve ({label.split(',')[1].strip()})")

    ax.set_xlabel("Number of fins, z")
    ax.set_ylabel("Mass flow, kg/s")
    ax.set_title(f"Mass flow vs. fin count - {case_base} ({CASES.get(case_base, case_base)})")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=TREND_DPI)
    plt.close(fig)
    print(f"  -> wrote {out_path}")


# =============================================================================
# COMBINED SUMMARY PLOT (mean |err| per case, swirl vs. non-swirl)
# =============================================================================
def plot_summary(all_results: dict, out_path: Path):
    """
    Single-panel bar chart: mean |err| per case, swirl vs. non-swirl side by
    side. (Previously this also had a second panel for max |err|; that's
    dropped now since only the mean is of interest.)
    """
    cases = list(CASES.keys())
    means_ns, means_s = [], []

    for case_base in cases:
        r_ns = all_results.get((case_base, False))
        r_s = all_results.get((case_base, True))

        def mean_abs_err(r):
            if r is None:
                return np.nan
            pct_errors = [row[-1] for row in r["rows"]]
            return np.mean(np.abs(pct_errors))

        means_ns.append(mean_abs_err(r_ns))
        means_s.append(mean_abs_err(r_s))

    x = np.arange(len(cases))
    width = 0.35

    fig, ax = plt.subplots(figsize=SUMMARY_FIGSIZE)

    ax.bar(x - width / 2, means_ns, width, label="no swirl", color=SUMMARY_NONSWIRL_COLOR)
    ax.bar(x + width / 2, means_s, width, label="swirl", color=SUMMARY_SWIRL_COLOR)
    ax.set_xticks(x)
    ax.set_xticklabels(cases)
    ax.set_ylabel("mean error (%)")
    ax.set_title("Eq. 3.33 scaling error by seal configuration (mean error)")
    ax.legend()

    fig.tight_layout()
    fig.savefig(out_path, dpi=SUMMARY_DPI)
    plt.close(fig)
    print(f"-> wrote {out_path}")


# =============================================================================
# RUN
# =============================================================================
def main():
    all_results = {}   # (case_base, swirl) -> result dict or None

    for case_base in CASES:
        for swirl in (False, True):
            label = f"{'s' if swirl else ''}{case_base}"
            print(f"Processing variant {label} ...")
            result = analyze_variant(case_base, swirl, avg_type=DEFAULT_AVG)
            all_results[(case_base, swirl)] = result
            if result is not None:
                write_report(result, OUT_DIR / f"eq23_report_{label}.txt")

    for case_base in CASES:
        r_ns = all_results.get((case_base, False))
        r_s = all_results.get((case_base, True))
        if r_ns is None and r_s is None:
            continue
        plot_combined_heatmap(case_base, r_ns, r_s, OUT_DIR / f"eq23_heatmap_{case_base}.pdf")
        if ENABLE_TREND_PLOTS:
            plot_trend(case_base, r_ns, r_s, OUT_DIR / f"eq23_trend_{case_base}.pdf")

    plot_summary(all_results, OUT_DIR / "eq23_summary_comparison.pdf")
    print("\nDone. Results in:", OUT_DIR.resolve())


if __name__ == "__main__":
    main()
