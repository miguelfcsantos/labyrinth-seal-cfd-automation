#!/usr/bin/env python3
"""
-------------------
Reads d1_spanwise_primitives_flux.dat for every simulation found under
MERGED_BASE, computes normalised velocities C_ax and C_tan based on RADIUS, OMEGA, and writes
per-location .dat files that replicate the Tecplot-extracted format on each specific plot.

Zone discovery is fully dynamic: all XI_CUT zones found in the .dat file are processed.
Zone names follow the pattern IP_ISO_XI_CUT_<xi_value>, and the axial coordinate X [mm]
is derived as:
    X = round(xi * 400)

Inlet and outlet zones are intentionally ignored — only IP_ISO_XI_CUT_* zones are written.

Output tree (2 files per zone per simulation):
  PLOTS_BASE/LOCATIONS/<X>/ax_<X>/<conv|div>/<sim_name>.dat   (RelativeChannelHeight + C_ax)
  PLOTS_BASE/LOCATIONS/<X>/tan_<X>/<conv|div>/<sim_name>.dat  (RelativeChannelHeight + C_tan)

The conv/div folder is determined automatically by reading geometry_used.yml:
  step_height: [+x, +x, +x]  →  div  (positive values)
  step_height: [-x, -x, -x]  →  conv (negative values)

Usage:
    python3 saca2.py                                          # batch: all sims in MERGED_BASE
    python3 saca2.py /your/path/local/merge_done/LC           # single sim
"""

import os
import re
import sys
import subprocess

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MERGED_BASE    = "/your/path/local/merge_done"
PLOTS_BASE     = "/your/path/PLOTS"
INPUT_FILE     = "output/output/POST/d1_spanwise_primitives_flux.dat"
GEOMETRY_FILE  = "geometry_used.yml"
PARAMETERS_FILE = "parameters_used.yml"

PLOT_SCRIPT    = "/your/path/PLOTS/plot_velocity_profiles.py"

# Normalisation: C = V / (RADIUS * OMEGA)
RADIUS = 0.257                # [m]
OMEGA  = 628.32               # [rad/s]
NORM   = RADIUS * OMEGA

# Xi → X [mm] conversion: X = xi * 400
XI_TO_X_SCALE = 400.0

# Zone name pattern: IP_ISO_XI_CUT_<xi_value>
ZONE_PATTERN = re.compile(r'^IP_ISO_XI_CUT_([-+]?\d+(?:\.\d+)?)$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def xi_to_x_label(xi_str):
    xi = float(xi_str)
    return int(round(xi * XI_TO_X_SCALE))


def detect_case(sim_name, merged_base=None):
    if merged_base is None:
        merged_base = MERGED_BASE

    yml_path = os.path.join(merged_base, sim_name, GEOMETRY_FILE)
    if not os.path.isfile(yml_path):
        print(f"    [WARN] geometry file not found: {yml_path}")
        return None

    with open(yml_path, "r") as f:
        content = f.read()

    match = re.search(r'step_height\s*:\s*\[([^\]]+)\]', content)
    if not match:
        print(f"    [WARN] 'step_height' key not found in {yml_path}")
        return None

    raw_values = match.group(1)
    numbers = re.findall(r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', raw_values)
    if not numbers:
        print(f"    [WARN] no numeric values found in step_height in {yml_path}")
        return None

    floats = [float(n) for n in numbers]
    non_zero = [v for v in floats if v != 0.0]
    if not non_zero:
        print(f"    [WARN] all step_height values are zero in {yml_path}")
        return None

    case = "div" if non_zero[0] > 0 else "conv"
    print(f"    step_height: {floats}  →  case = {case}")
    return case


def parse_dat(filepath):
    with open(filepath, "r") as f:
        lines = f.readlines()

    variables = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("VARIABLES"):
            block = stripped
            j = i + 1
            while j < len(lines):
                peek = lines[j].strip()
                if peek.startswith("ZONE") or peek.startswith("DATASET") or peek == "":
                    break
                block += " " + peek
                j += 1
            variables = re.findall(r'"([^"]+)"', block)
            i = j
            continue
        if stripped.startswith("ZONE") or stripped.startswith("DATASET"):
            break
        i += 1

    if not variables:
        raise ValueError(f"Could not parse VARIABLES from {filepath}")

    zones = {}
    current_zone = None
    current_var_idx = None
    collecting = False
    buf = []

    def flush_variable():
        if current_zone is None or current_var_idx is None:
            return
        varname = variables[current_var_idx]
        zones[current_zone]["vars"][varname] = [float(x) for x in buf]

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("ZONE T"):
            flush_variable()
            buf = []
            collecting = False
            current_var_idx = None

            m = re.search(r'ZONE T\s*=\s*"([^"]+)"', stripped)
            current_zone = m.group(1) if m else stripped
            zones[current_zone] = {"header": {}, "auxdata": [], "vars": {}}

            for key in ("I", "J", "K"):
                km = re.search(rf'\b{key}\s*=\s*(\d+)', stripped)
                if km:
                    zones[current_zone]["header"][key] = int(km.group(1))

        elif current_zone and stripped.startswith("ZONETYPE"):
            for key in ("I", "J", "K"):
                km = re.search(rf'\b{key}\s*=\s*(\d+)', stripped)
                if km:
                    zones[current_zone]["header"][key] = int(km.group(1))
        elif current_zone and stripped.startswith("STRANDID"):
            zones[current_zone]["header"]["STRANDID"] = stripped.split("=")[1].strip()
        elif current_zone and stripped.startswith("SOLUTIONTIME"):
            zones[current_zone]["header"]["SOLUTIONTIME"] = stripped.split("=")[1].strip()
        elif current_zone and stripped.startswith("VARLOCATION"):
            zones[current_zone]["header"]["VARLOCATION"] = stripped
        elif current_zone and stripped.startswith("AUXDATA"):
            zones[current_zone]["auxdata"].append(stripped)

        elif current_zone and stripped.startswith("###"):
            flush_variable()
            buf = []
            varname_raw = stripped.lstrip("#").strip()
            if varname_raw in variables:
                current_var_idx = variables.index(varname_raw)
                collecting = True
            else:
                collecting = False
                current_var_idx = None

        elif collecting and stripped and not stripped.startswith("ZONE"):
            buf.extend(stripped.split())

        i += 1

    flush_variable()
    return zones


def discover_xi_zones(zones):
    found = []
    for zone_name in zones:
        m = ZONE_PATTERN.match(zone_name)
        if m:
            xi_str = m.group(1)
            x_label = xi_to_x_label(xi_str)
            found.append((zone_name, xi_str, x_label))
    found.sort(key=lambda t: float(t[1]))
    return found


def write_profile_dat(outpath, zone_name, zone_data, var_label, values, rch_values):
    os.makedirs(os.path.dirname(outpath), exist_ok=True)

    hdr = zone_data["header"]
    I   = hdr.get("I", len(rch_values))

    lines = []
    lines.append('TITLE     = "TRACE Mesh and Solution"')
    lines.append('VARIABLES = "RelativeChannelHeight"')
    lines.append(f'"{var_label}"')
    lines.append('DATASETAUXDATA versionTRACE="9.8.0"')
    lines.append(f'ZONE T="{zone_name}"')
    lines.append(f' STRANDID=0, SOLUTIONTIME=0')
    lines.append(f' I={I}, J=1, K=1, ZONETYPE=Ordered')
    lines.append(' DATAPACKING=BLOCK')
    lines.append(' VARLOCATION=([1-2]=CELLCENTERED)')

    for aux in zone_data.get("auxdata", []):
        lines.append(f' {aux}')

    lines.append(' DT=(DOUBLE DOUBLE )')

    def format_block(vals):
        out = []
        for idx in range(0, len(vals), 5):
            chunk = vals[idx:idx+5]
            out.append(" " + "".join(f"{v: .9E}" for v in chunk))
        return out

    lines.extend(format_block(rch_values))
    lines.extend(format_block(values))

    with open(outpath, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"    [OK] {outpath}")


def process_zone(zone_name, zone_data, x_label, case, sim_name):
    """Extract C_ax / C_tan from a zone and write output files."""
    var_dict = zone_data["vars"]

    missing = [v for v in ("RelativeChannelHeight", "VelocityX", "VelocityTheta")
               if v not in var_dict]
    if missing:
        print(f"    [WARN] missing vars {missing} in zone '{zone_name}', skipping X={x_label}")
        return

    rch   = var_dict["RelativeChannelHeight"]
    c_ax  = [v / NORM for v in var_dict["VelocityX"]]
    c_tan = [v / NORM for v in var_dict["VelocityTheta"]]

    x = str(x_label)

    out_ax  = os.path.join(PLOTS_BASE, "LOCATIONS", x, f"ax_{x}",  case, f"{sim_name}.dat")
    out_tan = os.path.join(PLOTS_BASE, "LOCATIONS", x, f"tan_{x}", case, f"{sim_name}.dat")

    print(f"    [DEBUG] zone='{zone_name}'  x_label={x_label}  case={case}")
    print(f"    [DEBUG] out_ax  = {out_ax}")
    print(f"    [DEBUG] out_tan = {out_tan}")

    write_profile_dat(out_ax,  zone_name, zone_data, "C_ax",  c_ax,  rch)
    write_profile_dat(out_tan, zone_name, zone_data, "C_tan", c_tan, rch)


def process_simulation(sim_name, merged_base=None):
    if merged_base is None:
        merged_base = MERGED_BASE

    dat_path = os.path.join(merged_base, sim_name, INPUT_FILE)
    if not os.path.isfile(dat_path):
        print(f"  [SKIP] {sim_name}: .dat file not found → {dat_path}")
        return

    print(f"\n  Processing: {sim_name}")

    case = detect_case(sim_name, merged_base)
    if case is None:
        print(f"  [SKIP] {sim_name}: could not determine conv/div, skipping")
        return

    print(f"    Reading: {dat_path}")

    try:
        zones = parse_dat(dat_path)
    except Exception as e:
        print(f"  [ERROR] parsing failed: {e}")
        return

    # Only process IP_ISO_XI_CUT_* zones — inlet and outlet zones are ignored.
    xi_zones = discover_xi_zones(zones)

    if not xi_zones:
        print(f"  [WARN] no IP_ISO_XI_CUT_* zones found in {dat_path}")
        return

    print(f"    Found {len(xi_zones)} XI_CUT zone(s): "
          + ", ".join(f"X={x}" for _, _, x in xi_zones))

    for zone_name, xi_str, x_label in xi_zones:
        process_zone(zone_name, zones[zone_name], x_label, case, sim_name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not os.path.isdir(MERGED_BASE):
        print(f"[ERROR] MERGED_BASE not found:\n  {MERGED_BASE}")
        sys.exit(1)

    sim_names = sorted([
        d for d in os.listdir(MERGED_BASE)
        if os.path.isdir(os.path.join(MERGED_BASE, d))
    ])

    if not sim_names:
        print(f"[ERROR] No simulation folders found in:\n  {MERGED_BASE}")
        sys.exit(1)

    print(f"Found {len(sim_names)} simulation(s): {', '.join(sim_names)}\n")
    print(f"Normalisation: RADIUS={RADIUS} m, OMEGA={OMEGA} rad/s → NORM={NORM:.4f} m/s")
    print(f"Xi → X scale: {XI_TO_X_SCALE} (X = Xi × {XI_TO_X_SCALE:.0f} mm)")
    print(f"Output base: {PLOTS_BASE}\n")
    print(f"Note: inlet and outlet zones are ignored; only IP_ISO_XI_CUT_* zones are written.\n")

    for sim in sim_names:
        process_simulation(sim)

    print(f"\nDone.\n")

    print(f"Launching plot script: {PLOT_SCRIPT}\n")
    result = subprocess.run(
        [sys.executable, PLOT_SCRIPT],
        check=False
    )
    if result.returncode != 0:
        print(f"[WARN] Plot script exited with code {result.returncode}")
    else:
        print("[OK] Plot script completed successfully.")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        sim_path    = os.path.abspath(sys.argv[1])
        merged_base = os.path.dirname(sim_path)
        sim_name    = os.path.basename(sim_path)
        print(f"\nSingle-sim mode: {sim_name}  (base: {merged_base})")
        process_simulation(sim_name, merged_base)
        print(f"\nLaunching plot script: {PLOT_SCRIPT}\n")
        result = subprocess.run([sys.executable, PLOT_SCRIPT], check=False)
        if result.returncode != 0:
            print(f"[WARN] Plot script exited with code {result.returncode}")
        else:
            print("[OK] Plot script completed successfully.")
    else:
        main()
