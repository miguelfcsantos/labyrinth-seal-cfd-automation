# labyrinth-seal-cfd-automation

Automated CFD workflows for high-fidelity labyrinth seal analysis with the TRACE solver on an HPC cluster. The repository covers the full chain from a set of YAML inputs to submitted simulations, post-processed results, verification studies and publication-ready figures:

- validation case generation and post-processing
- geometry and parameter configuration
- HPC job submission and monitoring
- automated post-processing and convergence assessment
- a modular framework for parametric studies across operating conditions and seal geometries

The work focuses on a staggered labyrinth seal (Denecke cavity) with and without prescribed inlet swirl, and on how the leakage and swirl behaviour change with geometry and operating conditions.

---

## Repository overview

| Folder | What it is | Typical scale |
|---|---|---|
| [`valdiation-pipeline/`](valdiation-pipeline) | **Validation pipeline.** Builds a single case or a small sweep, submits it to the HPC, then post-processes the results **on the local workstation** (not on the cluster) and compares them against experimental data and reference CFD. | A few carefully chosen simulations |
| [`NASA_EEE_pipeline/`](NASA_EEE_pipeline) | **Parametric study pipeline.** A lighter-weight, plan-driven framework for running very large numbers of simulations: sweep driver, boundary-controller finalizer, value extractor and convergence checker, all working directly on the HPC. | Hundreds of simulations |
| [`gci-analysis/`](gci-analysis) | **Mesh convergence.** Generates and submits a mesh-refinement study and evaluates the Grid Convergence Index (Celik et al., 2008). | 6 mesh families, up to 5 levels each |
| [`stodola-verification/`](stodola-verification) | **1D scaling-law verification.** Checks the Stodola fin-count scaling relation against CFD mass flows for five seal configurations. | Fin-count series per configuration |
| [`YAML_files/`](YAML_files) | **Example inputs.** Reference `geometry`, `parameter` and sweep-plan files. | n/a |

Each folder has its own README with details; this page explains how they fit together.

---

## How the pieces fit together

```
                         YAML_files/
              (geometry.yml, parameter.yml, SWEEP_plan.yml ...)
                                │
          ┌─────────────────────┼──────────────────────────┐
          ▼                     ▼                          ▼
 valdiation-pipeline/   NASA_EEE_pipeline/     gci-analysis/, stodola-verification/
 build → submit →       plan → submit (BC) →   (their own launchers)
 local post-process     finalize → extract →            │
          │             convergence check               │
          ▼                     ▼                       ▼
                      values_*.yml  (common result format)
                                │
                                ▼
       plots · GCI tables · scaling-law reports · convergence reports
```

The folders share three conventions that make them interoperable:

1. **Everything starts from YAML.** A case is described by a `geometry` file and a `parameter` file. Each generated case keeps its own `*_used.yml` copies, so the baseline files are never edited and every case records exactly what was run.
2. **Results are exchanged as `values_*.yml` files.** Post-processing produces one YAML per simulation with the performance coefficients, split by averaging type (`flux`, `mass`, `area`). The GCI and Stodola analyses read these files rather than raw solver output.
3. **Consistent quantities.** Results are reported as discharge coefficient `CD`, temperature-rise coefficient `sigma`, outlet/inlet swirl coefficients `Kz` / `Ki`, and (where relevant) mass flow, Mach and Reynolds numbers.

---

## The two pipelines in more detail

The validation and parametric pipelines solve different problems and are deliberately built differently.

### Validation pipeline: few simulations, deep post-processing

Used to find out which settings give the best agreement with experiment, so it favours depth over volume.

- Builds one case interactively, or a small parameter sweep over inlet angle, turbulence preset and SST model variant.
- Post-processing runs on the **local workstation** instead of on the cluster: residual diagnostics, structured grid generation, CGNS merging and the TRACE `POST` tool, and extraction of normalised axial and tangential velocity profiles at each axial cut.
- Heavy data manipulation is the point: `bulk_calc.py` computes the performance coefficients, and `plot_velocity_profiles.py` overplots simulation profiles against experimental data and reference CFD in one go.
- Its single-simulation post-processing (`singleAutoPost.sh`) is also what produces the `values_*.yml` files used by the GCI and Stodola studies.

### Parametric study pipeline: many simulations, lightweight per case

Used to cover a wide design space, so it favours automation and throughput.

- A single **`SWEEP_plan.yml`** describes the whole study: an optional outer variable held fixed across passes, plus a list of one-factor-at-a-time inner sweeps (percent sweeps, explicit intervals, or percent sweeps with extra outer-band points).
- **Two simulations per case.** To impose a target inlet swirl coefficient, each case first runs as a boundary-controller (`_BC`) simulation that adjusts the inlet swirl angle until the target is met. Once the angle has converged, the **real, fixed-angle simulation** is built and submitted. Cases with swirl as the outer variable skip the controller and are submitted directly.
- **More work on the HPC side.** The sweep driver, the BC finalizer (which polls the cluster and submits the real case once the angle converges), the value extractor and the convergence checker all operate remotely, and finished cases are archived on the cluster.
- A read-only **convergence checker** flags cases with non-converged residuals or unstable fluxes, with plots and a text report per flagged case.

| | Validation pipeline | Parametric study pipeline |
|---|---|---|
| Number of simulations | Few | Many |
| Input | Interactive prompts or a small sweep | `SWEEP_plan.yml` |
| Boundary controller | Not used | Two-stage (`_BC` then real case) |
| Post-processing location | Local workstation | Mostly on the HPC |
| Main outputs | Velocity profiles, coefficients, comparison plots | `values` files, convergence reports |

---

## Verification studies

### Grid convergence (`gci-analysis/`)
Mesh matrix of six families (AA, AB, BA, BB, CA, CB) with up to five refinement levels, from about 0.18 to 20.8 million cells. `run_study.sh` generates and submits all meshes in two SST variants; `gci_batch.py` computes the GCI for every row, mesh triplet and averaging type, with asymptotic-range checks and plots.

### Stodola scaling law (`stodola-verification/`)
Tests the relation `m_dot(z2) / m_dot(z1) = sqrt(z1 / z2)` on CFD mass flows for five configurations (smooth, convergent, convergent half-step, divergent, divergent half-step), with and without inlet swirl. Produces error heat maps, mass-flow trend plots and a summary chart. The result is a measure of how well the scaling approximation holds for these geometries, not a general validation of the equation.

---

## Typical workflow

1. **Configure.** Start from the examples in [`YAML_files/`](YAML_files) and adapt geometry, parameters and (for sweeps) the sweep plan.
2. **Build and submit.** Use the launcher of the relevant folder. Meshing happens locally, cases are copied to the cluster scratch space and submitted with `sbatch`.
3. **Wait for the solves.** For plan-driven studies, run the BC finalizer so converged `_BC` cases are turned into real simulations automatically.
4. **Post-process.** Locally with the validation pipeline, or on the cluster with the value extractor.
5. **Check convergence.** Run the convergence checker, and the GCI study where mesh independence matters.
6. **Analyse.** Velocity-profile comparisons, GCI tables, scaling-law reports and sweep plots.

---

## Requirements

- **Solver and tools:** TRACE and its suite (`prep.py`, `gmcPlay`, `POST`), `PyMesh` with the geometry generator, Tecplot (headless, for residual plots).
- **Environment:** a Linux workstation with environment modules, passwordless `ssh`/`scp` to the HPC, and a SLURM cluster.
- **Python 3** with at least `numpy` and `matplotlib` (each folder's README lists its own additional requirements).

Paths, host names and module names are defined at the top of each script and need to be adapted to your environment.

---

## Notes and limitations

- Scripts were developed for a specific workstation and cluster setup. Expect to adjust paths, modules and the scheduler script before running them elsewhere.
- Several analyses depend on values typed in by hand (for example the mesh cell counts in the GCI script). Check these whenever a case definition changes.
- CFD post-processing is intentionally kept separate from the verification scripts: the GCI and Stodola studies assume the `values_*.yml` files already exist.

---

## References

- Celik, I. B., Ghia, U., Roache, P. J., Freitas, C. J., Coleman, H., Raad, P. E. (2008). *Procedure for Estimation and Reporting of Uncertainty Due to Discretization in CFD Applications.* Journal of Fluids Engineering, 130(7), 078001.

## Citation and acknowledgements

[TODO: thesis or paper reference, institution, supervisors, and data sources for the experimental and reference CFD data.]

## License

[TODO: add a license.]
