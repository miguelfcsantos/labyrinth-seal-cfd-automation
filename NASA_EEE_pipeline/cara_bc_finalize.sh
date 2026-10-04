#!/bin/bash
#
# bc_finalize.sh
# -----------------------------------------------------------------------
# Boundary-controller (BC) case finalizer.
#
# Polls an HPC cluster for "boundary-controller" (BC) simulation cases
# that are running a feedback-controlled sweep to find a converged
# inlet angle. Once a case's angle has converged, this script:
#   1. Pulls down that case's geometry/parameter YAML files as-is.
#   2. Builds a "real" case locally using the same geometry, but with
#      the converged angle fixed as velocity_angle_alpha, and no
#      controller block (the real case runs with a fixed angle, not a
#      target-seeking one).
#   3. Copies the full convergence history file into the real case
#      folder as a record.
#   4. Submits the real case to SLURM.
#   5. Best-effort cancels the (likely still-running) BC SLURM job and
#      archives its remote folder out of the way.
#
# GROUPING: real-case submissions and archived BC folders are bucketed
# by "outer loop" -- everything in the case name before the first
# underscore (e.g. "K6" for K6_RPM_180258_BC, "PR20000" for
# PR20000_SH_213_BC). This is derived automatically from the case name
# each pass; group subfolders are created on demand:
#   ${REMOTE_SIMS_BASE}/<group>/<case>            (real case, submitted here)
#   ${REMOTE_SIMS_BC_DONE}/<group>/<case>_BC      (archived after finalizing)
#
# READING BC folders makes no assumption about depth -- the script does
# a full recursive `find` under $REMOTE_SIMS_BASE for any "*_BC"
# directory, wherever it actually sits (flat, nested under a group
# folder, or otherwise). Each case's actual remote path is captured at
# discovery time and reused for every subsequent read (yaml fetch,
# job-cancel workdir, archive-mv source) -- nothing is reconstructed by
# guessing a path from the case name. Only the two OUTPUT locations
# above enforce the grouped layout.
#
# CONVERGENCE CRITERION: a case is considered converged once its angle
# column, truncated to 3 decimal places (integer part + all three
# decimal digits, not just the isolated 3rd digit), is identical across
# 3 consecutive data rows. The reported angle is that row's value
# truncated (not rounded) to 2 decimal places. Truncation is done via
# string formatting rather than floor()/round() arithmetic to avoid
# floating-point representation noise flipping a digit that "should" be
# exact.
#
#   Worked example:
#     78.4794  78.4586  78.4577  78.4576  78.4571
#     truncated to 3dp:  78.479   78.458   78.457   78.457   78.457
#   The last three rows all truncate to "78.457" -> converged at the
#   5th row, reported angle truncated to 78.45.
#
# IDEMPOTENCY: a local ledger file records every real case that has
# been finalized (built + submitted). A case is marked finalized
# immediately after submission succeeds -- before the archive/cleanup
# step even runs -- so the ledger's correctness never depends on that
# archive step succeeding. This prevents a case whose post-submit
# archive step failed (transient ssh hiccup, permissions, etc.) from
# being rebuilt and resubmitted again on a later poll, since its BC
# folder would otherwise still be sitting there looking "converged".
#
# This script is fully self-contained -- nothing is sourced from any
# other script.
# -----------------------------------------------------------------------

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -------------------------------------------------------------------
# Environment / path configuration.
# -------------------------------------------------------------------
WORKDIR="/path/to/testcase"
BASECASE="/path/to/geometry_generator"
LOCAL_SIMS_BASE="/path/to/local/sims"
REMOTE_SIMS_BASE="/path/to/hpc/scratch/sims"
TRACESTART_SCRIPT="/path/to/hpc/scripts/tracestart.sh"

HPC_USER="your_username"
HPC_HOST="hpc.example.com"
REMOTE_SIMS_BC_DONE="${REMOTE_SIMS_BASE}_bc_done"   # archive for harvested _BC folders

# -------------------------------------------------------------------
# Write a value into a top-level (or nested) yaml key, preserving
# indentation and any trailing inline comment.
# -------------------------------------------------------------------
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

# -------------------------------------------------------------------
# Runs the mesh + prep + gmc pipeline for one case. No target_v /
# controller-injection branch -- the real case never gets one.
# -------------------------------------------------------------------
run_case() {
    local case_name=$1
    local outdir=$2
    local param_file=$3
    local geom_file=$4

    echo "=============================="
    echo "Running case: $case_name"
    echo "=============================="

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

    cd "$outdir/input" || { echo "Missing input folder for $case_name"; return 1; }

    prep.py -clb -cgns TRACE.cgns -np 128 -sb TRACE_split.cgns splitScript.jou mergeScript.jou
    gmcPlay splitScript.jou
    gmcPlay /path/to/conv_gmc.jou
    gmcPlay /path/to/models/OmegaSST_Off_Bardina.jou
    cd "$WORKDIR"
    return 0
}

# -------------------------------------------------------------------
# scp's ONE case into its group's remote sims subdirectory
# (${REMOTE_SIMS_BASE}/<group>/) and submits it immediately, then
# removes the local copy once the scp is confirmed to have succeeded.
# If the mkdir or the scp fails, the local copy is deliberately left
# in place.
# -------------------------------------------------------------------
ship_and_submit() {
    local case_name=$1
    local outdir=$2
    local group=$3
    local remote_group_dir="${REMOTE_SIMS_BASE}/${group}"

    if ! ssh_run "mkdir -p '${remote_group_dir}'"; then
        echo "ERROR: could not create ${remote_group_dir} on HPC -- leaving local copy at $outdir, not submitting."
        return 1
    fi

    if ! scp -r "$outdir" "${HPC_HOST}:${remote_group_dir}/"; then
        echo "ERROR: scp failed for $case_name -- leaving local copy at $outdir, not submitting."
        return 1
    fi

    ssh "${HPC_HOST}" "sbatch --chdir=${remote_group_dir}/${case_name}/input $TRACESTART_SCRIPT"
    echo "Submitted: ${remote_group_dir}/${case_name}"

    rm -rf "$outdir"
    echo "Removed local copy: $outdir"
}

# -------------------------------------------------------------------
# Idempotency ledger -- DO NOT REMOVE. This is what stops a converged
# _BC case from being rebuilt + resubmitted again on every subsequent
# poll if the post-submit scancel/archive-mv step ever fails (transient
# ssh hiccup, permissions, the BC job still holding the directory, the
# script started twice, etc). A case is marked finalized IMMEDIATELY
# after run_case + ship_and_submit succeeds -- BEFORE the archive step
# even runs -- so the ledger's correctness never depends on the mv
# succeeding.
# -------------------------------------------------------------------
STATE_FILE="${SCRIPT_DIR}/.bc_finalized_cases"
touch "$STATE_FILE"

# -------------------------------------------------------------------
# Group = everything in a case name before the first underscore, e.g.
# "K6" for K6_RPM_180258(_BC), "PR20000" for PR20000_SH_213(_BC). Works
# the same whether or not the _BC suffix is present, since the suffix
# comes after later underscores.
# -------------------------------------------------------------------
group_of() {
    local name=$1
    printf '%s' "${name%%_*}"
}

is_finalized() {
    local real_case_name=$1
    grep -qxF "$real_case_name" "$STATE_FILE"
}

mark_finalized() {
    local real_case_name=$1
    echo "$real_case_name" >> "$STATE_FILE"
}

BC_DAT_REL="output/residual/bcControl_v.dat"
MARKER="___FILE_BOUNDARY___"

# How often to re-poll the HPC, in seconds. Override with e.g.
#   POLL_INTERVAL_SECONDS=120 ./bc_finalize.sh
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-300}"

# Pass --once to do a single pass and exit instead of looping forever.
RUN_ONCE=0
if [[ "${1:-}" == "--once" ]]; then
    RUN_ONCE=1
fi

ssh_run() {
    ssh "${HPC_USER}@${HPC_HOST}" "$1"
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# -------------------------------------------------------------------
# ONE ssh call that walks EVERY "*_BC" folder anywhere under
# $REMOTE_SIMS_BASE (no assumption about depth -- flat, nested under a
# group folder, or otherwise) and, for each, emits a marker line with
# "<case_name>|<path-relative-to-REMOTE_SIMS_BASE>" followed by the raw
# content of its bcControl_v.dat (empty if not present yet -- job
# hasn't reached the controller / hasn't started). Capturing the real
# relative path here (rather than reconstructing one later from the
# case name) makes this robust regardless of whatever layout the _BC
# folder actually landed in.
# -------------------------------------------------------------------
fetch_all_bc_status() {
    ssh_run "
find '${REMOTE_SIMS_BASE}' -type d -name '*_BC' | while IFS= read -r d; do
    case_name=\$(basename \"\$d\")
    rel=\"\${d#${REMOTE_SIMS_BASE}/}\"
    echo '${MARKER}:'\"\${case_name}|\${rel}\"
    f=\"\${d}/${BC_DAT_REL}\"
    if [ -f \"\$f\" ]; then
        cat \"\$f\"
    fi
done
"
}

# -------------------------------------------------------------------
# Split the combined fetch_all_bc_status() output into one "<case>.dat"
# file (bcControl_v.dat content) and one "<case>.relpath" file (the
# actual remote path, relative to $REMOTE_SIMS_BASE, where that _BC
# folder was found) per case, under $status_dir.
# -------------------------------------------------------------------
split_bc_status() {
    local all_text=$1 status_dir=$2
    python3 - "$status_dir" <<PYEOF
import sys, os

status_dir = sys.argv[1]
marker = "$MARKER"
text = """$all_text"""

current = None
buf = []

def flush():
    if current is not None:
        with open(os.path.join(status_dir, current + ".dat"), "w") as f:
            f.write("\n".join(buf))

for line in text.splitlines():
    if line.startswith(marker + ":"):
        flush()
        key = line.split(":", 1)[1].strip()
        case_name, rel = key.split("|", 1)
        current = case_name
        buf = []
        with open(os.path.join(status_dir, case_name + ".relpath"), "w") as f:
            f.write(rel)
    else:
        buf.append(line)
flush()
PYEOF
}

# -------------------------------------------------------------------
# Fetch geometry_used.yml + parameters_used.yml for a case, one ssh
# call. Takes the case's ACTUAL remote directory (as discovered by
# fetch_all_bc_status), not a reconstructed group/case_name path.
# -------------------------------------------------------------------
fetch_case_yaml() {
    local dir=$1
    ssh_run "
echo '${MARKER}:geometry'
cat '${dir}/geometry_used.yml'
echo '${MARKER}:parameters'
cat '${dir}/parameters_used.yml'
"
}

# -------------------------------------------------------------------
# Given raw bcControl_v.dat text, print the converged angle (string) on
# stdout and exit 0, or exit 1 (nothing on stdout) if not converged
# yet.
#
# Convergence rule: truncate the angle column (index 1, 0-based) to 3
# decimal places (integer part + all three decimal digits -- not just
# the isolated 3rd digit) and require that truncated value to be
# identical for 3 consecutive rows. The reported angle is that row's
# value truncated (not rounded) to 2 decimal places.
#
# Worked example:
#   78.4794  78.4586  78.4577  78.4576  78.4571
#   truncated to 3dp:  78.479   78.458   78.457   78.457   78.457
# The last three rows all truncate to "78.457" -> converged at the 5th
# row, reported angle truncated to 78.45.
#
# Truncation is done via string formatting (not floor()/round()
# arithmetic) to avoid floating-point representation noise flipping a
# digit that "should" be exact (e.g. 78.4571 sometimes being stored as
# 78.45709999999...).
#
# Header/aux lines (DATASETAUXDATA, VARAUXDATA, ZONE, TITLE, blank) are
# skipped.
# -------------------------------------------------------------------
check_convergence() {
    local dat_file=$1
    python3 -c "
import sys

SKIP = ('DATASETAUXDATA', 'VARAUXDATA', 'ZONE', 'TITLE')

with open('$dat_file') as f:
    text = f.read()

angles = []
for line in text.splitlines():
    s = line.strip()
    if not s or s.upper().startswith(SKIP):
        continue
    parts = s.split()
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        continue
    if len(nums) < 2:
        continue
    angles.append(nums[1])   # column index 1 = angle

def truncate_3dp(x):
    s = f'{x:.6f}'
    int_part, frac = s.split('.')
    return f'{int_part}.{frac[:3]}'

def truncate_2dp(x):
    s = f'{x:.6f}'
    int_part, frac = s.split('.')
    return f'{int_part}.{frac[:2]}'

keys = [truncate_3dp(a) for a in angles]

for i in range(2, len(angles)):
    if keys[i-2] == keys[i-1] == keys[i]:
        print(truncate_2dp(angles[i]))
        sys.exit(0)

sys.exit(1)
"
}

# -------------------------------------------------------------------
# Best-effort: try to find and cancel the SLURM job running this _BC
# case, by matching the job's working directory. Takes the case's
# ACTUAL remote directory (as discovered by fetch_all_bc_status), not a
# reconstructed group/case_name path. Verify this matches your actual
# squeue output before trusting it.
# -------------------------------------------------------------------
try_cancel_bc_job() {
    local case_name=$1 bc_dir=$2
    local workdir="${bc_dir}/input"
    local job_id
    job_id=$(ssh_run "squeue -u ${HPC_USER} -h -o '%i %Z' | awk -v d='${workdir}' '\$2==d {print \$1}'")
    if [[ -n "$job_id" ]]; then
        echo "  Cancelling SLURM job $job_id for $case_name"
        ssh_run "scancel $job_id"
    else
        echo "  WARNING: could not find a running SLURM job for $case_name (workdir match failed) -- leaving it, if it's still running it'll finish on its own."
    fi
}

# =========================================================================
# One pass: check every _BC case once, finalize whichever have converged.
# =========================================================================
run_one_pass() {
    log "Polling ${HPC_USER}@${HPC_HOST}:${REMOTE_SIMS_BASE} for *_BC cases..."

    local status_dir
    status_dir=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$status_dir'" RETURN

    local all_text
    all_text=$(fetch_all_bc_status)

    if [[ -z "$all_text" ]]; then
        log "No _BC cases found under ${REMOTE_SIMS_BASE} on ${HPC_HOST} (searched recursively). If you expect cases to exist, double check REMOTE_SIMS_BASE points at the right directory and that it actually exists there."
        return 0
    fi

    split_bc_status "$all_text" "$status_dir"

    mapfile -t bc_cases < <(cd "$status_dir" && ls -- *.dat 2>/dev/null | sed 's/\.dat$//')

    if [[ ${#bc_cases[@]} -eq 0 ]]; then
        log "No _BC cases found."
        return 0
    fi

    log "Found ${#bc_cases[@]} _BC case(s): ${bc_cases[*]}"

    local CONVERGED=() PENDING=() FAILED=() SKIPPED=()

    ssh_run "mkdir -p ${REMOTE_SIMS_BC_DONE}"

    local case_name dat_file
    for case_name in "${bc_cases[@]}"; do
        dat_file="$status_dir/${case_name}.dat"
        echo "------------------------------"
        echo "[$case_name] checking convergence..."

        if [[ ! -s "$dat_file" ]]; then
            echo "  bcControl_v.dat not present yet -- still starting up."
            PENDING+=("$case_name")
            continue
        fi

        local angle
        if ! angle=$(check_convergence "$dat_file"); then
            echo "  Not converged yet."
            PENDING+=("$case_name")
            continue
        fi

        echo "  Converged: angle = $angle"

        local group real_case_name outdir bc_relpath bc_dir
        group="$(group_of "$case_name")"
        real_case_name="${case_name%_BC}"
        bc_relpath=$(cat "$status_dir/${case_name}.relpath")
        bc_dir="${REMOTE_SIMS_BASE}/${bc_relpath}"

        if is_finalized "$real_case_name"; then
            echo "  SKIPPING: '$real_case_name' was already finalized/submitted in a previous pass, but its _BC folder is still on the HPC (its archive step must have failed last time)."
            echo "  Not resubmitting. Please manually verify/move ${bc_dir} -> ${REMOTE_SIMS_BC_DONE}/${group}/ on the HPC."
            SKIPPED+=("$case_name")
            continue
        fi

        outdir="${LOCAL_SIMS_BASE}/${real_case_name}"

        # --- pull down this case's yaml, split into geometry/parameters ---
        local yaml_text
        yaml_text=$(fetch_case_yaml "$bc_dir")
        mkdir -p "$outdir"
        python3 - "$outdir" <<PYEOF
import sys
outdir = sys.argv[1]
marker = "$MARKER"
text = """$yaml_text"""

sections = {}
current = None
buf = []
for line in text.splitlines():
    if line.startswith(marker + ":"):
        if current is not None:
            sections[current] = "\n".join(buf)
        current = line.split(":", 1)[1].strip()
        buf = []
    else:
        buf.append(line)
if current is not None:
    sections[current] = "\n".join(buf)

with open(outdir + "/geometry_used.yml", "w") as f:
    f.write(sections.get("geometry", ""))
with open(outdir + "/parameters_used.yml", "w") as f:
    f.write(sections.get("parameters", ""))
PYEOF

        # --- set the converged angle as the real (fixed) inlet angle ---
        update_key_in_file "$outdir/parameters_used.yml" "velocity_angle_alpha" "$angle"

        # --- stash the full convergence history alongside, for the record ---
        cp "$dat_file" "$outdir/bcControl_v_converged.dat"

        # --- build + submit the real case (no target_v => no controller block) ---
        if run_case "$real_case_name" "$outdir" "$outdir/parameters_used.yml" "$outdir/geometry_used.yml"; then
            if ship_and_submit "$real_case_name" "$outdir" "$group"; then
                CONVERGED+=("$real_case_name")
                # Record success BEFORE attempting the archive step below,
                # so a failed/late mv can never cause this case to be
                # finalized (and its real sim resubmitted) a second time.
                mark_finalized "$real_case_name"
            else
                FAILED+=("$real_case_name (ship_and_submit failed, local copy kept at $outdir)")
                continue
            fi
        else
            FAILED+=("$real_case_name (run_case failed, local copy kept at $outdir)")
            continue
        fi

        # --- retire the _BC case on the HPC ---
        try_cancel_bc_job "$case_name" "$bc_dir"
        ssh_run "mkdir -p '${REMOTE_SIMS_BC_DONE}/${group}'"
        if ssh_run "mv '${bc_dir}' '${REMOTE_SIMS_BC_DONE}/${group}/${case_name}'"; then
            echo "  Archived _BC case to ${REMOTE_SIMS_BC_DONE}/${group}/${case_name}"
        else
            echo "  WARNING: archive mv failed for ${case_name}. The real sim was already submitted and is recorded in $STATE_FILE, so it will NOT be resubmitted -- but ${bc_dir} still needs to be moved/cleaned up manually on the HPC, or it will keep showing up as 'converged' every pass (harmlessly, since it's now skipped)."
        fi
    done

    echo "=============================="
    log "Pass done. ${#CONVERGED[@]} finalized, ${#PENDING[@]} still running, ${#FAILED[@]} failed, ${#SKIPPED[@]} skipped (already finalized)."
    if (( ${#CONVERGED[@]} > 0 )); then
        echo "Finalized (real sims submitted):"
        printf '  %s\n' "${CONVERGED[@]}"
    fi
    if (( ${#PENDING[@]} > 0 )); then
        echo "Still running / not converged yet:"
        printf '  %s\n' "${PENDING[@]}"
    fi
    if (( ${#FAILED[@]} > 0 )); then
        echo "Failed:"
        printf '  %s\n' "${FAILED[@]}"
    fi
    if (( ${#SKIPPED[@]} > 0 )); then
        echo "Skipped (already finalized previously, needs manual HPC cleanup -- see WARNING above):"
        printf '  %s\n' "${SKIPPED[@]}"
    fi
    echo "=============================="
}

# =========================================================================
# Main
# =========================================================================

if (( RUN_ONCE == 1 )); then
    run_one_pass
    exit 0
fi

trap 'echo; log "Stopping (Ctrl+C)."; exit 0' INT TERM

log "Starting periodic finalizer -- checking every ${POLL_INTERVAL_SECONDS}s. Ctrl+C to stop, or run with --once for a single pass."
while true; do
    run_one_pass
    log "Sleeping ${POLL_INTERVAL_SECONDS}s until next check..."
    sleep "$POLL_INTERVAL_SECONDS"
done
