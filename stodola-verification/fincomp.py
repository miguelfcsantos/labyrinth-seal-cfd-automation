import math
import re
import os
import itertools

# ============================================================
#  CONFIG
# ============================================================

MERGE_DONE_DIR = "/localdata1/corr_mi/OI/TEST/merge_done"
OUTPUT_FILE    = "/localdata1/corr_mi/VALUES/ratio_verification.txt"

SIM_TYPES  = ["FR", "FS", "FH"]
VARIANTS   = ["Sch", "Off"]
FIN_RANGE  = range(2, 7)   # 2, 3, 4, 5, 6
AVG_TYPES  = ["flux", "mass", "area"]

KAPPA  = 1.4
R_GAS  = 287.0

# ============================================================
#  READERS  (unchanged from original)
# ============================================================

def parse_d0_zones(filepath, zones):
    with open(filepath) as f:
        lines = f.readlines()

    SKIP_START = ('DATASETAUXDATA', 'AUXDATA', 'TITLE', 'DT',
                  'ZONETYPE', 'STRANDID', 'SOLUTIONTIME', 'VARLOCATION')

    results  = []
    cur_name = None
    cur_data = {}
    cur_key  = None

    for line in lines:
        s = line.strip()
        if not s:
            continue

        m = re.match(r'ZONE\s+T\s*=\s*"([^"]*)"', s, re.IGNORECASE)
        if m:
            if cur_name is not None:
                results.append((cur_name, cur_data))
            cur_name = m.group(1).lower()
            cur_data = {}
            cur_key  = None
            continue

        if any(s.upper().startswith(k) for k in SKIP_START):
            cur_key = None
            continue
        if re.match(r'VARIABLES\s*=', s, re.IGNORECASE):
            cur_key = None
            continue

        if s.startswith('###'):
            cur_key = s[3:].strip()
            continue

        if cur_key is not None:
            try:
                cur_data[cur_key] = float(s.split()[0])
            except (ValueError, IndexError):
                pass
            cur_key = None

    if cur_name is not None:
        results.append((cur_name, cur_data))

    lookup = {name: d for name, d in results}
    out = []
    for z in zones:
        key = z.lower()
        if key not in lookup:
            avail = [n for n, _ in results]
            raise KeyError(f"Zone '{z}' not found in {filepath}. Available: {avail}")
        out.append(lookup[key])
    return out


def get_mdot(sim_name, avg_type):
    """
    Return raw mdot (abs) from the outlet zone of the primitives file.
    Returns None if file is missing or parse fails.
    """
    base   = os.path.join(MERGE_DONE_DIR, sim_name)
    f_prim = os.path.join(base, "output", "output", "POST",
                          f"d0_primitives_{avg_type}.dat")
    if not os.path.isfile(f_prim):
        return None
    try:
        _, z_out = parse_d0_zones(f_prim, zones=["inlet", "outlet"])
        return abs(z_out['MassFlow'])
    except Exception as e:
        print(f"  [WARN] {sim_name} / {avg_type}: {e}")
        return None


# ============================================================
#  RATIO VERIFICATION
# ============================================================

def predicted_ratio(z1, z2):
    """Formula: mdot_z2 / mdot_z1 = sqrt(z1 / z2)"""
    return math.sqrt(z1 / z2)


DIVIDER  = "#" + "=" * 65
DIVIDER2 = "#" + "-" * 65


def verify_group(sim_type, variant, avg_type, lines_out):
    """
    For one (sim_type, variant, avg_type) triple, collect all available
    fin counts, then compare every consecutive pair AND every possible pair.
    Appends formatted lines to lines_out.
    """
    # Collect mdot for each available fin count
    data = {}   # fin_count -> mdot
    for z in FIN_RANGE:
        sim_name = f"{sim_type}{z}_{variant}_"
        mdot = get_mdot(sim_name, avg_type)
        if mdot is not None:
            data[z] = mdot
        else:
            print(f"  [SKIP] {sim_name} / avg={avg_type} — not available")

    if len(data) < 2:
        lines_out.append(f"#  Not enough data for {sim_type}_{variant} / {avg_type}\n")
        return

    fin_counts = sorted(data.keys())

    header = (f"\n{DIVIDER}\n"
              f"#  Type: {sim_type}   Variant: {variant}   Avg: {avg_type.upper()}\n"
              f"{DIVIDER}\n")
    lines_out.append(header)

    # Table header
    col = f"  {'z1':>3}  {'z2':>3}  {'mdot_z1':>12}  {'mdot_z2':>12}  "
    col += f"{'actual_ratio':>13}  {'pred_ratio':>11}  {'Δratio':>9}  {'error_%':>9}\n"
    lines_out.append(col)
    lines_out.append(f"  {'-'*95}\n")

    # All pairwise combinations (z1 < z2, so z2 has MORE fins → smaller mdot)
    for z1, z2 in itertools.combinations(fin_counts, 2):
        mdot1  = data[z1]
        mdot2  = data[z2]
        actual = mdot2 / mdot1          # should be < 1 since more fins = less leakage
        pred   = predicted_ratio(z1, z2)
        delta  = actual - pred
        err_pct = (delta / pred) * 100.0

        row = (f"  {z1:>3}  {z2:>3}  {mdot1:>12.6e}  {mdot2:>12.6e}  "
               f"{actual:>13.6f}  {pred:>11.6f}  {delta:>+9.6f}  {err_pct:>+9.3f}%\n")
        lines_out.append(row)

    lines_out.append("\n")


# ============================================================
#  MAIN
# ============================================================

all_lines = []
all_lines.append(f"{DIVIDER}\n")
all_lines.append(f"#  LABYRINTH SEAL MASS-FLOW RATIO VERIFICATION\n")
all_lines.append(f"#  Formula: mdot(z2) / mdot(z1) = sqrt(z1 / z2)\n")
all_lines.append(f"#  z1 < z2  =>  ratio < 1  (more fins, less leakage)\n")
all_lines.append(f"{DIVIDER}\n")

for sim_type in SIM_TYPES:
    all_lines.append(f"\n\n{'#'*67}\n")
    all_lines.append(f"##  SIM TYPE: {sim_type}\n")
    all_lines.append(f"{'#'*67}\n")

    for variant in VARIANTS:
        all_lines.append(f"\n{'#'*67}\n")
        all_lines.append(f"##  Variant: {variant}\n")
        all_lines.append(f"{'#'*67}\n")

        for avg_type in AVG_TYPES:
            verify_group(sim_type, variant, avg_type, all_lines)

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
with open(OUTPUT_FILE, "w") as f:
    f.writelines(all_lines)

# Also print to stdout
print("".join(all_lines))
print(f"\n→ Written to {OUTPUT_FILE}")
