#!/usr/bin/env python3
"""
plot_velocity_profiles.py
─────────────────────────
Fully automatic plotting script for CARA-o PLOTS velocity profiles.
No user interaction required — run it after saca.py and it discovers
everything from the folder structure saca wrote.

Folder structure (written by saca2.py):
  LOCATIONS_BASE / {X} / {vel}_{X} / {conv|div} / {sim}.dat

For each sim found, only its actual case folder (conv OR div) is plotted —
never both, since saca already determined that.

For X < 30, the exp.csv and den.csv reference files are taken from the X=30
folder matching the SAME velocity and SAME case:
  e.g.  ax / conv / x=-50  →  LOCATIONS/30/ax_30/conv/exp.csv
        tan / div / x=0    →  LOCATIONS/30/tan_30/div/exp.csv

Output plots go to:
  PLOTS_BASE / PLOTS / {sim} / {vel}_{letter}_{X}.png

Skipping:
  If a .png already exists at the output path, it is skipped entirely.
  Use --force to override and re-plot everything.

Usage:
    python3 plot_velocity_profiles.py
    python3 plot_velocity_profiles.py --force
"""

import re
import os
import sys
import string
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from scipy.interpolate import make_interp_spline

# ── Configuration ─────────────────────────────────────────────────────────
BASE            = "/your/path/PLOTS"
LOCATIONS_BASE  = os.path.join(BASE, "LOCATIONS")
PLOTS_BASE      = os.path.join(BASE, "PLOTS")

VELOCITIES  = ["ax", "tan"]
CASES       = ["conv", "div"]
N_INTERP    = 500

# Folders with X < CSV_REF_THRESHOLD borrow exp/den CSVs from REF_X,
# matching the same velocity type and case.
CSV_REF_THRESHOLD = 30        # strictly less than → borrows
REF_X             = 30        # integer; folder name is str(REF_X) = "30"

VEL_LABELS  = {"ax": r"$C_{ax}$", "tan": r"$C_{tan}$"}
CASE_LABELS = {"conv": "Convergent", "div": "Divergent"}
X_LIMS      = {"ax": (-0.3, 0.3), "tan": (-0.1, 1.0)}
Y_LIMS      = {"ax": (0.0,  1.0), "tan": (0.0, 1.0)}

SIM_COLOR   = "#2563EB"


# ── Letter-index helpers ───────────────────────────────────────────────────

def _build_label_sequence(n: int) -> list:
    labels = list(string.ascii_lowercase)
    labels += ["z" + c for c in string.ascii_lowercase]
    if n > len(labels):
        raise ValueError(f"Need {n} location labels but only {len(labels)} defined.")
    return labels[:n]


def build_location_labels(locations: list) -> dict:
    sorted_locs = sorted(locations, key=lambda x: int(x))
    labels = _build_label_sequence(len(sorted_locs))
    return {loc: lbl for loc, lbl in zip(sorted_locs, labels)}


# ── Filename helpers ───────────────────────────────────────────────────────

def plot_filename(vel: str, label: str, location: str) -> str:
    val = int(location)
    return f"{vel}_{label}_{val}.png"


# ── CSV source resolution ──────────────────────────────────────────────────

def csv_folder(location: str, vel: str, case: str) -> str:
    """
    Return the directory where exp.csv / den.csv should be read from.

    Rule:
      int(location) < CSV_REF_THRESHOLD  →  use X=REF_X folder,
                                             same vel, same case
      otherwise                          →  use the location's own folder
    """
    if int(location) < CSV_REF_THRESHOLD:
        ref = str(REF_X)
        folder = os.path.join(LOCATIONS_BASE, ref, f"{vel}_{ref}", case)
        print(f"         [CSV ref] x={location} < {CSV_REF_THRESHOLD} "
              f"→ borrowing from LOCATIONS/{ref}/{vel}_{ref}/{case}/")
    else:
        folder = os.path.join(LOCATIONS_BASE, location, f"{vel}_{location}", case)
    return folder


# ── Discovery ──────────────────────────────────────────────────────────────

def discover_locations() -> list:
    if not os.path.isdir(LOCATIONS_BASE):
        return []
    locs = []
    for entry in os.listdir(LOCATIONS_BASE):
        if os.path.isdir(os.path.join(LOCATIONS_BASE, entry)):
            try:
                int(entry)
                locs.append(entry)
            except ValueError:
                pass
    return sorted(locs, key=lambda x: int(x))


def discover_sims_for(location: str, vel: str, case: str) -> list:
    folder = os.path.join(LOCATIONS_BASE, location, f"{vel}_{location}", case)
    if not os.path.isdir(folder):
        return []
    return [
        os.path.splitext(f)[0]
        for f in os.listdir(folder)
        if f.endswith(".dat")
    ]


def discover_all_sims() -> set:
    sims = set()
    for loc in discover_locations():
        for vel in VELOCITIES:
            for case in CASES:
                sims.update(discover_sims_for(loc, vel, case))
    return sims


def find_case_for_sim(sim: str, location: str, vel: str):
    found = []
    for case in CASES:
        path = os.path.join(LOCATIONS_BASE, location,
                            f"{vel}_{location}", case, f"{sim}.dat")
        if os.path.isfile(path):
            found.append(case)
    if len(found) == 2:
        print(f"  [WARN] {sim} found in BOTH conv and div "
              f"for x={location} {vel} — using conv")
        return "conv"
    return found[0] if found else None


# ── Parsing ────────────────────────────────────────────────────────────────

def parse_tecplot_dat(filepath: str):
    with open(filepath, 'r') as f:
        content = f.read()
    var_names = re.findall(r'"([^"]+)"', re.search(
        r'VARIABLES\s*=\s*(.*?)(?=\n[A-Z])', content, re.DOTALL).group(0))
    n_pts  = int(re.search(r'I\s*=\s*(\d+)', content).group(1))
    n_data = n_pts - 1 if 'CELLCENTERED' in content else n_pts
    dt_idx = content.find('DT=')
    nums   = [float(x) for x in re.findall(
        r'[+-]?\d+\.\d+[Ee][+-]\d+', content[dt_idx:])]
    data = {}
    for i, name in enumerate(var_names):
        data[name] = np.array(nums[i * n_data:(i + 1) * n_data])
    return data, var_names


def read_csv_xy(filepath: str):
    raw = np.loadtxt(filepath, delimiter=',')
    idx = np.argsort(raw[:, 1])
    return raw[idx, 0], raw[idx, 1]


def load_sim_dat(dat_path: str):
    if not os.path.isfile(dat_path):
        return None
    try:
        dat_data, var_names = parse_tecplot_dat(dat_path)
        dat_h   = dat_data[var_names[0]]
        dat_vel = dat_data[var_names[1]]
        idx     = np.argsort(dat_h)
        dat_h, dat_vel = dat_h[idx], dat_vel[idx]
        spline   = make_interp_spline(dat_h, dat_vel, k=3)
        h_fine   = np.linspace(dat_h.min(), dat_h.max(), N_INTERP)
        vel_fine = spline(h_fine)
        return dict(h_fine=h_fine, vel_fine=vel_fine,
                    dat_h=dat_h, dat_vel=dat_vel)
    except Exception as exc:
        print(f"  [WARN] Could not parse {dat_path}: {exc}")
        return None


# ── Plotting ───────────────────────────────────────────────────────────────

def make_plot(sim: str, location: str, vel: str, case: str,
              label: str, out_dir: str, force: bool = False) -> str:
    """
    Returns:
      'new'     — plot was created successfully
      'skipped' — plot already existed and force=False
      'failed'  — something went wrong
    """

    fname    = plot_filename(vel, label, location)
    out_path = os.path.join(out_dir, fname)

    # ── Skip if already plotted ──────────────────────────────────────────
    if not force and os.path.isfile(out_path):
        print(f"  [SKIP] {fname} already exists.")
        return 'skipped'

    # .dat always comes from the sim's own location folder
    dat_path = os.path.join(LOCATIONS_BASE, location,
                            f"{vel}_{location}", case, f"{sim}.dat")

    # CSVs: same vel + same case, but redirect to X=30 when location < 30
    ref_dir  = csv_folder(location, vel, case)
    den_path = os.path.join(ref_dir, "den.csv")
    exp_path = os.path.join(ref_dir, "exp.csv")

    print(f"         dat : {dat_path}")
    print(f"         den : {den_path}  exists={os.path.isfile(den_path)}")
    print(f"         exp : {exp_path}  exists={os.path.isfile(exp_path)}")

    # Load sim .dat
    sim_data = load_sim_dat(dat_path)
    if sim_data is None:
        print(f"  [FAIL] {sim}.dat not found or unreadable.")
        return 'failed'

    # Load CSVs (optional — warn if missing)
    den_vel = den_h = None
    if os.path.isfile(den_path):
        try:
            den_vel, den_h = read_csv_xy(den_path)
        except Exception as exc:
            print(f"  [WARN] Could not read den.csv: {exc}")
    else:
        print(f"  [WARN] den.csv not found: {den_path}")

    exp_vel = exp_h = None
    if os.path.isfile(exp_path):
        try:
            exp_vel, exp_h = read_csv_xy(exp_path)
        except Exception as exc:
            print(f"  [WARN] Could not read exp.csv: {exc}")
    else:
        print(f"  [WARN] exp.csv not found: {exp_path}")

    # Build figure
    fig, ax = plt.subplots(figsize=(7, 9))
    ax.axvline(0, color='#94A3B8', linewidth=0.8, linestyle='--', zorder=1)

    if exp_vel is not None:
        ax.scatter(exp_vel, exp_h,
                   color='#16A34A', s=60, zorder=6,
                   marker='D', facecolors='none', edgecolors='#16A34A',
                   linewidths=1.4, label='Experimental (Denecke)')

    if den_vel is not None:
        ax.plot(den_vel, den_h,
                color='#000000', linewidth=2.0,
                label='CFD comparison (Denecke)')

    ax.plot(sim_data['vel_fine'], sim_data['h_fine'],
            color=SIM_COLOR, linewidth=2.0,
            label=f'TRACE {sim} (interpolated)')

    ax.set_xlim(X_LIMS[vel])
    ax.set_ylim(Y_LIMS[vel])
    ax.set_xlabel(f'{VEL_LABELS[vel]}', fontsize=13, labelpad=8)
    ax.set_ylabel('Relative Channel Height', fontsize=13, labelpad=8)

    borrowed = int(location) < CSV_REF_THRESHOLD
    ref_note = f" [ref CSVs from x={REF_X}]" if borrowed else ""
    ax.set_title(
        f'{VEL_LABELS[vel]} — {CASE_LABELS[case]} — x = {location} mm{ref_note}\n'
        f'Simulation: {sim}',
        fontsize=12, pad=14)

    ax.xaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(True, which='major', linestyle='--', linewidth=0.6, alpha=0.5)
    ax.grid(True, which='minor', linestyle=':', linewidth=0.4, alpha=0.3)

    legend_loc = 'upper right' if vel == 'tan' else 'upper left'
    ax.legend(fontsize=9.5, framealpha=0.92, loc=legend_loc)
    fig.tight_layout()

    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  [OK]   {fname}")
    return 'new'


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CARA-o velocity profile plotter")
    parser.add_argument(
        "--force", action="store_true",
        help="Re-plot even if the output .png already exists")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║   CARA-o Velocity Profile Plotter    ║")
    print("║   (fully automatic — no prompts)     ║")
    print("╚══════════════════════════════════════╝\n")

    if args.force:
        print("  !! --force mode: existing plots will be overwritten !!\n")

    locations = discover_locations()
    if not locations:
        print(f"[ERROR] No location folders found in:\n  {LOCATIONS_BASE}")
        sys.exit(1)
    print(f"Locations: {', '.join(locations)} mm")

    loc_labels = build_location_labels(locations)
    print("Location labels:")
    for loc in sorted(locations, key=lambda x: int(x)):
        ref_note = (f"  ← CSVs borrowed from x={REF_X}"
                    if int(loc) < CSV_REF_THRESHOLD else "")
        print(f"  {loc_labels[loc]} = x={loc}{ref_note}")

    all_sims = discover_all_sims()
    if not all_sims:
        print(f"[ERROR] No .dat files found anywhere under:\n  {LOCATIONS_BASE}")
        sys.exit(1)
    print(f"\nSimulations: {', '.join(sorted(all_sims))}\n")

    n_new, n_skipped, n_failed = 0, 0, 0

    for sim in sorted(all_sims):
        out_dir = os.path.join(PLOTS_BASE, "PLOTS", sim)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n── {sim}  →  {out_dir}")

        for vel in VELOCITIES:
            for loc in sorted(locations, key=lambda x: int(x)):
                case = find_case_for_sim(sim, loc, vel)
                if case is None:
                    continue
                label = loc_labels[loc]
                print(f"\n  {vel} / x={loc} / {case} / label={label}")
                result = make_plot(sim, loc, vel, case, label, out_dir,
                                   force=args.force)
                if result == 'new':
                    n_new += 1
                elif result == 'skipped':
                    n_skipped += 1
                else:
                    n_failed += 1

    print(f"\nDone — {n_new} new  |  {n_skipped} skipped  |  {n_failed} failed")
    print(f"Plots folder: {os.path.join(PLOTS_BASE, 'PLOTS')}\n")


if __name__ == "__main__":
    main()
