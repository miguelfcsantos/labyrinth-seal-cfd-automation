# CFD Grid Convergence Index (GCI) Workflow

Scripts to generate, run and evaluate a mesh-refinement study for a staggered labyrinth seal (Denecke cavity) simulated with TRACE. The study follows the **Grid Convergence Index** method of Celik et al. (2008), *ASME J. Fluids Eng.* 130(7):078001.

This part of the repository contains two scripts:

| Script | Purpose |
|---|---|
| `run_study.sh` | Generates the meshes for every grid factor, applies the SST model variants, uploads the cases to the HPC and submits the jobs |
| `gci_batch.py` | Reads the post-processed results and computes the GCI for every row, triplet and averaging type |

---

## Workflow overview

```
run_study.sh
   │  generate geometry + mesh, apply SST journal, upload, sbatch
   ▼
TRACE simulations on the HPC
   ▼
single auto post          (see the `validation/` directory of this repository)
   │  writes values_{PREFIX}{ROW}{MESH}.yml
   ▼
VALUES/ directory
   ▼
gci_batch.py
   │  GCI tables, asymptotic-check plots, YAML results
   ▼
VALUES/RESULTS/
```

### Post-processing is not part of this folder

The TRACE results are **not** converted to `values_*.yml` by any script in this folder. That step is done by the **single auto post** pipeline, documented in the [`validation/`](../validation) directory of this repository. Running it for each finished simulation writes the `values_*.yml` files into the `VALUES` directory, and `gci_batch.py` reads them from there.

So the order is: launch the simulations, wait for them to finish, run single auto post on each, then run the GCI script.

---

## Mesh matrix

Six mesh families (rows) with up to five refinement levels each. `M1` is the finest level of a row and `M5` the coarsest. The grid factor is the value patched into `parameter.yml` by `run_study.sh`.

| Row | M1 | M2 | M3 | M4 | M5 |
|---|---|---|---|---|---|
| **AA** | 10,827,120 (GF 1.0) | 4,934,700 (0.62) | 2,257,920 (0.405) | 1,015,740 (0.265) | none |
| **AB** | 16,690,560 (1.214) | 7,618,800 (0.826) | 3,439,020 (0.515) | 1,576,980 (0.337) | 721,200 (0.22) |
| **BA** | 10,827,120 (1.0) | 3,955,620 (0.552) | 1,433,640 (0.315) | 520,860 (0.186) | none |
| **BB** | 20,790,840 (1.37) | 7,618,800 (0.826) | 2,790,840 (0.46) | 1,015,740 (0.265) | 360,000 (0.155) |
| **CA** | 10,827,120 (1.0) | 3,230,220 (0.493) | 947,280 (0.253) | 279,300 (0.134) | none |
| **CB** | 7,618,800 (0.826) | 2,257,920 (0.405) | 665,640 (0.21) | 188,400 (0.114) | none |

Each simulation name is `{ROW}{MESH}`, for example `AB3` is row AB, mesh level 3.

### Triplets

A GCI needs three meshes. Triplets are written `{coarse}{medium}{fine}` using mesh indices:

| Triplet | Coarse | Medium | Fine | Available for |
|---|---|---|---|---|
| `321` | M3 | M2 | M1 | all rows |
| `432` | M4 | M3 | M2 | all rows |
| `543` | M5 | M4 | M3 | AB and BB only (they have an M5 mesh) |

Within a triplet, the "fine" mesh is the lowest index of that triplet, so for `432` the fine mesh is M2, not M1. Triplets that need a mesh that does not exist for a row are skipped silently.

---

## Naming convention

`run_study.sh` produces two variants of every mesh, which differ in the stagnation fix:

| Variant directory | Stagnation fix | Rotational effects |
|---|---|---|
| `{ROW}{MESH}_Sch_Ba` | Schwarz | Bardina |
| `{ROW}{MESH}_Off_Ba` | Off | Bardina |

The GCI script identifies a case family by a **prefix**, used to build the input filename `values_{PREFIX}{ROW}{MESH}.yml`:

| Prefix | Example input file | Meaning |
|---|---|---|
| `wLO` | `values_wLOCB2.yml` | **TODO: describe what `wLO` stands for and which variant it maps to** |
| `w` | `values_wCB2.yml` | **TODO: describe what `w` stands for and which variant it maps to** |

The mapping from the launcher variants (`Sch_Ba`, `Off_Ba`) to these prefixes happens when the results are post-processed, not in the GCI script.

---

## Requirements

**Mesh generation and submission (`run_study.sh`)**, run on the local workstation:

- Environment modules: `trace_dependencies`, `gmc`, `pymesh`, `tecplot`, `trace_suite`
- `PyMesh.x` with the geometry generator `main.py` (the `lab_altered_py` directory)
- `prep.py` and `gmcPlay` from the TRACE suite
- Passwordless `ssh`/`scp` access to the HPC host

**Inputs the launcher expects:**

- `parameter.yml` and `geometry.yml` (Denecke cavity example)
- `OmegaSST_Schwarz_Bardina.jou` and `OmegaSST_Off_Bardina.jou` in the journal directory
- The sbatch script (`tracestart_OG.sh`) on the HPC

**GCI evaluation (`gci_batch.py`):**

- Python 3
- `numpy`, `matplotlib`

```bash
pip install numpy matplotlib
```

---

## Usage

### 1. Configure paths

Edit the path block at the top of `run_study.sh`:

| Variable | Description |
|---|---|
| `BASE_DIR` | Local directory where base geometries are generated (`testGEO`) |
| `JOU_DIR` | Directory with the `OmegaSST_*.jou` journals |
| `HPC_SCRATCH` | Target directory on the HPC |
| `HPC_HOST` | HPC hostname |
| `SBATCH_SCRIPT` | Path to the sbatch script on the HPC |
| `PARAM_YML`, `GEO_YML` | Parameter and geometry files |

The list of meshes is the `GF_ENTRIES` array (`"NAME GRID_FACTOR"`), and the model variants are in `SST_COMBOS`.

### 2. Generate and submit

```bash
bash run_study.sh
```

For each entry in `GF_ENTRIES` the script:

1. Patches `grid_factor` in `parameter.yml`
2. Generates the base geometry in `BASE_DIR/{sim_name}` and splits it for 128 processes (the base is kept locally and is not uploaded)
3. For each SST combo: copies the base, applies the journal, writes `sim_config.yml`, uploads the variant, submits the sbatch job and deletes the local variant copy

With the default settings this submits 2 jobs per mesh, 52 in total.

### 3. Post-process

When the simulations finish, run the **single auto post** pipeline from the [`validation/`](../validation) directory. It writes one `values_{PREFIX}{ROW}{MESH}.yml` per simulation into the `VALUES` directory.

Each values file must contain one block per averaging type (`flux`, `mass`, `area`), each with the quantities `CD`, `sigma` and `Kz`:

```yaml
avg_type: flux
CD: 0.0
sigma: 0.0
Kz: 0.0
```

(The numbers above are placeholders.)

### 4. Run the GCI

Set `VALUES_DIR` and `OUTPUT_DIR` at the top of `gci_batch.py`, then:

```bash
python gci_batch.py
```

The script loops over every prefix, row and triplet, and runs the GCI for each averaging type.

---

## Configuration of the GCI script

| Setting | Default | Description |
|---|---|---|
| `VALUES_DIR` | `/localdata1/corr_mi/VALUES` | Where the `values_*.yml` files are read from |
| `OUTPUT_DIR` | `VALUES/RESULTS` | Where results are written |
| `QUANTITIES` | `CD`, `sigma`, `Kz` | Quantities evaluated |
| `AVG_TYPES` | `flux`, `mass`, `area` | Averaging types evaluated |
| `SAFETY_FACTOR` | `1.25` | Safety factor for three-mesh studies |
| `P_SOLVER_TOL` | `1e-6` | Tolerance of the apparent-order iteration |
| `P_SOLVER_MAX_ITER` | `1000` | Maximum iterations of the apparent-order solver |
| `CELL_COUNTS` | see mesh matrix | Cell count of every mesh |
| `TRIPLETS` | `321`, `432`, `543` | Mesh triplets evaluated |
| `PREFIXES` | `wLO`, `w` | Case families evaluated |

> **Important:** `CELL_COUNTS` is typed in by hand and is used to compute the refinement ratios. It must match the meshes that were actually generated. Nothing in the script verifies this, so update it whenever a grid factor changes.

---

## Method

For a triplet with solutions f1 (fine), f2 (medium) and f3 (coarse):

- Refinement ratios from the 3D cell counts: `r = (N_fine / N_coarse)^(1/3)`, giving `r21` and `r32`
- Differences `eps21 = f2 - f1` and `eps32 = f3 - f2`
- Apparent order `p` by fixed-point iteration, following Celik et al. (2008)
- Extrapolated value `f_extrap = f1 + (f1 - f2) / (r21^p - 1)`
- `GCI_fine = 1.25 * |eps21| / (r21^p - 1) / |f1| * 100`, and `GCI_medium` analogously with `eps32`, `r32` and `f2`
- Asymptotic range check `GCI_medium / (r21^p * GCI_fine)`, which should be close to 1

The solver status is reported for every quantity: `converged`, `oscillatory` (GCI not reliable), `degenerate`, `singular` or `max_iter_reached`.

---

## Output

```
RESULTS/
├── failed_simulations.yml
├── wLOAA/
│   ├── 321/
│   │   ├── gci_table_flux.txt
│   │   ├── gci_asymptotic_flux.png
│   │   ├── gci_results_flux.yml
│   │   └── ... (same three files for mass and area)
│   ├── 432/
│   └── 543/
└── wAB/
    └── 321/
```

| File | Content |
|---|---|
| `gci_table_{avg_type}.txt` | Summary table with f1, f2, f3, `p`, GCI values, asymptotic check and solver status |
| `gci_asymptotic_{avg_type}.png` | Asymptotic check per quantity, with the ideal value 1.0 and a ±5 % band |
| `gci_results_{avg_type}.yml` | All computed values as flat `key: value` lines |
| `failed_simulations.yml` | Missing input files, parse errors and failed runs |

---

## Known limitations

- Cell counts are hardcoded (see above).
- The values-file parser is line based, not a YAML parser. Blocks must be separated by a comment line or a line that does not match `key: value`, and values must not have trailing inline comments.
- A diverging sequence can still report a converged `p`. Check `eps21` and `eps32` in the results when `p` looks suspicious.
- `run_study.sh` has no error handling: if mesh generation or `prep.py` fails, the script continues and may upload a broken case.
- `run_study.sh` edits `parameter.yml` in place, so after a run the file holds the last grid factor. The values used for each case are saved as `parameters_used.yml` and `geometry_used.yml` in its directory.

---

## Reference

Celik, I. B., Ghia, U., Roache, P. J., Freitas, C. J., Coleman, H., Raad, P. E. (2008). *Procedure for Estimation and Reporting of Uncertainty Due to Discretization in CFD Applications.* Journal of Fluids Engineering, 130(7), 078001.
