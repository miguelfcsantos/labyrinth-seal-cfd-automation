# Labyrinth Seal Fin-Count Parametric Study

This repository contains the scripts used to generate, process, and analyze a parametric CFD study of a labyrinth seal.

The study investigates the effect of the **number of fins** and **step-height configuration** on labyrinth-seal leakage mass flow. The workflow consists of:

1. **Generating and submitting the CFD simulations** — `run_study.sh`
2. **Extracting and verifying the CFD mass-flow results** — Python post-processing script
3. **Analyzing and plotting the results** — `laby_eq23_check.py`

The complete workflow is:

```text
Parametric study definition
        │
        ▼
run_study.sh
        │
        ▼
TRACE CFD simulations
        │
        ▼
Raw CFD results
        │
        ▼
Mass-flow extraction / verification
        │
        ▼
YAML result files
        │
        ▼
laby_eq23_check.py
        │
        ▼
Reports + heatmaps + trend plots + summary
```

---

# 1. Parametric CFD Study

The study varies two main geometric parameters:

* Number of labyrinth-seal fins
* Step-height configuration

Five step-height configurations are considered:

| Configuration | Description           | Step height |
| ------------- | --------------------- | ----------: |
| `FS`          | Smooth                |     `0.000` |
| `FC`          | Convergent            |    `-0.002` |
| `FD`          | Divergent             |    `+0.002` |
| `FCH`         | Convergent, half step |    `-0.001` |
| `FDH`         | Divergent, half step  |    `+0.001` |

The number of fins is varied from **1 to 9**.

This gives:

```text
5 step configurations × 9 fin counts = 45 CFD simulations
```

The individual cases follow the general naming convention:

```text
<CONFIGURATION>_F<N>
```

For example:

```text
FS_F4
FC_F6
FDH_F9
```

---

# 2. CFD Simulation Generation — `run_study.sh`

`run_study.sh` is the main parametric-study launcher.

It automatically generates the required geometry and TRACE case for every combination of step configuration and fin count, then transfers the cases to the HPC system and submits them through Slurm.

## 2.1 Required environment

The script loads the DLR modules required for geometry generation, preprocessing, and TRACE:

```text
trace_dependencies/gcc-11.4.0-trace-9.7.5-1
gmc/9.6.13
pymesh/2.0.1-numpy
tecplot
trace_suite/9.8.0-double
```

The script also defines the local project directory, HPC scratch directory, HPC host, TRACE input files, and geometry files.

These paths are specific to the DLR computing environment and may need to be changed when running the script on another system.

## 2.2 Geometry generation

For every case, the script modifies the geometry definition according to the selected fin count and step-height configuration.

For a case with `N` fins:

```text
number_of_fins = N
number_of_steps = N - 1
```

The `step_height` array therefore contains one value for each step between consecutive fins.

For example, a four-fin case contains three step heights.

## 2.3 Simulation setup

For each configuration, the script performs the following operations:

1. Modify `geometry.yml`.
2. Generate the base geometry using PyMesh.
3. Copy the TRACE input file.
4. Run the geometry/input preparation through `gmcPlay`.
5. Prepare the split/merge configuration.
6. Save the parameters used for the simulation.
7. Save the geometry used for the simulation.
8. Apply the CFD model journal.
9. Create the simulation configuration file.
10. Transfer the case to the HPC system.
11. Submit the simulation using Slurm.

The CFD model journal applied by the script is:

```text
OmegaSST_Off_Bardina.jou
```

## 2.4 Simulation metadata

Each generated simulation contains a `sim_config.yml` file documenting the main parameters used for that case.

The file contains information such as:

```yaml
sim_name: <case name>
number_of_fins: <number of fins>
number_of_steps: <number of steps>
step_height_value: <step height>
turbulence_model: Wilcox
```

---

# 3. CFD Result Extraction and Verification

After the TRACE simulations have finished, the CFD results are processed to obtain the leakage mass-flow values.

The extraction stage reads the post-processed CFD output and obtains the `MassFlow` values for the different fin-count cases.

The results are then stored in YAML files, which are used as the input for the final analysis script.

The resulting files follow the general naming convention:

```text
values_<CASE>_F<N>.yml
```

and, for the corresponding swirl cases:

```text
values_s<CASE>_F<N>.yml
```

For example:

```text
values_FS_F4.yml
values_sFS_F4.yml
values_FD_F8.yml
values_sFD_F8.yml
```

The extraction stage also provides a direct verification of the mass-flow scaling between different fin counts.

For two cases with fin counts \(z_1\) and \(z_2\), the relation investigated is:

$$
\dot{m}(z_2)
=
\dot{m}(z_1)
\sqrt{\frac{z_1}{z_2}}
$$

or equivalently:

$$
\dot{m}(z)\sqrt{z}=\text{constant}
$$

The extracted CFD mass flows can therefore be compared against the value predicted by this relation.

The earlier direct verification script can also compare the raw CFD mass-flow results between available fin counts and generate a text-based verification report.

---

# 4. Final Analysis — `laby_eq23_check.py`

`laby_eq23_check.py` is the final post-processing and visualization script.

Unlike the initial CFD-result extraction stage, this script does **not** directly access the TRACE simulation directories. It operates on the processed YAML files containing the extracted mass-flow data.

The script reads the available fin-count results, evaluates the scaling relation, and generates the final analysis outputs.

## 4.1 Input data

The script searches the configured values directory:

```text
/localdata1/corr_mi/VALUES/fins
```

The five study configurations are:

```text
FS
FC
FCH
FD
FDH
```

Fin-count comparisons are performed from:

```text
F2
```

through:

```text
F9
```

F1 is excluded from the Eq. 2.3 comparison because the first fin does not have an upstream step and therefore differs fundamentally from the subsequent fins.

## 4.2 Averaging types

The YAML files contain different averaging blocks:

```text
flux
mass
area
```

The default averaging type used by the analysis is:

```text
mass
```

The script can also be configured to use the other averaging types.

## 4.3 Eq. 2.3 comparison

For each configuration, the script reads the available CFD mass-flow values and compares different fin-count combinations.

For two fin counts \(z_1\) and \(z_2\), the predicted mass flow is calculated using:

$$
\dot{m}(z_2)
=
\dot{m}(z_1)
\sqrt{\frac{z_1}{z_2}}
$$

The CFD result is then compared with the predicted value.

The script calculates:

* Absolute error
* Percentage error
* Mean absolute error
* Maximum absolute error
* Standard deviation of the error

All available unordered fin-count pairs are considered, with consecutive fin-count comparisons evaluated first.

---

# 5. Generated Analysis Outputs

For each step-height configuration, the analysis script generates several outputs.

## 5.1 Text reports

A text report is generated containing:

* Available fin counts
* Extracted CFD mass-flow values
* Pairwise Eq. 2.3 predictions
* Absolute errors
* Percentage errors
* Error statistics

These reports provide the numerical basis for the subsequent plots.

## 5.2 Heatmaps

The script generates combined heatmaps showing the error between the CFD results and the Eq. 2.3 prediction for different fin-count combinations.

The heatmaps distinguish between:

* Non-swirl cases
* Swirl cases

The combined representation allows the effect of swirl on the scaling relation to be examined directly.

## 5.3 Mass-flow trend plots

For each step-height configuration, the script generates a plot of:

```text
Mass flow vs. number of fins
```

The CFD results are plotted together with the corresponding Eq. 2.3 scaling curve.

This shows how the actual CFD mass flow changes with increasing fin count and how closely the classical scaling relation follows the CFD data.

## 5.4 Summary plot

A combined summary plot compares the mean absolute error for the different step-height configurations.

The results are separated into:

* Swirl
* Non-swirl

This provides a compact comparison of the accuracy of the scaling relation across the different configurations.

---

# 6. Important Note on Eq. 2.3

The relation

$$
\dot{m}(z)\sqrt{z}=\text{constant}
$$

is treated as a classical scaling approximation for the purpose of this study.

It is based on a simplified treatment and does not by itself provide a complete description of compressible labyrinth-seal leakage.

Therefore, the analysis is intended to quantify how closely the relation reproduces the CFD results for the investigated geometries rather than treating the relation as a complete validation model for the CFD simulations.

---

# 7. Complete Workflow

The intended workflow is:

### Step 1 — Generate and submit the CFD cases

Run:

```bash
./run_study.sh
```

This generates the geometry and TRACE cases for the complete parametric study and submits them to the HPC system.

### Step 2 — Wait for the CFD simulations to finish

The TRACE simulations produce the raw CFD results for all completed cases.

### Step 3 — Extract the CFD results

The result-extraction stage reads the completed simulation results and obtains the relevant `MassFlow` values.

The extracted results are stored as YAML files in the configured values directory.

### Step 4 — Analyze the YAML results

Run:

```bash
python laby_eq23_check.py
```

The script reads the YAML files and generates the numerical reports and plots.

The final workflow is therefore:

```text
                 ┌─────────────────────┐
                 │   run_study.sh      │
                 │                     │
                 │ Geometry + TRACE    │
                 │ case generation     │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │   TRACE CFD runs    │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ Result extraction   │
                 │                     │
                 │ MassFlow → YAML     │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ laby_eq23_check.py  │
                 │                     │
                 │ Analysis + plots    │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ Final results       │
                 │                     │
                 │ Reports             │
                 │ Heatmaps            │
                 │ Trend plots         │
                 │ Summary plots       │
                 └─────────────────────┘
```

---

# 8. Directory Structure

A simplified representation of the workflow is:

```text
project/
│
├── run_study.sh
├── laby_eq23_check.py
│
├── geometry.yml
├── TRACE_entry.input
│
└── results/
    │
    └── values/
        └── fins/
            ├── values_FS_F2.yml
            ├── values_FS_F3.yml
            ├── ...
            ├── values_FD_F9.yml
            ├── values_sFS_F2.yml
            ├── values_sFS_F3.yml
            └── ...
```

The exact directory structure depends on the DLR/HPC environment and the paths configured in the scripts.

---

# 9. Requirements

The CFD generation stage requires the DLR software environment used by the scripts, including:

* TRACE
* GMC
* PyMesh
* Tecplot
* GCC and the corresponding TRACE dependencies
* Slurm/HPC access

The final analysis script requires Python with the libraries used by the script for:

* YAML/data parsing
* Numerical processing
* Plot generation

The paths defined at the beginning of the scripts are specific to the original computing environment and should be adjusted when moving the workflow to another system.

---

# 10. Summary

The repository implements a complete parametric CFD workflow:

```text
Parameter sweep
      ↓
Geometry generation
      ↓
TRACE CFD simulations
      ↓
Mass-flow extraction
      ↓
YAML result files
      ↓
Eq. 2.3 comparison
      ↓
Error analysis
      ↓
Plots and reports
```

The purpose of the workflow is to quantify the relationship between **labyrinth-seal fin count and leakage mass flow** and to assess how accurately the classical fin-count scaling relation represents the CFD results for different step-height configurations and swirl conditions.
"""

path = Path("/mnt/data/README.md")
path.write_text(readme, encoding="utf-8")
print(path)
print(len(readme.splitlines()), "lines")
print(readme[:300])
