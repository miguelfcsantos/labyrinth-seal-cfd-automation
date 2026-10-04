#!/usr/bin/env python3
"""
Automatic grid generator for POST — single simulation.

Usage:
    python3 griddd_single.py /your/path/local/sims/LC

Reads parameters_used.yml and geometry_used.yml from the sim folder,
computes the grid, and writes grid.dat into the sim folder root.
"""

import sys
import os
import numpy as np
import yaml

import tecplotdata.io as tecplot_io
from tecplotdata import TecplotData, TecplotZone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DELTA_X = 0.001
DELTA_R = 0.0005

N_R_BASELINE   = 41
RATIO_BASELINE = 0.2613 / 0.256

REF_DAT = "/your/path/post_files/linesHubAndTipAsIJK.dat"


# ---------------------------------------------------------------------------
# Helpers (unchanged from original)
# ---------------------------------------------------------------------------
def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def piecewise_x_grid(parts):
    xs = []
    for i, (x1, x2, num) in enumerate(parts):
        xs.append(np.linspace(x1, x2, num, endpoint=(i + 1 == len(parts))))
    return np.concatenate(xs)


def compute_r_limits(r_rotor_base, fin_height, clearance, step_heights):
    gap = fin_height + clearance
    cumsum = 0.0
    vals_rotor = [r_rotor_base]
    for h in step_heights:
        cumsum += h
        vals_rotor.append(r_rotor_base + cumsum)
    vals_stator = [v + gap for v in vals_rotor]
    return min(vals_rotor), max(vals_stator)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <sim_folder_path>")
        print(f"Example: python3 {sys.argv[0]} /your/path/local/sims/LC")
        sys.exit(1)

    sim_dir = os.path.abspath(sys.argv[1])
    if not os.path.isdir(sim_dir):
        print(f"[ERROR] Folder not found: {sim_dir}")
        sys.exit(1)

    sim_name = os.path.basename(sim_dir)

    print("=" * 60)
    print(f" Processing: {sim_name}  →  {sim_dir}")
    print("=" * 60)

    params_yml = os.path.join(sim_dir, "parameters_used.yml")
    geom_yml   = os.path.join(sim_dir, "geometry_used.yml")
    output_dat = os.path.join(sim_dir, "grid.dat")

    params = load_yaml(params_yml)
    geom   = load_yaml(geom_yml)

    extension_inlet  = float(params["mesh"]["extension_inlet"])
    extension_outlet = float(params["mesh"]["extension_outlet"])

    r_rotor_base   = float(geom["inner_height_inlet"])
    fin_height     = float(geom["fin_height"])
    clearance      = float(geom["clearence"])
    step_heights   = list(geom["step_height"])
    number_of_fins = int(geom["number_of_fins"])
    distance_fin   = float(geom["distance_fin"])
    X_START        = float(geom.get("x_offset", 0.0))

    X_END = (number_of_fins + 1) * distance_fin
    N_X2  = round(201 * X_END / 0.04)

    BASELINE_EXT = 0.024
    BASELINE_N   = 25

    N_X1 = round(BASELINE_N * extension_inlet  / BASELINE_EXT)
    N_X3 = round(BASELINE_N * extension_outlet / BASELINE_EXT)
    N_X  = N_X1 + N_X2 + N_X3

    X_MIN = X_START - extension_inlet
    X_MAX = X_END   + extension_outlet

    x_vals = piecewise_x_grid([
        (X_MIN - DELTA_X, X_START,         N_X1),
        (X_START,         X_END,           N_X2),
        (X_END,           X_MAX + DELTA_X, N_X3),
    ])

    r_min, r_max = compute_r_limits(r_rotor_base, fin_height, clearance, step_heights)

    r_stator_base = r_rotor_base + fin_height + clearance
    radius_ratio  = r_stator_base / r_rotor_base
    N_R = round(N_R_BASELINE * radius_ratio / RATIO_BASELINE)

    print(f"[{sim_name}] extension_inlet={extension_inlet*1000:.1f} mm  → N_X1={N_X1}, X_MIN={X_MIN}")
    print(f"[{sim_name}] extension_outlet={extension_outlet*1000:.1f} mm → N_X3={N_X3}, X_MAX={X_MAX}")
    print(f"[{sim_name}] number_of_fins={number_of_fins}, distance_fin={distance_fin} → X_END={X_END}, N_X2={N_X2}")
    print(f"[{sim_name}] r_min={r_min:.6f}, r_max={r_max:.6f}, N_R={N_R}, N_X={N_X}")

    tp_reference  = tecplot_io.read(REF_DAT)
    varIdxCX_ref  = tp_reference.get_var_index("CoordinateX")
    varIdxCR_ref  = tp_reference.get_var_index("CoordinateR")

    hub_reference = tp_reference.zones[0]
    tip_reference = tp_reference.zones[1]

    r_hub_x = np.interp(x_vals,
                        hub_reference.var[varIdxCX_ref].data,
                        hub_reference.var[varIdxCR_ref].data)
    r_tip_x = np.interp(x_vals,
                        tip_reference.var[varIdxCX_ref].data,
                        tip_reference.var[varIdxCR_ref].data)

    xi_vals = x_vals / X_END
    r_vals  = np.linspace(r_min - DELTA_R, r_max + DELTA_R, N_R)

    X,  R  = np.meshgrid(x_vals, r_vals)
    XI, _  = np.meshgrid(xi_vals, r_vals)
    ETA    = (R - r_hub_x[None, :]) / (r_tip_x - r_hub_x)[None, :]

    tp    = TecplotData(title=f"Grid file for labyrinth seal – {sim_name}")
    zone1 = TecplotZone(name="Zone A", zonetype="ORDERED", i=N_X, j=N_R, k=1)
    tp.add_zone(zone1)

    varIdxCX  = tp.add_variable("CoordinateX",   vartype="DOUBLE", varlocation="NODAL", empty=False)
    varIdxCR  = tp.add_variable("CoordinateR",   vartype="DOUBLE", varlocation="NODAL", empty=False)
    varIdxXi  = tp.add_variable("CoordinateXi",  vartype="DOUBLE", varlocation="NODAL", empty=False)
    varIdxEta = tp.add_variable("CoordinateEta", vartype="DOUBLE", varlocation="NODAL", empty=False)

    zone1.var[varIdxCX ].data = X.flatten()
    zone1.var[varIdxCR ].data = R.flatten()
    zone1.var[varIdxXi ].data = XI.flatten()
    zone1.var[varIdxEta].data = ETA.flatten()

    tecplot_io.write(tp, output_dat)
    print(f"[{sim_name}] Grid written → {output_dat}")


if __name__ == "__main__":
    main()
