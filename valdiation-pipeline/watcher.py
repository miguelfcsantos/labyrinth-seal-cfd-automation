#!/usr/bin/env python3
"""
HPC Simulation Watcher
======================
Polls the HPC every N minutes, detects finished TRACE simulations,
downloads them one by one into a staging folder, then moves each to
the sims/ folder and calls autoPost.sh on it.

After a successful download the sim folder is moved on the HPC from
  sims/  →  sims_done/
so it will never appear in a future poll again (no flag file needed).
"""

import os
import subprocess
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

HPC_USER     = "corr_mi"
HPC_HOST     = "cara.dlr.de"
HPC_SIMS     = "/scratch/ws25/corr_mi-den_mig/sims"
HPC_SIMS_DONE = "/scratch/ws25/corr_mi-den_mig/sims_done"        # moved here after download

LOCAL_STAGING = "/localdata1/corr_mi/OI/TEST/staging"            # incomplete downloads land here
LOCAL_SIMS    = "/localdata1/corr_mi/OI/TEST/sims"               # merger.py / autoPost reads from here

AUTOPOST      = "/home/corr_mi/singleAutoPost.sh"                 # your post-processing script

POLL_INTERVAL = 300   # seconds between checks
MAX_PARALLEL  = 6     # how many sims to download simultaneously
FINISH_MARKER = "TRACE terminated normally"                       # string that signals a finished sim

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def ssh(command: str) -> str:
    result = subprocess.run(
        ["ssh", f"{HPC_USER}@{HPC_HOST}", command],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def get_finished_sims() -> list[str]:
    """
    Single SSH call: list every sim in HPC_SIMS and return only those
    whose out.log contains the finish marker.

    Sims that have already been moved to HPC_SIMS_DONE simply won't
    appear here — no flag file required.
    """
    batch_cmd = f"""
for sim in {HPC_SIMS}/*/; do
    [ -d "$sim" ] || continue
    name=$(basename "$sim")
    # FINISH_MARKER may be followed by 130+ MPI lines, so grep the whole log
    if grep -q "{FINISH_MARKER}" "$sim/input/out.log" 2>/dev/null; then
        echo "$name FINISHED"
    else
        echo "$name RUNNING"
    fi
done
"""
    output = ssh(batch_cmd)
    finished, running = [], []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            name, status = parts
            if status == "FINISHED":
                finished.append(name)
            else:
                running.append(name)

    total = len(finished) + len(running)
    log.info("Total in sims/: %d | Finished/pending: %d | Still running: %d",
             total, len(finished), len(running))
    if running:
        log.info("Still running: %s", running)

    return finished


def download_sim(sim_name: str) -> bool:
    """
    rsync the sim from HPC_SIMS into LOCAL_STAGING, then atomically
    rename it into LOCAL_SIMS.  Returns True on success.
    """
    staging_dest = os.path.join(LOCAL_STAGING, sim_name)
    final_dest   = os.path.join(LOCAL_SIMS, sim_name)

    # Clean up any previous failed staging attempt
    if os.path.exists(staging_dest):
        subprocess.run(["rm", "-rf", staging_dest])

    os.makedirs(LOCAL_STAGING, exist_ok=True)
    os.makedirs(LOCAL_SIMS, exist_ok=True)

    log.info("[%s] Downloading to staging...", sim_name)
    result = subprocess.run([
        "scp", "-r",
        f"{HPC_USER}@{HPC_HOST}:{HPC_SIMS}/{sim_name}",
        staging_dest,
    ])

    if result.returncode != 0:
        log.error("[%s] scp FAILED — leaving in staging, will retry next cycle", sim_name)
        return False

    log.info("[%s] Download complete. Moving staging → sims/", sim_name)
    # os.rename is atomic on the same filesystem — downstream tools never see a partial folder
    os.rename(staging_dest, final_dest)
    return True


def move_to_done_on_hpc(sim_name: str):
    """
    Move the sim folder from HPC_SIMS to HPC_SIMS_DONE so it never
    shows up in a future poll.  The destination directory is created
    if it doesn't exist yet.
    """
    cmd = (
        f"mkdir -p {HPC_SIMS_DONE} && "
        f"mv {HPC_SIMS}/{sim_name} {HPC_SIMS_DONE}/{sim_name}"
    )
    result = subprocess.run(
        ["ssh", f"{HPC_USER}@{HPC_HOST}", cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("[%s] Failed to move on HPC: %s", sim_name, result.stderr.strip())
    else:
        log.info("[%s] Moved to %s on HPC", sim_name, HPC_SIMS_DONE)


def run_postprocessing(sim_name: str):
    """Call autoPost.sh with the local sim folder path."""
    sim_path = os.path.join(LOCAL_SIMS, sim_name)
    log.info("[%s] Running autoPost.sh...", sim_name)
    result = subprocess.run(
        ["bash", AUTOPOST, sim_path],
        env={**os.environ, "SIM_DIR": sim_path},
    )
    if result.returncode != 0:
        log.error("[%s] autoPost.sh returned error code %d", sim_name, result.returncode)
    else:
        log.info("[%s] Post-processing done.", sim_name)


def handle_sim(sim_name: str):
    """Download one sim, move it on the HPC, then run post-processing."""
    if not download_sim(sim_name):
        return                          # scp failed; retry next poll, sim stays in HPC_SIMS
    move_to_done_on_hpc(sim_name)      # only reached on successful download
    run_postprocessing(sim_name)

# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def check_and_download():
    log.info("--- Polling HPC ---")

    finished = get_finished_sims()

    if not finished:
        log.info("Nothing new to download.")
        return

    log.info("Downloading %d sim(s) in parallel (max %d at a time)...", len(finished), MAX_PARALLEL)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {pool.submit(handle_sim, sim): sim for sim in finished}
        for future in as_completed(futures):
            sim = futures[future]
            try:
                future.result()
            except Exception as exc:
                log.error("[%s] Thread failed: %s", sim, exc)


def main():
    log.info("HPC Watcher started. Polling every %d seconds.", POLL_INTERVAL)
    log.info("HPC source:  %s@%s:%s", HPC_USER, HPC_HOST, HPC_SIMS)
    log.info("HPC done:    %s@%s:%s", HPC_USER, HPC_HOST, HPC_SIMS_DONE)
    log.info("Local:       %s  →  %s", LOCAL_STAGING, LOCAL_SIMS)

    while True:
        try:
            check_and_download()
        except Exception as exc:
            log.error("Unexpected error during poll: %s", exc)

        log.info("Sleeping %d seconds...\n", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
