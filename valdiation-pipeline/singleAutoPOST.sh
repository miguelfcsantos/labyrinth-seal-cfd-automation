#!/bin/bash
# autoPost.sh — full post-processing pipeline for a single simulation
#
# Usage:
#   bash autoPost.sh /localdata1/corr_mi/OI/sims/LC   ← single sim (original behaviour)
#   bash autoPost.sh                                    ← process ALL sims in default folder
set -e

DEFAULT_SIMS_DIR="/localdata1/corr_mi/OI/TEST/sims"

# HPC destination for the finished, post-processed sim folder
HPC_HOST="cara.dlr.de"
HPC_SCRATCH="/scratch/ws25/corr_mi-den_mig/POST_SIMS"

# Heavy files to delete locally once the post-processed folder has
# been successfully uploaded to the HPC. Paths are relative to
# DONE_DIR/$SIM_NAME.
HEAVY_FILES=(
    "output/cgns/TRACE.cgns.backup"
    "output/cgns/TRACE.cgns"
    "output/cgns/TRACE_merged.cgns"
    "output/cgns/TRACE_merged_post.cgns"
    "input/TRACE_split.cgns"
    "input/TRACE.cgns"
)

# ── Argument handling ─────────────────────────────────────────────────────────
if [ -z "$1" ]; then
    # No argument → loop over every subdirectory in the default sims folder
    if [ ! -d "$DEFAULT_SIMS_DIR" ]; then
        echo "[autoPost] ERROR: default sims folder not found: $DEFAULT_SIMS_DIR"
        exit 1
    fi
    SIM_DIRS=("$DEFAULT_SIMS_DIR"/*/)   # trailing slash = directories only
    if [ ${#SIM_DIRS[@]} -eq 0 ]; then
        echo "[autoPost] No simulations found in $DEFAULT_SIMS_DIR"
        exit 0
    fi
    echo ""
    echo "======================================================"
    echo " autoPost BATCH MODE"
    echo " Found ${#SIM_DIRS[@]} simulation(s) in $DEFAULT_SIMS_DIR"
    echo "======================================================"
    FAILED=()
    for SIM_DIR in "${SIM_DIRS[@]}"; do
        SIM_DIR="${SIM_DIR%/}"   # strip trailing slash
        bash "$0" "$SIM_DIR" || FAILED+=("$(basename "$SIM_DIR")")
    done
    echo ""
    echo "======================================================"
    echo " BATCH COMPLETE"
    echo " Processed: ${#SIM_DIRS[@]}  |  Failed: ${#FAILED[@]}"
    if [ ${#FAILED[@]} -gt 0 ]; then
        echo " Failed sims:"
        for f in "${FAILED[@]}"; do echo "   - $f"; done
    fi
    echo "======================================================"
    exit 0
fi

# ── Single-sim mode (original behaviour) ─────────────────────────────────────
SIM_DIR="$1"
if [ ! -d "$SIM_DIR" ]; then
    echo "[autoPost] ERROR: folder not found: $SIM_DIR"
    exit 1
fi
SIM_NAME=$(basename "$SIM_DIR")

echo ""
echo "======================================================"
echo " autoPost starting: $SIM_NAME"
echo " Path: $SIM_DIR"
echo "======================================================"

echo ""
echo "[1/6] Generating residuals..."
python3 /home/corr_mi/genRESIDUALS_single.py "$SIM_DIR"

echo ""
echo "[2/6] Generating grid..."
python3 /home/corr_mi/griddd_single.py "$SIM_DIR"

echo ""
echo "[3/6] Running merger + POST..."
python3 /home/corr_mi/merger_single.py "$SIM_DIR"
DONE_DIR="/localdata1/corr_mi/OI/TEST/merge_done"

echo ""
echo "[4/6] Running saca.py..."
python3 /home/corr_mi/saca2.py "$DONE_DIR/$SIM_NAME"
python3 /home/corr_mi/bulk_calc.py

# ── 5. Upload post-processed sim to HPC ────────────────────────────────────
# set -e means: if this scp fails, the script stops right here and the
# heavy-file cleanup below (step 6) never runs — so nothing is deleted
# unless the upload actually succeeded.
echo ""
echo "[5/6] Uploading post-processed sim ${SIM_NAME} → ${HPC_HOST}:${HPC_SCRATCH}"
ssh "$HPC_HOST" "mkdir -p ${HPC_SCRATCH}"
scp -r "$DONE_DIR/$SIM_NAME" "${HPC_HOST}:${HPC_SCRATCH}"

# ── 6. Delete heavy local files (only reached if the upload succeeded) ────
echo ""
# echo "[6/6] Removing heavy local files from ${DONE_DIR}/${SIM_NAME}..."
#  for rel_path in "${HEAVY_FILES[@]}"; do
#      full_path="$DONE_DIR/$SIM_NAME/$rel_path"
#      if [ -f "$full_path" ]; then
#          rm -f "$full_path"
#          echo "  Deleted: $rel_path"
#      else
#          echo "  Already absent, skipping: $rel_path"
#      fi
done

echo ""
echo "======================================================"
echo " autoPost DONE: $SIM_NAME"
echo "======================================================"
