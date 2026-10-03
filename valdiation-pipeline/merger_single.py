#!/usr/bin/env python3
"""
Post-processing pipeline for a SINGLE TRACE CFD simulation.

Usage:
    python3 merger_single.py /localdata1/corr_mi/OI/sims/LC

Steps:
  1. Prepares mergeScript.jou in the case's output/cgns folder
  2. Runs gmcPlay to merge and post-process CGNS files
  3. Runs POST via mpirun
  4. Moves the finished case to DONE_DIR
"""

import os
import sys
import shutil
import subprocess
import logging

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DONE_DIR  = "/localdata1/corr_mi/OI/TEST/merge_done"
POST_GMC  = "/home/corr_mi/gmcReadyForPost.jou"
LMOD_CMD  = "/usr/share/lmod/lmod/libexec/lmod"

MODULES_GMC   = ["gmc/9.6.13"]
MODULES_TRACE = ["trace_suite/9.8.0-double"]

POST_NP       = 24
POST_CGNS_IN  = "TRACE_merged_post.cgns"
POST_CGNS_OUT = "../post/POSTq.cgns"
CUTS_XI       = "/localdata1/corr_mi/post_files/cutsXi.dat"
BANDS         = "/localdata1/corr_mi/post_files/bands.dat"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lmod helper
# ---------------------------------------------------------------------------
def lmod(action, *modules):
    result = subprocess.run(
        [LMOD_CMD, "python", action] + list(modules),
        capture_output=True,
        text=True,
        check=True,
    )
    env_updates = {}
    exec(result.stdout, {"os": os}, env_updates)
    for key, val in env_updates.items():
        if isinstance(val, str):
            os.environ[key] = val


def load_modules(*modules):
    lmod("purge", "--force")
    lmod("load", *modules)
    log.info("Loaded modules: %s", ", ".join(modules))


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------
def run(cmd, cwd=None):
    log.info("Running: %s  (cwd=%s)", " ".join(str(c) for c in cmd), cwd or ".")
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=os.environ, check=True)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------
def process_case(case_dir):
    case_dir = os.path.abspath(case_dir)

    input_file = os.path.join(case_dir, "input", "mergeScript.jou")
    if not os.path.isfile(input_file):
        log.warning("No mergeScript.jou found in %s — skipping", case_dir)
        return

    log.info("=== Processing: %s ===", os.path.basename(case_dir))

    output_dir  = os.path.join(case_dir, "output", "cgns")
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, "mergeScript.jou")
    shutil.copy(input_file, output_file)
    shutil.copy(POST_GMC, output_dir)

    with open(output_file, "r") as f:
        lines = f.read().splitlines()
    lines[0]  = "gmc -> open 'TRACE.cgns'"
    lines[-1] = "gmc -> save 'TRACE_merged.cgns'"
    with open(output_file, "w") as f:
        f.write("\n".join(lines) + "\n")

    load_modules(*MODULES_GMC)
    run(["gmcPlay", "mergeScript.jou"], cwd=output_dir)
    run(["gmcPlay", os.path.basename(POST_GMC)], cwd=output_dir)

    load_modules(*MODULES_TRACE)
    run([
        "mpirun", "-np", str(POST_NP),
        "POST",
        "-i",   POST_CGNS_IN,
        "-rbc", "-cbi", "-iP",
        "-gf",  os.path.join(case_dir, "grid.dat"),
        "-cf",  CUTS_XI,
        "-bf",  BANDS,
        "-avg", "-xgta",
        "-o",   POST_CGNS_OUT,
    ], cwd=output_dir)

    os.makedirs(DONE_DIR, exist_ok=True)
    dest = os.path.join(DONE_DIR, os.path.basename(case_dir))
    shutil.move(case_dir, dest)
    log.info("Moved: %s -> %s", os.path.basename(case_dir), DONE_DIR)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <sim_folder_path>")
        print(f"Example: python3 {sys.argv[0]} /localdata1/corr_mi/OI/sims/LC")
        sys.exit(1)

    case_dir = sys.argv[1]

    if not os.path.isdir(case_dir):
        log.error("Folder not found: %s", case_dir)
        sys.exit(1)

    try:
        process_case(case_dir)
    except Exception as exc:
        log.error("FAILED: %s — %s", os.path.basename(case_dir), exc)
        sys.exit(1)

    log.info("Done.")


if __name__ == "__main__":
    main()
