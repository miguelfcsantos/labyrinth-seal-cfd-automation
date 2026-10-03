#!/bin/bash
#
# paramstudy.sh
# -----------------------------------------------------------------------
# Parametric sweep driver for the staggered-labyrinth-seal CFD cases.
#
# RUN FLOW
# --------
#   Everything is read from the plan file, always -- there is no more
#   interactive collection step. The plan file supplies:
#       outer: variable / fmt / values   (optional)
#       inner: a list of hardcoded OFAT sweeps (variation_pct+points,
#              start/end/points, or outer-band entries) -- always present,
#              always read from the file, never asked about interactively.
#       K_in:  a SINGLE constant value (not a list). It is used to compute
#              the BC target velocity for every case. It is ignored
#              entirely when the outer variable is swirl.
#
#   If `outer:` is absent, the inner sweeps just run flat, keyed by K_in.
#   If `outer:` is present, its values are held fixed (one at a time)
#   while every inner sweep runs underneath each one.
#
# SWIRL AS OUTER VARIABLE
# -----------------------
#   If the outer variable is swirl (velocity_angle_alpha), K_in is IGNORED
#   and the BC controller is NOT used: the swirl values you give are written
#   straight into velocity_angle_alpha and the cases are submitted directly
#   as real (non-_BC) cases.
#
# BASELINE HANDLING
# -----------------
#   geometry.yml / parameter.yml are an immutable baseline. Every case gets
#   its own geometry_used.yml / parameters_used.yml copy in its output
#   folder; only those copies are edited and handed to PyMesh.x.
#
# FOLDER STRUCTURE
# -----------------
#   - outer: set (not swirl): every case for a given outer value goes in
#     <OUTBASE>/<outer_label><outer_token>/... e.g. PR150/PR150_CLR_213_BC.
#     K_in never appears in the folder or case name in this case -- it's
#     just used internally to compute the BC target velocity, so PR150 at
#     K_in=0.3 and PR150 at K_in=0.5 land in exactly the same place.
#   - outer: set to swirl: same idea, folder = <SWA_label><token>, but no
#     _BC suffix and no K anywhere.
#   - outer: absent: folder = K<token> (K_in is the only thing that varies
#     between runs of the script), e.g. K7/K7_PR_150_BC.
#   Remote layout under REMOTE_SIMS_BASE mirrors the same subfolder.
#
# CASE NAMES
# ----------
#   <folder-prefix><LABEL>_<value>[_BC]
#     - folder-prefix is the outer token (e.g. "PR150_") or, if there's no
#       outer variable, the K token (e.g. "K7_").
#     - value tokens are always the plain "compact" style: strip '.' and
#       leading zeros, e.g. 1.25 -> "125", 2.0 -> "2", 0.000233 -> "233".
#       Names always contain the ACTUAL value, never a percentage.
#     - The outer variable ALWAYS uses the same VAR_FMT precision the
#       registry uses for that variable as an inner sweep -- the plan
#       file's `outer: fmt:` field is parsed but ignored (a note is
#       printed if it's set). This is deliberate: letting the plan file
#       override precision on the outer variable previously caused small
#       values (e.g. clearance ~2e-5) to round away to "0.0000" and get
#       written into the case files as zero.
#
# BOUNDARY-CONTROLLER (BC) WORKFLOW
# ---------------------------------
#   Registry-driven cases are submitted as "_BC" cases: the control file
#   gets a SetBoundaryConditionController block that scales the inlet
#   VelocityAngleThetaAbs until the mass-averaged inlet VelocityTheta hits
#   K_in * omega * R. cara_bc_finalize.sh later extracts the converged angle
#   and submits the real case.
#   The plan file's `control file:` entry is documentation only (not parsed);
#   the heredoc in inject_bc_control_block() is the single source of truth.
#
# OUTER BANDS (inner: entries, percent-based variables only)
# ----------------------------------------------------------
#   An inner: entry can add bands outside +/-variation_pct, either symmetric
#   (outer_variation_pct + outer_points) or asymmetric
#   (outer_variation_pct_neg/_pos + outer_points_neg/_pos). `bands:` selects
#   inner / outer / both. The shared +/-variation_pct boundary point is never
#   emitted twice.
#
# Parsing of the plan file is hand-rolled (no PyYAML) and only understands
# the shape the plan file uses. It is not a general YAML parser.
# -----------------------------------------------------------------------

set -uo pipefail

WORKDIR="/localdata1/testcases/staggered-labyrinth-seal_new/examples/Denecke_cavity"
BASECASE="/localdata1/testcases/staggered-labyrinth-seal_new/lab_altered_py"

# The sweep-plan YAML (K_in, outer:, inner:).
k_plan_FILE="/home/corr_mi/Projects/MM/SWEEP_plan.yml"

# BASELINE files (never written to by the sweep logic).
GEOMETRY_FILE="$WORKDIR/geometry.yml"
PARAMETER_FILE="$WORKDIR/parameter.yml"

LOCAL_SIMS_BASE="/home/corr_mi/Projects/MM/testGEO"
REMOTE_SIMS_BASE="/scratch/ws25/corr_mi-mig_param_fac/Param_sims"
TRACESTART_SCRIPT="/scratch/ws25/corr_mi-mig_param_fac/tracestart_OG.sh"

# Total axial fin span (m). Only used by the number_of_fins special sweep.
TOTAL_FIN_SPAN=0.06

# -------------------------------------------------------------------
# Boundary-controller constants
# -------------------------------------------------------------------
BC_RELAX_ARGS="0.1 0.0500 2000"
BC_IT_START=5000
BC_IT_STEP=400
BC_RES="1e-14"

# Precision used ONLY for the K folder/prefix token when there's no outer
# variable (e.g. K_in=0.7 -> "K7"). NOT used for anything written to yaml.
K_IN_FMT="%.1f"

# =========================================================================
# Variable registry
#   mode = direct      -> +/- percent sweep around baseline, written straight
#                         into the yaml key
#   mode = derived_pr  -> value is a pressure ratio; written as
#                         total_pressure = PR * outlet pressure
#   mode = interval    -> direct start/end interval (no baseline percent)
#   mode = magnitude   -> step_height only: sweeps the (uniform) magnitude of
#                         the baseline list, keeping each entry's sign
# =========================================================================
declare -A VAR_KEY=(
    [clearance]="clearence"
    [x_offset]="x_offset"
    [rpm]="rpm"
    [pressure_ratio]="total_pressure"
    [swirl]="velocity_angle_alpha"
    [fin_thickness]="fin_tip_width"
    [fin_angle]="fin_wall_angle"
    [step_height]="step_height"
    [fin_height]="fin_height"
    [radius_fillet]="radius_fillet"
    [distance_fin]="distance_fin"
)
# Naming/writing precision. pressure_ratio and fin_angle intentionally give
# clean short tokens (PR125, FAN2); fin_angle's formatted value is also what
# gets written to fin_wall_angle (plain whole degrees).
declare -A VAR_FMT=(
    [clearance]="%.6f"
    [x_offset]="%.3f"
    [rpm]="%.1f"
    [pressure_ratio]="%.2f"
    [swirl]="%.1f"
    [fin_thickness]="%.6f"
    [fin_angle]="%.0f"
    [step_height]="%.6f"
    [fin_height]="%.7f"
    [radius_fillet]="%.6f"
    [distance_fin]="%.6f"
)
declare -A VAR_LABEL=(
    [clearance]="CLR"
    [x_offset]="XOF"
    [rpm]="RPM"
    [pressure_ratio]="PR"
    [swirl]="K"
    [fin_thickness]="FTH"
    [fin_angle]="FAN"
    [step_height]="SH"
    [fin_height]="FH"
    [radius_fillet]="RF"
    [distance_fin]="DF"
)
declare -A VAR_MODE=(
    [clearance]="direct"
    [x_offset]="direct"
    [rpm]="direct"
    [pressure_ratio]="derived_pr"
    [swirl]="interval"
    [fin_thickness]="direct"
    [fin_angle]="interval"
    [step_height]="magnitude"
    [fin_height]="direct"
    [radius_fillet]="direct"
    # distance_fin: plain +/- percent sweep, number_of_fins untouched (total
    # span changes). Independent of the number_of_fins special sweep.
    [distance_fin]="direct"
)

# =========================================================================
# Helpers
# =========================================================================

# "0.000233" -> "233", "3450.0" -> "34500", "-0.001200" -> "-1200"
# This is the ONLY naming/value-token style used anywhere in the script now.
compact_number() {
    local formatted=$1
    python3 -c "
s = '$formatted'
sign = ''
if s.startswith('-'):
    sign = '-'
    s = s[1:]
s = s.replace('.', '')
s = s.lstrip('0') or '0'
print(sign + s)
"
}

# Read the current numeric value of a yaml key (ignores inline comments).
get_current_value() {
    local file=$1 key=$2
    python3 -c "
key = '$key'
with open('$file') as f:
    for line in f:
        s = line.strip()
        if s.startswith(key + ':'):
            val = s.split(':', 1)[1].split('#')[0].strip()
            print(val)
            break
"
}

# Which BASELINE file (geometry.yml / parameter.yml) a key lives in.
find_var_file() {
    local key=$1
    local in_geom in_param

    in_geom=$(grep -E "^[[:space:]]*${key}:" "$GEOMETRY_FILE" || true)
    in_param=$(grep -E "^[[:space:]]*${key}:" "$PARAMETER_FILE" || true)

    if [[ -n "$in_geom" && -n "$in_param" ]]; then
        echo "ERROR: key '$key' found in both geometry.yml and parameter.yml -- ambiguous." >&2
        exit 1
    elif [[ -n "$in_geom" ]]; then
        echo "$GEOMETRY_FILE"
    elif [[ -n "$in_param" ]]; then
        echo "$PARAMETER_FILE"
    else
        echo "ERROR: key '$key' not found in geometry.yml or parameter.yml." >&2
        exit 1
    fi
}

# Write a value into a yaml key, preserving indentation and inline comment.
# Always called on a PER-CASE copy, never on the baseline.
update_key_in_file() {
    local file=$1 key=$2 value=$3
    python3 - "$file" "$key" "$value" <<'PYEOF'
import sys
file_path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]

with open(file_path) as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith(key + ":"):
        indent = line[:len(line) - len(line.lstrip())]
        comment = ""
        if "#" in line:
            c = line.split("#", 1)[1].rstrip("\n")
            if c.strip():
                comment = "  #" + c
        new_lines.append(f"{indent}{key}: {value}{comment}\n")
    else:
        new_lines.append(line)

with open(file_path, "w") as f:
    f.writelines(new_lines)
PYEOF
}

# center*(1-variation) ... center*(1+variation), `points` values.
generate_range_pct() {
    local center=$1 variation=$2 points=$3
    python3 -c "
center = float('$center')
variation = float('$variation') / 100.0
points = int('$points')
if points < 1:
    raise SystemExit('points must be >= 1')
if points == 1:
    print(center)
else:
    start = center * (1 - variation)
    end = center * (1 + variation)
    step = (end - start) / (points - 1)
    for i in range(points):
        print(start + i * step)
"
}

# start ... end, `points` evenly spaced values.
generate_range_interval() {
    local start=$1 end=$2 points=$3
    python3 -c "
start = float('$start')
end = float('$end')
points = int('$points')
if points < 1:
    raise SystemExit('points must be >= 1')
if points == 1:
    print(start)
else:
    step = (end - start) / (points - 1)
    for i in range(points):
        print(start + i * step)
"
}

# Two-band sweep OUTSIDE +/-inner_pct% (see header). The shared boundary
# point at +/-inner_pct% is never emitted; the far edges are included.
generate_range_outer_band() {
    local center=$1 inner_pct=$2 outer_pct_neg=$3 outer_points_neg=$4 outer_pct_pos=$5 outer_points_pos=$6
    python3 -c "
center = float('$center')
inner = float('$inner_pct') / 100.0
outer_neg = float('$outer_pct_neg') / 100.0
outer_pos = float('$outer_pct_pos') / 100.0
n_neg = int('$outer_points_neg')
n_pos = int('$outer_points_pos')

neg_far  = center * (1 - (inner + outer_neg))
neg_near = center * (1 - inner)
pos_near = center * (1 + inner)
pos_far  = center * (1 + (inner + outer_pos))

def band_excluding_boundary(a, b, n, boundary_at):
    # Build as if there were n+1 evenly spaced points a..b, then drop the
    # end that is the shared boundary.
    if n < 1:
        return []
    step = (b - a) / n
    pts = [a + i * step for i in range(n + 1)]
    return pts[:-1] if boundary_at == 'last' else pts[1:]

for v in band_excluding_boundary(neg_far, neg_near, n_neg, boundary_at='last'):
    print(v)
for v in band_excluding_boundary(pos_near, pos_far, n_pos, boundary_at='first'):
    print(v)
"
}

# Uniform magnitude of the baseline step_height list.
get_step_height_magnitude() {
    local file=$1
    python3 -c "
import ast
with open('$file') as f:
    for line in f:
        s = line.strip()
        if s.startswith('step_height:'):
            list_str = s.split(':', 1)[1].split('#')[0].strip()
            vals = ast.literal_eval(list_str)
            mags = [abs(v) for v in vals if v != 0]
            print(mags[0] if mags else 0.0)
            break
"
}

# Rewrite step_height in a per-case file: same signs, new magnitude.
apply_step_height_magnitude() {
    local file=$1 new_magnitude=$2
    python3 - "$file" "$new_magnitude" <<'PYEOF'
import sys, ast

file_path, new_magnitude = sys.argv[1], float(sys.argv[2])

with open(file_path) as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith("step_height:"):
        indent = line[:len(line) - len(line.lstrip())]
        comment = ""
        if "#" in line:
            c = line.split("#", 1)[1].rstrip("\n")
            if c.strip():
                comment = "  #" + c
        list_str = stripped.split(":", 1)[1].split("#")[0].strip()
        orig_values = ast.literal_eval(list_str)

        new_values = []
        for v in orig_values:
            if v > 0:
                new_values.append(new_magnitude)
            elif v < 0:
                new_values.append(-new_magnitude)
            else:
                new_values.append(0.0)

        formatted = "[" + ", ".join(f"{v:.6f}" for v in new_values) + "]"
        new_lines.append(f"{indent}step_height: {formatted}{comment}\n")
    else:
        new_lines.append(line)

with open(file_path, "w") as f:
    f.writelines(new_lines)
PYEOF
}

# Resize step_height to (new_n_fins - 1) entries: truncate, or pad by
# repeating the last entry.
resize_step_height() {
    local file=$1 new_n_fins=$2
    python3 - "$file" "$new_n_fins" <<'PYEOF'
import sys, ast

file_path, new_n_fins = sys.argv[1], int(sys.argv[2])
new_n_steps = new_n_fins - 1

with open(file_path) as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith("step_height:"):
        indent = line[:len(line) - len(line.lstrip())]
        comment = ""
        if "#" in line:
            c = line.split("#", 1)[1].rstrip("\n")
            if c.strip():
                comment = "  #" + c
        list_str = stripped.split(":", 1)[1].split("#")[0].strip()
        orig_values = ast.literal_eval(list_str)

        if new_n_steps <= len(orig_values):
            new_values = orig_values[:new_n_steps]
        else:
            pad = [orig_values[-1]] * (new_n_steps - len(orig_values))
            new_values = orig_values + pad

        formatted = "[" + ", ".join(f"{v:.6f}" for v in new_values) + "]"
        new_lines.append(f"{indent}step_height: {formatted}{comment}\n")
    else:
        new_lines.append(line)

with open(file_path, "w") as f:
    f.writelines(new_lines)
PYEOF
}

# Set number_of_fins and step_height list together (step_height_profile sweep).
update_step_geometry() {
    local file=$1 n_fins=$2 step_heights=$3
    python3 - "$file" "$n_fins" "$step_heights" <<'PYEOF'
import sys
file_path, n_fins, step_heights = sys.argv[1], sys.argv[2], sys.argv[3]
with open(file_path) as f:
    lines = f.readlines()
new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith("number_of_fins:"):
        new_lines.append(f"number_of_fins: {n_fins}\n")
    elif stripped.startswith("step_height:"):
        comment = ""
        if "#" in line:
            c = line.split("#", 1)[1].rstrip("\n")
            if c.strip():
                comment = "  #" + c
        new_lines.append(f"step_height: {step_heights}{comment}\n")
    else:
        new_lines.append(line)
with open(file_path, "w") as f:
    f.writelines(new_lines)
PYEOF
}

# make_step_list <value> <count> [<value> <count> ...] -> "[v, v, w, w, w]"
make_step_list() {
    python3 -c "
args = '$*'.split()
vals = []
i = 0
while i < len(args) - 1:
    v = float(args[i]); n = int(args[i+1])
    vals.extend([v] * n)
    i += 2
print('[' + ', '.join(str(v) for v in vals) + ']')
"
}

# Create outdir and seed it with a fresh copy of the baseline files.
prepare_case_workfiles() {
    local outdir=$1
    mkdir -p "$outdir"
    cp "$GEOMETRY_FILE" "$outdir/geometry_used.yml"
    cp "$PARAMETER_FILE" "$outdir/parameters_used.yml"
}

# -------------------------------------------------------------------
# Boundary-controller helpers
# -------------------------------------------------------------------

# K_in from the plan file. Must be a SINGLE constant value now (a scalar,
# or a one-element list) -- not a list to loop over. Errors out otherwise.
get_k_in() {
    python3 -c "
import ast
with open('$k_plan_FILE') as f:
    for line in f:
        s = line.strip()
        if s.startswith('K_in:'):
            raw = s.split(':', 1)[1].split('#')[0].strip()
            if raw.startswith('['):
                vals = ast.literal_eval(raw)
            else:
                vals = [float(raw)]
            if len(vals) != 1:
                raise SystemExit(
                    f\"ERROR: K_in must be a single constant value now \"
                    f\"(got {len(vals)} values in $k_plan_FILE).\"
                )
            print(float(vals[0]))
            break
    else:
        raise SystemExit(\"ERROR: 'K_in' not found in $k_plan_FILE\")
"
}

# Parse the top-level `inner:` list of the plan file. Emits one fixed-width,
# 8-field tab-separated line per entry:
#   <var>\tpct\t<variation_pct>\t<points>\t\t\t\t                          (bands: inner)
#   <var>\tpct_outer\t<pct>\t<o_pct_neg>\t<o_pts_neg>\t<o_pct_pos>\t<o_pts_pos>\t   (bands: outer)
#   <var>\tpct_both\t<pct>\t<points>\t<o_pct_neg>\t<o_pts_neg>\t<o_pct_pos>\t<o_pts_pos>  (bands: both)
#   <var>\tinterval\t<start>\t<end>\t<points>\t\t\t                        (interval)
# Entries with missing fields are skipped with a warning on stderr.
parse_inner_entries() {
    python3 - "$k_plan_FILE" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path) as f:
    lines = f.readlines()

entries = []
current = None
in_block = False
for raw in lines:
    line = raw.rstrip("\n")
    if not in_block:
        if line.strip() == "inner:" and not line.startswith((" ", "\t", "#")):
            in_block = True
        continue
    if line.strip() == "":
        continue
    if not line.startswith((" ", "\t")):
        break  # dedented back to a top-level key -- end of the inner: block
    s = line.strip()
    if s.startswith("#"):
        continue
    if s.startswith("- "):
        if current is not None:
            entries.append(current)
        current = {}
        s = s[2:].strip()
    if ":" in s and current is not None:
        k, v = s.split(":", 1)
        current[k.strip()] = v.split("#")[0].strip()
if current is not None:
    entries.append(current)


def emit(var, mode, p1="", p2="", p3="", p4="", p5="", p6=""):
    print(f"{var}\t{mode}\t{p1}\t{p2}\t{p3}\t{p4}\t{p5}\t{p6}")


for e in entries:
    var = e.get("variable", "")
    if not var:
        continue

    if "start" in e and "end" in e and "points" in e:
        bands = e.get("bands", "").strip().lower()
        if bands in ("outer", "both"):
            sys.stderr.write(
                f"WARNING: inner entry '{var}' is interval-mode (start/end/points) "
                f"and can't use bands: {bands} -- outer bands only apply to "
                f"percent-based variables. Running its plain interval sweep instead.\n"
            )
        emit(var, "interval", e["start"], e["end"], e["points"])
        continue

    has_inner_pts = "variation_pct" in e and "points" in e

    asym_fields = ["outer_variation_pct_neg", "outer_points_neg",
                   "outer_variation_pct_pos", "outer_points_pos"]
    asym_count = sum(1 for f in asym_fields if f in e)
    sym_present = "outer_variation_pct" in e and "outer_points" in e
    sym_partial = ("outer_variation_pct" in e) != ("outer_points" in e)

    outer_pct_neg = outer_pts_neg = outer_pct_pos = outer_pts_pos = None
    has_outer = False

    if asym_count == 4:
        outer_pct_neg = e["outer_variation_pct_neg"]
        outer_pts_neg = e["outer_points_neg"]
        outer_pct_pos = e["outer_variation_pct_pos"]
        outer_pts_pos = e["outer_points_pos"]
        has_outer = True
    elif asym_count > 0:
        sys.stderr.write(
            f"WARNING: inner entry '{var}' sets only {asym_count}/4 of "
            f"outer_variation_pct_neg/outer_points_neg/outer_variation_pct_pos/"
            f"outer_points_pos -- need all four for an asymmetric band. "
            f"Outer band ignored for this entry.\n"
        )
    elif sym_present:
        total_pts = int(e["outer_points"])
        neg_n = total_pts // 2
        pos_n = total_pts - neg_n
        outer_pct_neg = e["outer_variation_pct"]
        outer_pts_neg = str(neg_n)
        outer_pct_pos = e["outer_variation_pct"]
        outer_pts_pos = str(pos_n)
        has_outer = True
    elif sym_partial:
        sys.stderr.write(
            f"WARNING: inner entry '{var}' has only one of "
            f"outer_variation_pct/outer_points -- ignoring the outer band.\n"
        )

    bands = e.get("bands", "").strip().lower()
    if not bands:
        bands = "outer" if has_outer else "inner"

    if bands == "inner":
        if not has_inner_pts:
            sys.stderr.write(
                f"WARNING: inner entry '{var}' (bands: inner) is missing "
                f"variation_pct/points -- skipped.\n"
            )
            continue
        emit(var, "pct", e["variation_pct"], e["points"])
    elif bands == "outer":
        if "variation_pct" not in e or not has_outer:
            sys.stderr.write(
                f"WARNING: inner entry '{var}' (bands: outer) needs "
                f"variation_pct (anchor) plus either outer_variation_pct+"
                f"outer_points, or all four outer_..._neg/outer_..._pos "
                f"fields -- skipped.\n"
            )
            continue
        emit(var, "pct_outer", e["variation_pct"],
             outer_pct_neg, outer_pts_neg, outer_pct_pos, outer_pts_pos)
    elif bands == "both":
        if not has_inner_pts or not has_outer:
            sys.stderr.write(
                f"WARNING: inner entry '{var}' (bands: both) needs "
                f"variation_pct + points, plus either outer_variation_pct+"
                f"outer_points or all four outer_..._neg/outer_..._pos "
                f"fields -- skipped.\n"
            )
            continue
        emit(var, "pct_both", e["variation_pct"], e["points"],
             outer_pct_neg, outer_pts_neg, outer_pct_pos, outer_pts_pos)
    else:
        sys.stderr.write(
            f"WARNING: inner entry '{var}' has unknown bands: '{bands}' "
            f"(expected inner/outer/both) -- skipped.\n"
        )
PYEOF
}

# Parse the top-level `outer:` block of the plan file (only if live, i.e.
# uncommented). Emits VARIABLE / FMT / VALUE lines, or nothing.
parse_outer_config() {
    python3 - "$k_plan_FILE" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path) as f:
    lines = f.readlines()

found = False
in_block = False
data = {}
for raw in lines:
    line = raw.rstrip("\n")
    if not in_block:
        if line.strip() == "outer:" and not line.startswith((" ", "\t", "#")):
            in_block = True
            found = True
        continue
    if line.strip() == "":
        continue
    if not line.startswith((" ", "\t")):
        break
    s = line.strip()
    if ":" not in s:
        continue
    k, v = s.split(":", 1)
    data[k.strip()] = v.split("#")[0].strip()

if not found:
    sys.exit(0)

variable = data.get("variable", "")
fmt = data.get("fmt", "").strip().strip('"').strip("'")
values_str = data.get("values", "").strip("[]")
values = [v.strip() for v in values_str.split(",") if v.strip()]

if not variable or not values:
    sys.stderr.write(
        "WARNING: 'outer:' block present but missing 'variable' or "
        "'values' -- treated as absent (flat inner-only sweep)\n"
    )
    sys.exit(0)

print(f"VARIABLE\t{variable}")
print(f"FMT\t{fmt}")
for v in values:
    print(f"VALUE\t{v}")
PYEOF
}

# Target inlet VelocityTheta for ONE case: K_in * omega * R, with
# R = inner_height_inlet + fin_height + clearence (from the case's own files).
compute_target_velocity() {
    local geom_file=$1 param_file=$2 k_in=$3
    python3 -c "
import math

def read_val(path, key):
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith(key + ':'):
                return float(s.split(':', 1)[1].split('#')[0].strip())
    raise SystemExit(f\"ERROR: key '{key}' not found in {path}\")

rpm    = read_val('$param_file', 'rpm')
h_in   = read_val('$geom_file', 'inner_height_inlet')
fin_h  = read_val('$geom_file', 'fin_height')
clear  = read_val('$geom_file', 'clearence')

R = h_in + fin_h + clear
omega = rpm * 2.0 * math.pi / 60.0
target_v = float('$k_in') * omega * R
print(f'{target_v:.6f}')
"
}

# Append the velocity boundary-controller block to a TRACE control file.
inject_bc_control_block() {
    local control_file=$1 target_v=$2
    cat >> "$control_file" << EOF

all immediate SetBoundaryControlSet EvaluationPanel_v inlet VelocityTheta Mass
all immediate SetBoundaryControlSet ControPanel_v inlet VelocityAngleThetaAbs Mass

all immediate SetBoundaryConditionController -op Scale ${target_v} ${BC_RELAX_ARGS} -cPanel ControPanel_v -ePanel EvaluationPanel_v -it ${BC_IT_START} ${BC_IT_STEP} -o ../output/residual/bcControl_v.dat -res ${BC_RES}
EOF
}

# Mesh + prep + gmc pipeline for one case. If target_v is non-empty this is a
# BC case and the controller block is appended to TRACE_control.input.
run_case() {
    local case_name=$1
    local outdir=$2
    local param_file=$3
    local geom_file=$4
    local target_v=${5:-}

    echo "=============================="
    echo "Running case: $case_name"
    echo "=============================="

    # Loaded here so this also works when cara_bc_finalize.sh sources this
    # file in library-only mode.
    module load trace_dependencies/gcc-11.4.0-trace-9.7.5-1
    module load gmc/9.6.13
    module load tecplot
    module load trace_suite/9.8.0-double

    module load pymesh/2.0.1-numpy
    cd "$BASECASE" || { echo "Cannot enter $BASECASE"; return 1; }

    PyMesh.x main.py -p "$param_file" -g "$geom_file" -c "$outdir"
    if [[ ! -d "$outdir/input" ]]; then
        echo "ERROR: PyMesh.x did not produce $outdir/input -- skipping case $case_name"
        return 1
    fi

    module unload pymesh/2.0.1-numpy
    module load trace_suite/9.8.0-double

    if [[ -n "$target_v" ]]; then
        local control_file="$outdir/input/TRACE_control.input"
        if [[ ! -f "$control_file" ]]; then
            echo "ERROR: expected control file $control_file not found -- skipping case $case_name"
            return 1
        fi
        inject_bc_control_block "$control_file" "$target_v"
        echo "  Injected BC controller block, target velocity = $target_v"
    fi

    cd "$outdir/input" || { echo "Missing input folder for $case_name"; return 1; }

    prep.py -clb -cgns TRACE.cgns -np 128 -sb TRACE_split.cgns splitScript.jou mergeScript.jou
    gmcPlay splitScript.jou
    gmcPlay /home/corr_mi/Projects/MM/conv_gmc.jou
    gmcPlay /home/corr_mi/Projects/MM/models/OmegaSST_Off_Bardina.jou
    cd "$WORKDIR"
    return 0
}

# scp ONE case to its remote subfolder and submit it. On success the local
# copy is deleted; on scp failure it is left in place.
ship_and_submit() {
    local case_name=$1
    local outdir=$2
    local folder=${3:-}
    local remote_dir="${REMOTE_SIMS_BASE}"
    [[ -n "$folder" ]] && remote_dir="${REMOTE_SIMS_BASE}/${folder}"

    ssh cara.dlr.de "mkdir -p ${remote_dir}"

    if ! scp -r "$outdir" "cara.dlr.de:${remote_dir}/"; then
        echo "ERROR: scp failed for $case_name -- leaving local copy at $outdir, not submitting."
        return 1
    fi

    ssh cara.dlr.de "sbatch --chdir=${remote_dir}/${case_name}/input $TRACESTART_SCRIPT"
    echo "Submitted: ${remote_dir}/${case_name}"

    rm -rf "$outdir"
    echo "Removed local copy: $outdir"
}

# run_case + ship_and_submit + bookkeeping. Returns 0 only if the case was
# built AND submitted. folder defaults to the global CURRENT_FOLDER.
run_and_ship() {
    local case_name=$1 outdir=$2 target_v=${3:-} folder=${4:-$CURRENT_FOLDER}
    if run_case "$case_name" "$outdir" "$outdir/parameters_used.yml" "$outdir/geometry_used.yml" "$target_v"; then
        if ship_and_submit "$case_name" "$outdir" "$folder"; then
            CASE_NAMES+=("$case_name")
            return 0
        fi
    fi
    FAILED_CASES+=("$case_name")
    return 1
}

# -------------------------------------------------------------------
# Registry-driven scalar sweep.
#
# range_mode:
#   pct        p1=variation_pct  p2=points
#   interval   p1=start p2=end p3=points          (interval VAR_MODE)
#   list       p1=comma-separated explicit values (any VAR_MODE)
#   pct_outer  p1=inner_pct p2=o_pct_neg p3=o_pts_neg p4=o_pct_pos p5=o_pts_pos
#   pct_both   p1=inner_pct p2=inner_pts p3=o_pct_neg p4=o_pts_neg p5=o_pct_pos p6=o_pts_pos
#
# If outer_key/outer_value are given, that key is written to every case
# (held fixed) and case_prefix is prepended to the case name.
#
# Globals used: K_IN, NO_BC, CURRENT_FOLDER (all cases in this call land in
# OUTBASE/CURRENT_FOLDER/...). When NO_BC=1 (outer variable is swirl) no BC
# controller is used: cases are plain, submitted directly, and carry no
# _BC suffix.
# -------------------------------------------------------------------
run_registry_variable_sweep() {
    local var_name=$1 range_mode=$2 p1=$3 p2=${4:-} p3=${5:-} p4=${6:-} p5=${7:-} p6=${8:-}
    local outer_key=${9:-} outer_value=${10:-} case_prefix=${11:-}

    local key="${VAR_KEY[$var_name]}"
    local fmt="${VAR_FMT[$var_name]}"
    local label="${VAR_LABEL[$var_name]}"
    local mode="${VAR_MODE[$var_name]}"
    local baseline_file
    baseline_file=$(find_var_file "$key")
    echo "'$key' found in baseline: $baseline_file"

    local center="" pout=""
    if [[ "$mode" == "derived_pr" ]]; then
        pout=$(get_current_value "$PARAMETER_FILE" "pressure")
        local pin_current
        pin_current=$(get_current_value "$PARAMETER_FILE" "total_pressure")
        center=$(python3 -c "print($pin_current / $pout)")
        echo "Detected outlet pressure (fixed, from baseline): $pout"
        echo "Detected baseline inlet total pressure: $pin_current"
        echo "Detected baseline pressure ratio (center): $center"
    elif [[ "$mode" == "interval" ]]; then
        local current
        current=$(get_current_value "$baseline_file" "$key")
        echo "Detected baseline $var_name value (reference only): $current"
    elif [[ "$mode" == "magnitude" ]]; then
        center=$(get_step_height_magnitude "$baseline_file")
        echo "Detected baseline $var_name magnitude (center): $center"
    else
        center=$(get_current_value "$baseline_file" "$key")
        echo "Detected baseline $var_name value (center): $center"
    fi

    local values=()
    if [[ "$range_mode" == "list" ]]; then
        mapfile -t values < <(printf '%s' "$p1" | tr ',' '\n' | sed '/^[[:space:]]*$/d')
        echo "Explicit values for $var_name: ${values[*]}"
    elif [[ "$range_mode" == "interval" ]]; then
        mapfile -t values < <(generate_range_interval "$p1" "$p2" "$p3")
    elif [[ "$range_mode" == "pct_outer" ]]; then
        if [[ "$mode" == "interval" ]]; then
            echo "ERROR: '$var_name' is interval-mode and can't use an outer band -- use plain start/end for this variable." >&2
            exit 1
        fi
        echo "Outer-band sweep for $var_name (bands: outer): negative band [${p3} pts] out to -$(python3 -c "print(float('$p1')+float('$p2'))")%, positive band [${p5} pts] out to +$(python3 -c "print(float('$p1')+float('$p4'))")% -- boundary points at +/-${p1}% excluded"
        mapfile -t values < <(generate_range_outer_band "$center" "$p1" "$p2" "$p3" "$p4" "$p5")
    elif [[ "$range_mode" == "pct_both" ]]; then
        if [[ "$mode" == "interval" ]]; then
            echo "ERROR: '$var_name' is interval-mode and can't use an outer band -- use plain start/end for this variable." >&2
            exit 1
        fi
        echo "Combined inner+outer sweep for $var_name (bands: both): +/-${p1}% (${p2} points) plus bands out to -$(python3 -c "print(float('$p1')+float('$p3'))")% [${p4} pts] / +$(python3 -c "print(float('$p1')+float('$p5'))")% [${p6} pts]"
        local inner_values=() outer_values=()
        mapfile -t inner_values < <(generate_range_pct "$center" "$p1" "$p2")
        mapfile -t outer_values < <(generate_range_outer_band "$center" "$p1" "$p3" "$p4" "$p5" "$p6")
        values=("${inner_values[@]}" "${outer_values[@]}")
    else
        mapfile -t values < <(generate_range_pct "$center" "$p1" "$p2")
    fi

    local v formatted token case_name full_name outdir target write_value target_v outer_target bc_suffix
    for v in "${values[@]}"; do
        formatted=$(printf "$fmt" "$v")
        token=$(compact_number "$formatted")
        case_name="${case_prefix}${label}_${token}"
        if (( NO_BC )); then bc_suffix=""; else bc_suffix="_BC"; fi
        full_name="${case_name}${bc_suffix}"
        outdir="${OUTBASE}/${CURRENT_FOLDER}/${full_name}"

        prepare_case_workfiles "$outdir"

        # Hold the outer variable fixed for this whole inner pass.
        if [[ -n "$outer_key" ]]; then
            if grep -qE "^[[:space:]]*${outer_key}:" "$outdir/geometry_used.yml"; then
                outer_target="$outdir/geometry_used.yml"
            else
                outer_target="$outdir/parameters_used.yml"
            fi
            update_key_in_file "$outer_target" "$outer_key" "$outer_value"
        fi

        if [[ "$mode" == "derived_pr" ]]; then
            write_value=$(python3 -c "print($v * $pout)")
            update_key_in_file "$outdir/parameters_used.yml" "total_pressure" "$write_value"
        elif [[ "$mode" == "magnitude" ]]; then
            apply_step_height_magnitude "$outdir/geometry_used.yml" "$formatted"
        else
            if [[ "$baseline_file" == "$GEOMETRY_FILE" ]]; then
                target="$outdir/geometry_used.yml"
            else
                target="$outdir/parameters_used.yml"
            fi
            update_key_in_file "$target" "$key" "$formatted"
        fi

        if (( NO_BC )); then
            target_v=""
            echo "  [$case_name] no BC controller (swirl is set directly)"
        else
            target_v=$(compute_target_velocity "$outdir/geometry_used.yml" "$outdir/parameters_used.yml" "$K_IN")
            echo "  [$case_name] target inlet velocity = $target_v (K_in=$K_IN)"
        fi

        run_and_ship "$full_name" "$outdir" "$target_v" "$CURRENT_FOLDER"
    done
}

# One pass over every inner: entry (INNER_LINES), with an optional outer
# variable held fixed. $1=outer_key $2=outer_value $3=case_prefix (all may be empty).
run_inner_pass() {
    local outer_key=$1 outer_value=$2 case_prefix=$3
    local line var range_mode p1 p2 p3 p4 p5 p6
    for line in "${INNER_LINES[@]}"; do
        IFS=$'\t' read -r var range_mode p1 p2 p3 p4 p5 p6 <<< "$line"
        if [[ -z "${VAR_KEY[$var]+x}" ]]; then
            echo "WARNING: inner variable '$var' is not in the VAR_KEY registry -- skipped." >&2
            continue
        fi
        run_registry_variable_sweep "$var" "$range_mode" "$p1" "$p2" "$p3" "$p4" "$p5" "$p6" \
            "$outer_key" "$outer_value" "$case_prefix"
    done
}

# Run the whole plan (outer + inner), once, using the constant K_IN.
run_plan() {
    local n_inner=${#INNER_LINES[@]}

    # No outer: flat inner-only sweep, folder/prefix keyed by K_in.
    if [[ -z "$OUTER_VAR" ]]; then
        local k_formatted k_token
        k_formatted=$(printf "$K_IN_FMT" "$K_IN")
        k_token=$(compact_number "$k_formatted")
        CURRENT_FOLDER="K${k_token}"
        echo "=== No outer variable -- folder/prefix keyed by K_in=$K_IN -> ${CURRENT_FOLDER} ==="
        run_inner_pass "" "" "${CURRENT_FOLDER}_"
        return
    fi

    if [[ "${VAR_MODE[$OUTER_VAR]}" == "magnitude" ]]; then
        echo "ERROR: outer variable '$OUTER_VAR' is list-valued (magnitude mode) and can't be used as an outer with inner sweeps." >&2
        exit 1
    fi

    local outer_key="${VAR_KEY[$OUTER_VAR]}"
    local outer_label="${VAR_LABEL[$OUTER_VAR]}"
    # Outer formatting ALWAYS comes from the registry (VAR_FMT), same as
    # every inner sweep -- never from the plan file's `fmt:` field. Using a
    # plan-supplied fmt here previously truncated real values (e.g. clearance
    # 0.0000230 rounded to 4 decimals -> "0.0000", written as zero clearance).
    local outer_fmt="${VAR_FMT[$OUTER_VAR]}"
    if [[ -n "$OUTER_FMT" ]]; then
        echo "NOTE: plan file's outer 'fmt: ${OUTER_FMT}' is ignored -- using registry precision ${outer_fmt} for $OUTER_VAR instead."
    fi
    local raw_val outer_val_formatted outer_token outer_write_val outer_pout

    for raw_val in "${OUTER_VALUES[@]}"; do
        outer_val_formatted=$(printf "$outer_fmt" "$raw_val")
        outer_token=$(compact_number "$outer_val_formatted")
        CURRENT_FOLDER="${outer_label}${outer_token}"

        # derived_pr: the yaml key is total_pressure, so the value written
        # must be PR * outlet_pressure, not the raw ratio. Case names still
        # show the clean ratio.
        if [[ "${VAR_MODE[$OUTER_VAR]}" == "derived_pr" ]]; then
            outer_pout=$(get_current_value "$PARAMETER_FILE" "pressure")
            outer_write_val=$(python3 -c "print($outer_val_formatted * $outer_pout)")
            echo "=== Outer: $OUTER_VAR = $outer_val_formatted (PR) -> total_pressure = $outer_write_val (outlet pressure = $outer_pout) -- folder ${CURRENT_FOLDER} ==="
        else
            outer_write_val="$outer_val_formatted"
            echo "=== Outer: $OUTER_VAR = $outer_val_formatted -- folder ${CURRENT_FOLDER} ==="
        fi

        run_inner_pass "$outer_key" "$outer_write_val" "${CURRENT_FOLDER}_"
    done
}

# -------------------------------------------------------------------
# Plan loading (the only source of the sweep now -- no interactive path).
# -------------------------------------------------------------------
load_plan_from_yaml() {
    local outer_lines=() line tag val
    OUTER_VAR=""
    OUTER_FMT=""
    OUTER_VALUES=()
    K_IN=""

    mapfile -t outer_lines < <(parse_outer_config)
    if (( ${#outer_lines[@]} > 0 )); then
        for line in "${outer_lines[@]}"; do
            IFS=$'\t' read -r tag val <<< "$line"
            case "$tag" in
                VARIABLE) OUTER_VAR="$val" ;;
                FMT)      OUTER_FMT="$val" ;;
                VALUE)    OUTER_VALUES+=("$val") ;;
            esac
        done
        if [[ -z "${VAR_KEY[$OUTER_VAR]+x}" && "$OUTER_VAR" != "number_of_fins" && "$OUTER_VAR" != "step_height_profile" ]]; then
            echo "ERROR: outer variable '$OUTER_VAR' is not in the VAR_KEY registry." >&2
            exit 1
        fi
    fi

    # Special sweeps still get their own interactive prompts, triggered by
    # naming them as the outer variable (own prompts, no BC/K_in).
    if [[ "$OUTER_VAR" == "number_of_fins" || "$OUTER_VAR" == "step_height_profile" ]]; then
        LEGACY_VAR="$OUTER_VAR"
        return
    fi

    mapfile -t INNER_LINES < <(parse_inner_entries)

    if [[ -z "$OUTER_VAR" && ${#INNER_LINES[@]} -eq 0 ]]; then
        echo "ERROR: no usable 'outer:' or 'inner:' entries found in $k_plan_FILE" >&2
        exit 1
    fi

    if [[ "$OUTER_VAR" == "swirl" ]]; then
        echo "Outer variable is swirl -> K_in is ignored and the BC controller is not used."
        NO_BC=1
        K_IN=""
    else
        K_IN=$(get_k_in) || exit 1
    fi
}

# -------------------------------------------------------------------
# Special sweeps with their own prompts (not BC, not K_in-driven, run once):
#   number_of_fins, step_height_profile.
# -------------------------------------------------------------------
run_legacy_sweep() {
    local var_name=$1

    if [[ "$var_name" == "step_height_profile" ]]; then
        # Build a brand-new fin/step profile from scratch.
        local n_fins n_steps inv_mode
        read -p "Enter number of fins: " n_fins
        if (( n_fins < 2 )); then
            echo "Need at least 2 fins."
            exit 1
        fi
        n_steps=$(( n_fins - 1 ))

        read -p "Is there an inversion? (yes/no): " inv_mode
        inv_mode=$(echo "$inv_mode" | tr '[:upper:]' '[:lower:]')

        if [[ "$inv_mode" == "no" ]]; then
            local h_center h_var h_points h_values=() h hf h_token step_heights case_name outdir
            read -p "Center step height: " h_center
            read -p "Variation (%): " h_var
            read -p "Number of simulations: " h_points
            mapfile -t h_values < <(generate_range_pct "$h_center" "$h_var" "$h_points")

            for h in "${h_values[@]}"; do
                hf=$(printf "%.6f" "$h")
                h_token=$(compact_number "$hf")
                step_heights=$(make_step_list "$hf" "$n_steps")
                case_name="NF${n_fins}_SH${h_token}"
                outdir="${OUTBASE}/${case_name}"
                prepare_case_workfiles "$outdir"
                update_step_geometry "$outdir/geometry_used.yml" "$n_fins" "$step_heights"
                run_and_ship "$case_name" "$outdir"
            done

        elif [[ "$inv_mode" == "yes" ]]; then
            local inv_fin before_steps after_steps
            local h1_center h1_var h1_points h2_center h2_var h2_points
            local h1_values=() h2_values=() h1 h2 h1f h2f h1_token h2_token step_heights case_name outdir
            read -p "Inversion fin index (2..${n_fins}): " inv_fin
            before_steps=$(( inv_fin - 1 ))
            after_steps=$(( n_steps - before_steps ))

            echo "--- BEFORE inversion ---"
            read -p "Center step height: " h1_center
            read -p "Variation (%): " h1_var
            read -p "Number of simulations: " h1_points

            echo "--- AFTER inversion ---"
            read -p "Center step height: " h2_center
            read -p "Variation (%): " h2_var
            read -p "Number of simulations: " h2_points

            mapfile -t h1_values < <(generate_range_pct "$h1_center" "$h1_var" "$h1_points")
            mapfile -t h2_values < <(generate_range_pct "$h2_center" "$h2_var" "$h2_points")

            for h1 in "${h1_values[@]}"; do
                for h2 in "${h2_values[@]}"; do
                    h1f=$(printf "%.6f" "$h1")
                    h2f=$(printf "%.6f" "$h2")
                    h1_token=$(compact_number "$h1f")
                    h2_token=$(compact_number "$h2f")
                    step_heights=$(make_step_list "$h1f" "$before_steps" "$h2f" "$after_steps")
                    case_name="NF${n_fins}_SH${h1_token}_${h2_token}"
                    outdir="${OUTBASE}/${case_name}"
                    prepare_case_workfiles "$outdir"
                    update_step_geometry "$outdir/geometry_used.yml" "$n_fins" "$step_heights"
                    run_and_ship "$case_name" "$outdir"
                done
            done
        else
            echo "Invalid input: expected 'yes' or 'no'."
            exit 1
        fi

    elif [[ "$var_name" == "number_of_fins" ]]; then
        # Sweep the fin count; step_height is resized to match.
        local baseline_distance_fin fins_mode fins_start fins_end fins_step fin_counts=()
        local n_fins distance_fin distance_fin_fmt distance_fin_token case_name outdir

        baseline_distance_fin=$(get_current_value "$GEOMETRY_FILE" "distance_fin")
        echo "Baseline distance_fin: $baseline_distance_fin"
        echo "number_of_fins has two modes:"
        echo "  1) Keep TOTAL fin span fixed (${TOTAL_FIN_SPAN} m) -- distance_fin = ${TOTAL_FIN_SPAN} / (n_fins + 1)"
        echo "  2) Keep distance_fin fixed at the baseline value (${baseline_distance_fin})"
        read -p "Mode (1/2): " fins_mode
        read -p "Start number of fins: " fins_start
        read -p "End number of fins: " fins_end
        read -p "Step (increment): " fins_step

        mapfile -t fin_counts < <(seq "$fins_start" "$fins_step" "$fins_end")

        for n_fins in "${fin_counts[@]}"; do
            if (( n_fins < 2 )); then
                echo "Skipping n_fins=$n_fins (need at least 2 fins)."
                continue
            fi

            if [[ "$fins_mode" == "1" ]]; then
                distance_fin=$(python3 -c "print($TOTAL_FIN_SPAN / ($n_fins + 1))")
            else
                distance_fin="$baseline_distance_fin"
            fi
            distance_fin_fmt=$(printf "%.6f" "$distance_fin")
            distance_fin_token=$(compact_number "$distance_fin_fmt")

            case_name="NF${n_fins}_DIST${distance_fin_token}"
            outdir="${OUTBASE}/${case_name}"

            prepare_case_workfiles "$outdir"
            update_key_in_file "$outdir/geometry_used.yml" "number_of_fins" "$n_fins"
            resize_step_height "$outdir/geometry_used.yml" "$n_fins"
            if [[ "$fins_mode" == "1" ]]; then
                update_key_in_file "$outdir/geometry_used.yml" "distance_fin" "$distance_fin_fmt"
            fi

            run_and_ship "$case_name" "$outdir"
        done
    fi
}

# =========================================================================
# Main
# =========================================================================
# Library-only mode so cara_bc_finalize.sh can `BC_SWEEP_LIB_ONLY=1 source`
# this file without triggering the sweep.
if [[ "${BC_SWEEP_LIB_ONLY:-0}" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi

# Globals used by the sweep functions.
CASE_NAMES=()
FAILED_CASES=()
HAD_BC_CASES=0
NO_BC=0
K_IN=""
OUTER_VAR=""
OUTER_FMT=""
OUTER_VALUES=()
INNER_LINES=()
LEGACY_VAR=""
CURRENT_FOLDER=""

# Opens the BASELINE files for manual editing (do this in only one tab if
# running several at once).
kate "$GEOMETRY_FILE" "$PARAMETER_FILE"

cd "$WORKDIR" || { echo "Cannot enter working directory"; exit 1; }

OUTBASE="$LOCAL_SIMS_BASE"
mkdir -p "$OUTBASE"
ssh cara.dlr.de "mkdir -p ${REMOTE_SIMS_BASE}"

load_plan_from_yaml

if [[ -n "$LEGACY_VAR" ]]; then
    # Special sweeps: own prompts, no K_in, no BC, flat local folder.
    run_legacy_sweep "$LEGACY_VAR"
else
    echo "=============================="
    echo "Sweep plan (read from $k_plan_FILE):"
    if [[ -n "$OUTER_VAR" ]]; then
        echo "  outer : $OUTER_VAR = ${OUTER_VALUES[*]}"
    else
        echo "  outer : (none) -- folder keyed by K_in instead"
    fi
    echo "  inner : ${#INNER_LINES[@]} entr$( (( ${#INNER_LINES[@]} == 1 )) && echo y || echo ies )"
    if (( NO_BC )); then
        echo "  K_in  : ignored (swirl is the outer variable) -- direct cases, no BC controller"
    else
        echo "  K_in  : $K_IN (constant, used for BC target velocity only -- never in folder/case names when outer: is set)"
    fi
    echo "=============================="

    run_plan
fi

# =========================================================================
# Final summary. Every successful case was already shipped, submitted and
# its local copy removed. Failed cases either failed meshing/prep, or failed
# scp (in which case the local folder under $LOCAL_SIMS_BASE was kept).
# =========================================================================
echo "=============================="
echo "Sweep finished: ${#CASE_NAMES[@]} submitted, ${#FAILED_CASES[@]} failed."
if (( ${#FAILED_CASES[@]} > 0 )); then
    echo "Failed cases (never submitted):"
    printf '  %s\n' "${FAILED_CASES[@]}"
fi
if [[ "$HAD_BC_CASES" == "1" ]]; then
    echo "These were BC cases. Once they've converged on the HPC, run:"
    echo "  ./cara_bc_finalize.sh"
    echo "to extract angles and submit the real (non-BC) simulations."
fi
echo "=============================="
