# Labyrinth Seal CFD Parametric Study Pipeline

This document explains how the four scripts fit together into one pipeline for running, finalizing, and post-processing parametric CFD sweeps of the staggered-labyrinth-seal case on the `cara` HPC cluster.

The pipeline has four stages, run roughly in this order:

1. **`paramstudy.sh`** — builds and submits sweep cases (Bash)
2. **`cara_bc_finalize.sh`** — polls for converged boundary-controller cases and submits the "real" simulations (Bash)
3. **HPC Value Extractor** — pulls finished simulations, computes derived quantities, archives them (Python)
4. **HPC Convergence Checker** — checks solver/flux convergence of archived simulations and flags problem cases (Python)

---

## 1. `paramstudy.sh` — Sweep Driver

**Purpose:** reads a single YAML "plan file" and turns it into a batch of meshed, submitted CFD cases. Nothing is asked interactively (aside from the two legacy special sweeps) — everything comes from the plan file.

### Inputs it reads

- `geometry.yml` / `parameter.yml` — the **immutable baseline**. Every case gets its own `geometry_used.yml` / `parameters_used.yml` copy; only those copies are ever edited.
- `SWEEP_plan.yml` — the plan file, containing:
  - `outer:` (optional) — one variable held fixed across passes, with a list of values
  - `inner:` (always present) — a list of OFAT (one-factor-at-a-time) sweeps, each defined as a percent sweep, a start/end interval, or a percent sweep with extra "outer band" points
  - `K_in:` — a single constant swirl-coefficient target (not a list), used to compute the boundary-condition target velocity for every case. Ignored entirely if the outer variable is swirl.

### The variable registry

A set of associative arrays (`VAR_KEY`, `VAR_FMT`, `VAR_LABEL`, `VAR_MODE`) map a human-readable variable name (e.g. `clearance`, `pressure_ratio`, `swirl`) to:

- the actual YAML key it writes (`clearence`, `total_pressure`, `velocity_angle_alpha`, …)
- the number format used for naming/writing
- the short label used in case names (`CLR`, `PR`, `K`, …)
- its sweep mode: `direct` (plain ± % sweep), `derived_pr` (value is a pressure ratio, written as `PR × outlet pressure`), `interval` (explicit start/end, no baseline percent), or `magnitude` (only used for `step_height`, sweeps the uniform magnitude of the baseline list while preserving signs)

### Two ways a sweep can be structured

- **No `outer:` block** → sweep runs flat. Every case folder/prefix is keyed by `K_in` (e.g. `K7/K7_PR_150_BC`).
- **`outer:` present** → its values are held fixed one at a time, and every inner sweep runs underneath each one. Folder/prefix is keyed by the outer variable instead (e.g. `PR150/PR150_CLR_213_BC`), and `K_in` never appears in the name — it's only used internally.
  - **Special case: outer variable is swirl.** Then `K_in` is ignored completely and no boundary controller is used — the swirl values are written straight into `velocity_angle_alpha`, and cases are submitted directly (no `_BC` suffix).

Note: the outer variable's *precision* always comes from the registry (`VAR_FMT`), not from the plan file's own `outer: fmt:` field — that field is parsed but deliberately ignored. Deferring to a plan-supplied format previously rounded very small values (e.g. clearance ≈ 2×10⁻⁵) down to `0.0000`, silently writing zero into the case.

### Case naming

`<folder-prefix><LABEL>_<value>[_BC]`, where the value is always a "compact" token — the actual value with the decimal point and leading zeros stripped (e.g. `1.25 → "125"`, `0.000233 → "233"`), never a percentage.

### Boundary-controller (BC) workflow

Most cases are submitted as `_BC` cases first: a `SetBoundaryConditionController` block is appended to the TRACE control file, which scales the inlet swirl angle until the mass-averaged inlet `VelocityTheta` hits the target `K_in × ω × R`. This target is computed per-case from that case's own geometry/rpm. `cara_bc_finalize.sh` (stage 2) later reads the converged angle and submits the real, fixed-angle case.

### Per-case pipeline (`run_case`)

For each case: `PyMesh.x` meshes the case from the per-case geometry/parameter files → (if it's a BC case) the controller block is injected into `TRACE_control.input` → `prep.py` splits the mesh → `gmcPlay` runs a sequence of GMC scripts. If meshing fails (no `input/` folder produced), the case is skipped and recorded as failed.

### Shipping and submission

`ship_and_submit` scp's the finished case folder to the HPC, then submits it via `sbatch`. On scp failure the local copy is deliberately kept (so nothing is lost); on success the local copy is deleted. Successes and failures are tracked in `CASE_NAMES` / `FAILED_CASES` and summarized at the end.

### Legacy special sweeps

`number_of_fins` and `step_height_profile`, named as the outer variable, trigger their own interactive prompts instead of the registry/BC machinery — they build entirely new fin-count/step-height geometries from scratch and submit them directly (no `K_in`, no BC controller).

---

## 2. `cara_bc_finalize.sh` — BC Case Finalizer

**Purpose:** polls the HPC for `_BC` cases that are converging on an inlet swirl angle, and once one converges, builds and submits the corresponding "real" (fixed-angle) case.

### Discovery

One SSH call does a recursive `find` under `REMOTE_SIMS_BASE` for any `*_BC` directory, wherever it actually sits. Each case's **actual** discovered path is kept and reused for every later read — nothing is reconstructed by guessing a path from the case name.

### Convergence criterion

For each case, the script reads `bcControl_v.dat` and looks at the angle column. It's converged once the angle, **truncated to 3 decimal places** (not rounded, and not just the isolated 3rd digit — the whole truncated value), is identical across 3 consecutive rows. The reported angle is that row's value truncated to 2 decimal places. Truncation is done via string formatting rather than `floor()`/`round()` to avoid floating-point noise flipping a digit that should be exact.

### Finalizing a converged case

1. Pull down that case's `geometry_used.yml` / `parameters_used.yml` as-is.
2. Write the converged angle into `velocity_angle_alpha` (fixed, no controller).
3. Copy the full convergence history (`bcControl_v_converged.dat`) into the new case folder as a record.
4. Mesh + prep + submit the real case (same `run_case` logic as stage 1, minus the controller-injection branch).
5. Best-effort cancel the (likely still-running) `_BC` SLURM job by matching its working directory in `squeue`, then archive the `_BC` folder into a `..._bc_done` tree, grouped the same way as the real case.

Grouping for both the real-case output and the archived `_BC` folder is derived from the case name itself: everything before the first underscore (e.g. `K6` from `K6_RPM_180258_BC`).

### Idempotency

A local ledger file (`.bc_finalized_cases`) records every real case that has been finalized. A case is marked finalized **immediately after submission succeeds** — before the archive/cleanup step even runs — so a failed archive `mv` can never cause a case to be rebuilt and resubmitted on a later poll just because its `_BC` folder is still sitting there looking "converged."

### Loop

Runs forever, polling every `POLL_INTERVAL_SECONDS` (default 300s), or once with `--once`.

---

## 3. HPC Value Extractor (Python)

**Purpose:** runs once, top to bottom (no loop), and for every **finished, non-BC** simulation on the HPC: reads its key physical quantities, computes derived non-dimensional numbers, writes a results file locally, and archives the simulation's remote folder out of the "active" tree.

### Finding finished sims

One SSH call lists every simulation under `HPC_SIMS/*/*/ ` (any family prefix: K, PR, FTH, FAN, SH, FH, RF, DF, …) and checks each one's `input/out.log` for the finish marker `"TRACE terminated normally"`. Sims ending in `_BC` are filtered out entirely — they're never valued, counted as running, or touched. If nothing is found, it dumps the raw SSH output for debugging.

### Reading each sim

One SSH call per sim reads (`cat`s, no download) 4 files directly: `geometry_used.yml`, `parameters_used.yml`, and the inlet/outlet flux residual files. A trailing `echo` after every `cat` guarantees the next section's marker always starts on a fresh line, even if a file's last line has no trailing newline — otherwise the marker could get glued onto the previous line's content and be silently swallowed instead of recognized as a section boundary.

### Computed quantities

From inlet/outlet stagnation pressures/temperatures, mass flow, and swirl velocities, it computes (per averaging type, currently just `flux`):

- Ideal mass-flow coefficient and discharge coefficient (`Q`, `CD`)
- Non-dimensional temperature rise (`σ`)
- Outlet and inlet swirl coefficients (`Kz`, `Ki`)
- Inlet/outlet Mach numbers
- Axial Reynolds number, using Sutherland's law for viscosity at inlet stagnation temperature

### Output

- Results are written locally to `VALUES_DIR/<prefix>/<sim_name>.yml`, where `<prefix>` is everything before the first underscore in the sim name.
- The simulation's remote folder is then moved on the HPC into `sims_valued/<prefix>/<sim_name>` — grouped the same way as the local output.
- Sims that fail to process (e.g. missing files) are skipped and reported at the end without stopping the run.

---

## 4. HPC Convergence Checker (Python)

**Purpose:** read-only. Checks every simulation already archived in `sims_valued` for solver and flux convergence, and writes a report + plots for anything that looks off. It never moves, modifies, or deletes simulation files.

### Per-sim checks

1. **Log check** — tail of `input/out.log` is scanned for the finish marker and for error patterns (`nan`, `segmentation fault`, `diverg`, `fatal`, `exceeded wall`, etc.). Unfinished sims with error patterns are flagged as `ERROR`; unfinished sims without errors are `RUNNING`.
2. **Residual convergence** — `residual.dat` (subsampled on the remote side with `awk` if it's huge, always keeping the header, every Kth row, and the last 500 rows) is parsed, and each primary/turbulence residual is checked against three criteria: absolute threshold, required drop in orders of magnitude from its first finite value, and a "plateau" check (the spread of `log10(residual)` over the last chunk of iterations must be tight). A residual passes if it's below its absolute threshold **or** both dropped enough and plateaued. `CFLNumberMin/Max` and `PerformanceIndex` are recorded but never affect the pass/fail decision.
3. **Flux stability** — the tail of the inlet/outlet flux files is checked per physical quantity: relative standard deviation (std normalized by the window's amplitude, not by a possibly-near-zero mean) must be under a tolerance, unless the whole window is already at floating-point noise level.

A sim is `CONVERGED` only if **all** residuals converge **and** all flux quantities are stable; otherwise `NOT_CONVERGED`.

### Output

- `NOT_CONVERGED` sims get a folder under `FLAGGED_DIR/<sim>/` with three convergence plots (L1/L2, Lmax, turbulence residuals) and a text report listing every criterion's pass/fail with numbers.
- `ERROR` sims get the flagged out.log lines saved, with no plots.
- A single `summary.txt` lists every checked sim's final status.

---

## How the four scripts connect

```
SWEEP_plan.yml
      │
      ▼
paramstudy.sh  ──────────────► submits "_BC" cases (or direct cases if outer=swirl)
      │
      ▼ (poll loop)
cara_bc_finalize.sh  ─────────► finds converged "_BC" cases, submits the real cases,
                                 archives the "_BC" folders
      │
      ▼ (run once, after real cases finish)
HPC Value Extractor  ─────────► reads finished (non-BC) sims, computes derived
                                 quantities, writes VALUES/*, archives to sims_valued/*
      │
      ▼ (read-only, any time)
HPC Convergence Checker  ─────► checks sims_valued/* for solver/flux convergence,
                                 flags anything questionable
```

The two Bash scripts share the same case-naming and grouping conventions (prefix before the first underscore), so the Python scripts can group results the same way without needing to know which family (K, PR, FTH, …) a given case belongs to in advance.
