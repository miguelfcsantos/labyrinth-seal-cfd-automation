#!/usr/bin/env python3
"""
Residual macro generator for a SINGLE simulation.

Usage:
    python3 genRESIDUALS_single.py /your/path/local/sims/LC

Generates residual_ALL.mcr in /your/path/MACROS/RESIDUALS/
then runs Tecplot in mesa (headless) mode to produce the PNG plots.
"""

import os
import sys
import subprocess

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MACRO_OUTDIR = "/your/path/MACROS/RESIDUALS"

VAR_NAMES_RESIDUAL = (
    '"TimeStep" "SimulationDuration" "ResidualL1" "ResidualMax" "BlockIndex" '
    '"CellIndexI" "CellIndexJ" "CellIndexK" "ResidualL2" '
    '"DistanceWallCoordinateLowReynoldsMaximum" '
    '"DistanceWallCoordinateLowReynoldsAverage" '
    '"DistanceWallCoordinateWallFunctionMaximum" '
    '"DistanceWallCoordinateWallFunctionAverage" '
    '"ViscosityEddyRatioMax" "BlockIndexViscEddyMax" '
    '"TurbulentEnergyKineticResidualL1" "TurbulentEnergyKineticResidualMax" '
    '"TurbulentDissipationRateResidualL1" "TurbulentDissipationRateResidualMax" '
    '"CFLNumberMin" "BlockIndexCFLNumberMin" "CFLNumberMax" "PerformanceIndex"'
)

VAR_NAMES_MASS = (
    '"TimeStep" "MassFlow" "PressureStagnationAbs" "TemperatureStagnationAbs" '
    '"MachAbs" "Pressure" "Density" "Time" "ViscosityEddy" "VelocityX" '
    '"VelocityThetaAbs" "VelocityR" "VelocityTheta" "VelocityAngleThetaAbs" '
    '"VelocityAngleR" "TurbulentEnergyKinetic" "TurbulentDissipationRate" '
    '"Mach" "VelocityAngleTheta" "Entropy" "BackflowRatio" "SwirlAbs" '
    '"CoordinateX" "CoordinateR" "SpatialAverageType"'
)


# ---------------------------------------------------------------------------
# Macro builder (unchanged from original)
# ---------------------------------------------------------------------------
def build_macro(sim_name, merged_base):
    base   = f"{merged_base}/{sim_name}/output/residual"
    res    = f"{base}/residual.dat"
    inlet  = f"{base}/d0_mass_inlet.dat"
    outlet = f"{base}/d0_mass_outlet.dat"

    def png(name):
        return f"{base}/{name}"

    def read_residual(mode="New"):
        return [
            f'$!ReadDataSet  \'"{res}" \'',
            f"  ReadDataOption = {mode}",
            "  ResetStyle = Yes",
            "  VarLoadMode = ByName",
            "  AssignStrandIDs = Yes",
            f"  VarNameList = '{VAR_NAMES_RESIDUAL}'",
        ]

    def read_inlet(mode="New"):
        return [
            f'$!ReadDataSet  \'"{inlet}" \'',
            f"  ReadDataOption = {mode}",
            "  ResetStyle = Yes" if mode == "New" else "  ResetStyle = No",
            "  VarLoadMode = ByName",
            "  AssignStrandIDs = Yes",
            f"  VarNameList = '{VAR_NAMES_MASS}'",
        ]

    def read_outlet():
        return [
            f'$!ReadDataSet  \'"{outlet}" \'',
            "  ReadDataOption = Append",
            "  ResetStyle = No",
            "  VarLoadMode = ByName",
            "  AssignStrandIDs = Yes",
            f"  VarNameList = '{VAR_NAMES_MASS}'",
        ]

    def export(png_name):
        return [
            f"$!ExportSetup ExportFName = '{png(png_name)}'",
            "$!Export",
            "  ExportRegion = CurrentFrame",
        ]

    L = []
    L += ["#!MC 1410", "$!DRAWGRAPHICS FALSE"]

    L += read_residual("New")
    L += [
        "$!ActiveLineMaps += [2]",
        "$!LineMap [3]  Assign{YAxisVar = 9}",
        "$!ActiveLineMaps += [3]",
        "$!ActiveLineMaps -= [1]",
        "$!Pick AddAtPosition",
        "  X = 8.14977973568",
        "  Y = 2.04955947137",
        "  ConsiderStyle = Yes",
        "$!View Fit",
        "$!View Fit",
        "$!XYLineAxis YDetail 1 {CoordScale = Log}",
        "$!Pick AddAtPosition",
        "  X = 5.45374449339",
        "  Y = 7.09801762115",
        "  ConsiderStyle = Yes",
        "$!View Fit",
        "$!PrintSetup Palette = Color",
        "$!ExportSetup ExportRegion = CurrentFrame",
        "$!ExportSetup ImageWidth = 681",
    ]
    L += export("L1L2.png")
    L += [
        "$!ActiveLineMaps -= [3]",
        "$!LineMap [3]  Assign{YAxisVar = 4}",
        "$!LineMap [2]  Assign{YAxisVar = 4}",
        "$!Pick AddAtPosition",
        "  X = 5.03083700441",
        "  Y = 2.72356828194",
        "  ConsiderStyle = Yes",
        "$!View Fit",
    ]
    L += export("L_max.png")
    L += read_inlet("New")
    L += read_outlet()
    L += [
        "$!AlterData ",
        "  IgnoreDivideByZero = Yes",
        "  Equation = '{MassflowInv]}=-{MassFlow}'",
        "$!Pick AddAtPosition",
        "  X = 2.86343612335",
        "  Y = 3.42400881057",
        "  ConsiderStyle = Yes",
        "$!ActiveLineMaps += [2]",
        "$!LineMap [2]  Assign{YAxisVar = 26}",
        "$!Pick AddAtPosition",
        "  X = 7.859030837",
        "  Y = 1.52092511013",
        "  ConsiderStyle = Yes",
        "$!View Fit",
        "$!LineMap [2]  Assign{Zone = 2}",
        "$!Pick AddAtPosition",
        "  X = 8.95594713656",
        "  Y = 1.66629955947",
        "  ConsiderStyle = Yes",
        "$!View Fit",
    ]
    L += export("MassFlow.png")
    L += read_residual("New")
    L += [
        "$!LineMap [1]  Assign{YAxisVar = 14}",
        "$!Pick AddAtPosition",
        "  X = 6.80176211454",
        "  Y = 4.46806167401",
        "  ConsiderStyle = Yes",
        "$!View Fit",
        "$!Pick AddAtPosition",
        "  X = 4.54185022026",
        "  Y = 4.78524229075",
        "  ConsiderStyle = Yes",
    ]
    L += export("EddyRatioMax.png")
    L += read_inlet("New")
    L += read_outlet()
    L += [
        "$!AlterData",
        "  FEDerivativeMethod = GreenGauss",
        "  IgnoreDivideByZero = Yes",
        "  Equation = '{PressureRatio} = V3[2] / V3[1]'",
        "$!AlterData",
        "  FEDerivativeMethod = GreenGauss",
        "  IgnoreDivideByZero = Yes",
        "  Equation = '{EntropyRise} = V20[2] - V20[1]'",
        "$!AlterData",
        "  FEDerivativeMethod = GreenGauss",
        "  IgnoreDivideByZero = Yes",
        "  Equation = '{TemperatureRatio} = V4[2] / V4[1]'",
        "$!LineMap [1]  Assign{YAxisVar = 26}",
        "$!LineMap [1]  Assign{Zone = 2}",
        "$!Pick AddAtPosition",
        "  X = 6.29955947137",
        "  Y = 2.20814977974",
        "  ConsiderStyle = Yes",
        "$!View Fit",
    ]
    L += export("PressureRatio_mass_avg.png")
    L += [
        "$!LineMap [1]  Assign{YAxisVar = 27}",
        "$!Pick AddAtPosition",
        "  X = 6.96035242291",
        "  Y = 0.675110132159",
        "  ConsiderStyle = Yes",
        "$!View Fit",
    ]
    L += export("EntropyRise_mass_avg.png")
    L += [
        "$!LineMap [1]  Assign{YAxisVar = 28}",
        "$!Pick AddAtPosition",
        "  X = 5.85022026432",
        "  Y = 3.63546255507",
        "  ConsiderStyle = Yes",
        "$!View Fit",
    ]
    L += export("TemperatureRatio_mass_avg.png")

    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <sim_folder_path>")
        print(f"Example: python3 {sys.argv[0]} /your/path/local/sims/LC")
        sys.exit(1)

    sim_path = os.path.abspath(sys.argv[1])
    if not os.path.isdir(sim_path):
        print(f"[ERROR] Folder not found: {sim_path}")
        sys.exit(1)

    sim_name    = os.path.basename(sim_path)
    merged_base = os.path.dirname(sim_path)

    print(f"\n[genRESIDUALS] Processing: {sim_name}")

    os.makedirs(MACRO_OUTDIR, exist_ok=True)

    macro_content = build_macro(sim_name, merged_base)

    combined_path = os.path.join(MACRO_OUTDIR, f"residual_{sim_name}.mcr")
    with open(combined_path, "w") as f:
        f.write(macro_content)
        f.write("$!Quit\n")

    print(f"  [OK] Macro written: {combined_path}")

    command = f'module load tecplot && tec360 -mesa -p "{combined_path}"'
    subprocess.run(command, shell=True, executable="/bin/bash")

    print(f"[genRESIDUALS] Done: {sim_name}\n")


if __name__ == "__main__":
    main()
