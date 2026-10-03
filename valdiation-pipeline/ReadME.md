# Labyrinth Seal CFD Pipeline — Full Process Overview

This document walks through the entire pipeline, from generating a simulation case to producing the final comparison plots and performance numbers.



## 1. Build the case(s)

Two entry points exist, depending on whether you want one case or a parameter sweep.

### Single case (manual build script)
- Loads the required modules (trace, gmc, pymesh, tecplot).
- Opens `parameter.yml` / `geometry.yml` in `kate` for manual edits, then asks for a sim name.
- Reads `x_offset` out of the geometry file (used to decide which pre-built input-file variant to use).
- Runs `PyMesh.x main.py` to generate the mesh/geometry into a new case folder under `testGEO/<sim_name>`.
- Appends boundary-condition controller commands to `TRACE_control.input` (inlet velocity/pressure control targets with convergence residuals).
- Copies in the appropriate `TRACE_entry.input` variant and inlet-profile journal (this step is largely manual/commented — left as a checklist of options).
- Splits the mesh (`prep.py` + `gmcPlay splitScript.jou`) and runs a CGNS conversion journal.

### Parametric sweep (`run_study.sh`)
Automates the same base build, but generates many variants at once across up to three independent axes:

1. **Inlet angle** — patched into row 6, column 4 of `TRACE_entry.input`
2. **Turbulence preset** — coupled TU + length-scale pairs (T1/T2/T3), patched into every data row
3. **SST model combo** — stagnation-fix × rotational-effects, applied via a matching journal file

The user is prompted with y/n for each axis. The script:
- Builds **one shared base geometry** (mesh generation is expensive, so it's done once).
- Loops over every combination of the chosen axes, copying the base case into a uniquely-named variant folder (e.g. `mycase_A50_T2_SST_Sch_Ba`).
- Patches the relevant input file columns per variant using `awk`.
- Writes a `sim_config.yml` into each variant folder recording exactly what was varied.
- Uploads each variant and submits it (see step 2) automatically, in the same loop.

At the end it prints how many total simulations were generated (angle count × turbulence count × model-combo count).

---

## 2. Submit to the HPC cluster

Both build paths end the same way for each case:

```bash
scp -r <case_dir> cara.dlr.de:/scratch/.../sims
ssh cara.dlr.de "sbatch --chdir=<remote_case>/input tracestart_OG.sh"
```

The case folder (mesh, input files, `sim_config.yml`) is copied to HPC scratch space, and the TRACE solver job is submitted via `sbatch`. The cluster then runs the CFD solve independently; the pipeline picks back up once the solve has finished and produced output.

---

## 3. Post-process the finished simulation (`autoPost.sh`)

Once a solve is done, `autoPost.sh` turns raw solver output into analyzable data. It can run on a single sim folder or, with no argument, loop over every sim in a default directory (batch mode calls itself once per sim and reports a pass/fail summary).

Six steps per sim:

| Step | Script | What it does |
|---|---|---|
| 1 | `genRESIDUALS_single.py` | Builds a Tecplot macro and runs it headless to generate residual/mass-flow/eddy-ratio/ratio diagnostic PNGs |
| 2 | `griddd_single.py` | Computes a structured (X, R) grid for the seal geometry from `parameters_used.yml` / `geometry_used.yml`, writes `grid.dat` |
| 3 | `merger_single.py` | Merges raw CGNS output (`gmcPlay`), then runs the `POST` tool (via `mpirun`) using the grid from step 2 plus predefined cut/band definitions; moves the finished case to a "done" folder |
| 4 | `saca2.py` (saca) | Reads the spanwise flux data POST produced, extracts normalized axial/tangential velocity (`C_ax`, `C_tan`) at each axial cut, and sorts results into `conv`/`div` folders based on the seal's geometry (converging vs. diverging step) |
| 5 | — | Uploads the finished, post-processed sim folder back to HPC scratch via `scp` |
| 6 | — | Deletes heavy local CGNS files to save disk space — **currently disabled/commented out**, and its leftover `done` keyword will cause a syntax error if the block isn't fixed |

`set -e` means any failed step stops that sim immediately, and cleanup (step 6) is only ever reached after a successful upload.

---

## 4. Analyze and plot

Two independent scripts consume the post-processed data:

### `bulk_calc.py` — performance coefficients
For every finished sim, reads the converged (last-row) inlet/outlet residual values and computes standard labyrinth-seal metrics:

- **π** — pressure ratio
- **CD** — discharge coefficient (actual vs. ideal mass flow)
- **σ** — normalized temperature-rise coefficient
- **Kz / Ki** — outlet/inlet swirl coefficients
- **ΔTt** — stagnation temperature rise

Results are written to `calculated_values.yml` in each sim's folder and to a shared `VALUES/` directory, plus printed as a summary table. Sims missing required files are skipped with a warning rather than aborting the batch.

### `plot_velocity_profiles.py` — velocity profile plots
Runs after `saca2.py` and auto-discovers every sim and axial location from the folder structure `saca` wrote. For each sim/location/velocity-component combination:

- Spline-interpolates the sim's velocity profile
- Loads matching experimental and reference-CFD comparison curves (borrowing from the X=30 mm location for locations closer to the seal inlet, which have no experimental data of their own)
- Plots relative channel height vs. normalized velocity, saving a PNG per sim/location

Existing plots are skipped unless `--force` is passed, so re-runs are cheap.

---

## End-to-end summary

1. **Build** — one case manually, or a whole sweep automatically (`run_study.sh`) varying inlet angle, turbulence, and/or SST model settings.
2. **Submit** — every case is uploaded and `sbatch`'d to the `cara` HPC cluster.
3. **Post-process** (`autoPost.sh`, single sim or batch) — generate residual diagnostics, build the analysis grid, merge/post-process CGNS output, and extract normalized velocity profiles.
4. **Analyze & plot** — compute discharge/swirl/temperature-rise coefficients (`bulk_calc.py`) and generate velocity-profile comparison plots against experimental data (`plot_velocity_profiles.py`).

The whole chain turns a set of YAML parameters into submitted CFD jobs, and turns finished CFD jobs into thesis-ready numbers and figures — with the sweep launcher letting many parameter combinations run through the exact same pipeline unattended.
