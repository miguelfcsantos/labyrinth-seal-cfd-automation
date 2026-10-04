#!/usr/bin/env python3
"""
HPC Value Extractor
====================
Polls the HPC for finished simulations (any family -- K, PR, FTH, FAN, SH,
FH, RF, DF, ...), grabs each sim's family from its name, reads its
important values straight off the HPC, and:
  - puts the extracted values in the VALUES folder in the home directory
  - moves the sim's folder on the HPC into sims_valued

Runs once, top to bottom, in series (no polling loop, no threads).

NOTE: sims whose name ends in "_BC" are boundary-condition runs and are
never valued -- they're filtered out before any processing, and never
touched or moved on the HPC.
"""

import math
import os
import re
import subprocess

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

HPC_USER        = "your_username"
HPC_HOST        = "hpc.example.com"

# All sims, any family, grouped by family folder:
#   sims/K2/K2_CLR_230/
#   sims/PR15000/PR15000_CLR_123/
#   sims/FTH1230/FTH1230_FAN_4/
HPC_SIMS        = "/path/to/hpc/workspace/sims"

# Shared "done" archive for ALL sim families, always grouped by prefix:
#   sims_valued/K3/K3_CLR_123
#   sims_valued/PR15000/PR15000_SH_123
#   sims_valued/FTH1230/FTH1230_FAN_4
HPC_SIMS_VALUED = "/path/to/hpc/workspace/sims_valued"

VALUES_DIR = "/path/to/local/VALUES"

FINISH_MARKER = "TRACE terminated normally"

# Sims whose name ends with this suffix are boundary-condition runs and
# are never valued.
BC_SUFFIX = "_BC"

KAPPA = 1.4
R_GAS = 287.0
CP    = 1005.0

# Sutherland's law constants for air (used for the Re_ax viscosity).
SUTHERLAND_MU_REF = 1.716e-5   # Pa.s, reference viscosity at T_ref
SUTHERLAND_T_REF  = 273.15     # K
SUTHERLAND_S      = 110.4      # K, Sutherland's constant for air

AVG_TYPES = ["flux"]   # residual files currently only produced for 'flux'

# "ctan"    -> outlet swirl velocity, used for Kz (outlet swirl coefficient)
# "ctan_in" -> inlet swirl velocity,  used for Ki (inlet swirl coefficient)
VAR_MAP = {
    "pt0":     ("PressureStagnationAbs",     "inlet"),
    "psz":     ("Pressure",                  "outlet"),
    "Tt0":     ("TemperatureStagnationAbs",  "inlet"),
    "Ttz":     ("TemperatureStagnationAbs",  "outlet"),
    "mdot":    ("MassFlow",                  "outlet"),
    "ctan_in": ("VelocityTheta",             "inlet"),
    "ctan":    ("VelocityTheta",             "outlet"),
    "Ma_0":    ("MachAbs",                   "inlet"),
    "Ma_z":    ("MachAbs",                   "outlet"),
}

MARKER = "___FILE_BOUNDARY___"

# ---------------------------------------------------------------------------
# SSH HELPERS
# ---------------------------------------------------------------------------

def ssh(command: str) -> str:
    """
    Runs `command` on the HPC under an explicit `bash -s` (command sent over
    stdin, not as an ssh argv argument). This sidesteps two failure modes
    that otherwise fail *silently* (empty stdout, no exception):
      - the remote user's default login shell not being bash (multi-line
        scripts using bash-only syntax like `${var%/}` then either error
        out or no-op under sh/csh/tcsh, and we'd never see why since we
        only used to capture stdout)
      - ssh/shell quoting mangling a long inline command-line argument
    Any stderr from the remote command is printed here (prefixed) instead
    of being silently discarded, since a swallowed remote error was
    exactly why the family-wide discovery came back as "Total: 0" with no
    indication of what went wrong.
    """
    result = subprocess.run(
        ["ssh", f"{HPC_USER}@{HPC_HOST}", "bash", "-s"],
        input=command,
        capture_output=True,
        text=True,
    )
    if result.stderr.strip():
        print(f"  [ssh stderr] {result.stderr.strip()}")
    return result.stdout


def get_finished_sims() -> list:
    """
    Single SSH call: list every sim under {HPC_SIMS}, grouped by family
    folder (any prefix -- K, PR, FTH, FAN, SH, FH, RF, DF, ... whatever the
    sim-generation side produces), e.g.:
        {HPC_SIMS}/K2/K2_CLR_230/
        {HPC_SIMS}/PR15000/PR15000_CLR_123/
        {HPC_SIMS}/FTH1230/FTH1230_FAN_4/
    and return the ones whose out.log contains the finish marker.

    Boundary-condition sims (name ends with BC_SUFFIX, e.g. "_BC") are
    skipped entirely -- we don't value those, and they aren't counted
    as running either.

    Returns a list of dicts: {"name": <case_name>, "dir": <real remote
    path>}. The real path is captured directly from the glob rather than
    reconstructed from the name, so it doesn't matter whether a given
    family's folder name matches the sim's own prefix exactly.
    """
    batch_cmd = f"""
for sim in "{HPC_SIMS}"/*/*/; do
    [ -d "$sim" ] || continue
    name=$(basename "$sim")
    sim_dir="${{sim%/}}"
    if grep -q "{FINISH_MARKER}" "$sim_dir/input/out.log" 2>/dev/null; then
        echo "$name|$sim_dir|FINISHED"
    else
        echo "$name|$sim_dir|RUNNING"
    fi
done
"""
    output = ssh(batch_cmd)
    finished, running, skipped_bc = [], [], []
    for line in output.strip().splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3:
            name, sim_dir, status = parts
            if name.endswith(BC_SUFFIX):
                skipped_bc.append(name)
                continue
            if status == "FINISHED":
                finished.append({"name": name, "dir": sim_dir})
            else:
                running.append(name)

    total = len(finished) + len(running) + len(skipped_bc)
    print(f"Total across sims/: {total} | Finished: {len(finished)} | "
          f"Still running: {len(running)} | Skipped ({BC_SUFFIX}): {len(skipped_bc)}")
    if running:
        print(f"Still running: {running}")

    if total == 0:
        print(
            "  [debug] Zero sims found under "
            f"{HPC_SIMS}/*/*/ -- dumping the raw, unparsed ssh output below "
            "so you can see exactly what the remote side returned "
            "(empty means the glob matched nothing or the remote command "
            "produced no output at all):"
        )
        print("  ----- raw ssh stdout begin -----")
        print(output if output.strip() else "  (completely empty)")
        print("  ----- raw ssh stdout end -----")
        print(
            "  [debug] To check by hand, run this from your own machine:\n"
            f'  ssh {HPC_USER}@{HPC_HOST} \'for sim in "{HPC_SIMS}"/*/*/; '
            'do echo FOUND: "$sim"; done\''
        )

    return finished


def fetch_sim_files(sim_dir: str, sim_name: str) -> dict:
    """
    One SSH call that reads (cats) the 4 files this sim needs, straight off
    the HPC, no download to disk. `sim_dir` is the sim's real remote
    directory (as discovered by get_finished_sims -- works the same
    regardless of which family folder it lives under). Returns
    {key: text_or_None}. Missing files come back as None (and are
    reported to the caller).

    IMPORTANT: an explicit `echo` (bare newline) follows every `cat`, even
    on success, so the NEXT file's marker always starts on its own fresh
    line. Without this, if a file's last line has no trailing newline (very
    plausible -- not every writer forces one), the next marker gets glued
    onto the end of that line (e.g. "clearence: 0.000217___FILE_BOUNDARY__
    _:parameters:OK"), which doesn't start with MARKER, so it's silently
    swallowed as ordinary content instead of recognized as a section
    boundary -- and the section it should have started never gets created
    in the output dict at all (raising a bare KeyError on access, not a
    "missing file" error, which is what actually happened here).
    """
    files = {
        "geometry":   f"{sim_dir}/geometry_used.yml",
        "parameters": f"{sim_dir}/parameters_used.yml",
        "inlet":      f"{sim_dir}/output/residual/d0_flux_inlet.dat",
        "outlet":     f"{sim_dir}/output/residual/d0_flux_outlet.dat",
    }

    parts = []
    for key, path in files.items():
        parts.append(
            f'if [ -f "{path}" ]; then '
            f'echo "{MARKER}:{key}:OK"; cat "{path}"; echo; '
            f'else echo "{MARKER}:{key}:MISSING"; fi'
        )
    output = ssh("\n".join(parts))

    sections = {}
    current_key = None
    buf = []
    for line in output.splitlines():
        if line.startswith(MARKER + ":"):
            if current_key is not None:
                sections[current_key] = "\n".join(buf)
            _, key, status = line.split(":", 2)
            if status == "MISSING":
                sections[key] = None
                current_key = None
            else:
                current_key = key
                buf = []
        else:
            buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf)

    # Defensive check: even with the trailing-newline fix above, make sure
    # every expected key actually showed up (e.g. if a marker line ever got
    # mangled some other way) rather than letting a plain KeyError surface
    # later with a confusing traceback.
    for key in files:
        if key not in sections:
            raise RuntimeError(
                f"Expected section '{key}' never appeared in ssh output for "
                f"{sim_name} -- marker parsing failed unexpectedly."
            )

    return sections


def move_to_valued_on_hpc(sim_dir: str, sim_name: str, prefix: str):
    """
    Moves a sim's real remote directory into sims_valued/<prefix>/<sim_name>,
    e.g.:
        sims_valued/K3/K3_CLR_123
        sims_valued/PR15000/PR15000_SH_123
        sims_valued/FTH1230/FTH1230_FAN_4
    """
    dest_dir = f"{HPC_SIMS_VALUED}/{prefix}"
    cmd = (
        f"mkdir -p {dest_dir} && "
        f"mv {sim_dir} {dest_dir}/{sim_name}"
    )
    result = subprocess.run(
        ["ssh", f"{HPC_USER}@{HPC_HOST}", cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [{sim_name}] Failed to move on HPC: {result.stderr.strip()}")
    else:
        print(f"  [{sim_name}] Moved to {dest_dir}/{sim_name} on HPC")


# ---------------------------------------------------------------------------
# PARSERS (operate on text already fetched into memory, not on local files)
# ---------------------------------------------------------------------------

def read_yaml_value_from_text(text: str, key: str) -> float:
    """Read a top-level 'key: value' from yaml-ish text, ignoring inline comments."""
    for line in text.splitlines():
        if line.startswith(key + ":"):
            value = line.split(":", 1)[1].strip()
            value = value.split("#")[0].strip()
            return float(value)
    raise ValueError(f"Key '{key}' not found in fetched text")


def parse_d0_residual_from_text(text: str, source_desc: str) -> dict:
    """
    Parse a single-zone residual .dat file (row-based Tecplot format) that
    has already been read into a string. Returns VARIABLES name -> last row
    value (the most converged data row).
    """
    lines = text.splitlines()

    SKIP = ('DATASETAUXDATA', 'ZONE', 'AUXDATA', 'TITLE', 'DT',
            'ZONETYPE', 'STRANDID', 'SOLUTIONTIME', 'VARLOCATION')

    var_text, in_vars = "", False
    for line in lines:
        s = line.strip()
        if re.match(r'VARIABLES\s*=', s, re.IGNORECASE):
            in_vars = True
        if in_vars:
            if any(s.upper().startswith(k) for k in SKIP) and "VARIABLES" not in s.upper():
                in_vars = False
            else:
                var_text += " " + s

    variables = re.findall(r'"([^"]+)"', var_text)
    n_vars = len(variables)
    if n_vars == 0:
        raise ValueError(f"No VARIABLES declaration found in {source_desc}")

    past_zone, numbers = False, []
    for line in lines:
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if re.match(r'ZONE\s+T\s*=', s, re.IGNORECASE):
            past_zone = True
            continue
        if not past_zone:
            continue
        if any(s.upper().startswith(k) for k in SKIP):
            continue
        try:
            numbers.extend(float(x) for x in s.split())
        except ValueError:
            continue

    if not numbers:
        raise ValueError(f"No data rows found in {source_desc}")
    if len(numbers) % n_vars != 0:
        raise ValueError(
            f"Data in {source_desc} doesn't split evenly into rows of {n_vars} "
            f"variables (got {len(numbers)} numbers total) -- check for "
            f"malformed/truncated rows."
        )

    last_row = numbers[-n_vars:]
    return dict(zip(variables, last_row))


# ---------------------------------------------------------------------------
# CALCULATIONS
# ---------------------------------------------------------------------------

def gap_area(R, s):
    """Annular gap area, thesis p.45."""
    return 2.0 * math.pi * R * s


def calc_Q_dot_id(pt0, psz, Tt0):
    pi = pt0 / psz
    Q = (math.sqrt(2 * KAPPA / (R_GAS * (KAPPA - 1)) *
                    (1 - (1 / pi) ** ((KAPPA - 1) / KAPPA)))
         * (1 / pi) ** (1 / KAPPA))
    return Q, pi


def calc_cd(mdot, Q, pt0, A, Tt0):
    return mdot / (Q * pt0 * A / math.sqrt(Tt0))


def calc_sigma(Tt0, Ttz, omega, R):
    return CP * (Ttz - Tt0) / (omega ** 2 * R ** 2)


def calc_kz(c_tan, omega, R):
    return c_tan / (omega * R)


def sutherland_viscosity(T: float) -> float:
    """
    Dynamic viscosity of air via Sutherland's law, evaluated at the
    (stagnation) temperature T [K]. Returns mu in Pa.s.
    """
    return (SUTHERLAND_MU_REF * (T / SUTHERLAND_T_REF) ** 1.5 *
            (SUTHERLAND_T_REF + SUTHERLAND_S) / (T + SUTHERLAND_S))


def calc_re_ax(mdot: float, mu: float, R: float) -> float:
    """
    Axial Reynolds number:
        Re_ax = mdot / (mu * pi * R)
    using the Sutherland viscosity evaluated at Tt0 and the same radius
    R used elsewhere (sigma/Kz/Ki).
    """
    return mdot / (mu * math.pi * R)


def compute_avg(avg_type, z_in, z_out, A, omega, R):
    """Run the math for one averaging type given already-parsed inlet/outlet dicts."""
    zones = {"inlet": z_in, "outlet": z_out}

    values = {}
    for quantity, (var_name, zone) in VAR_MAP.items():
        try:
            values[quantity] = zones[zone][var_name]
        except KeyError:
            raise KeyError(
                f"Variable '{var_name}' not found in {zone} data for avg_type='{avg_type}'"
            )

    pt0, Tt0 = values["pt0"], values["Tt0"]
    psz, Ttz = values["psz"], values["Ttz"]
    mdot = abs(values["mdot"])
    ctan    = values["ctan"]
    ctan_in = values["ctan_in"]
    Ma_0 = values["Ma_0"]
    Ma_z = values["Ma_z"]

    Q, pi = calc_Q_dot_id(pt0, psz, Tt0)
    CD    = calc_cd(mdot, Q, pt0, A, Tt0)
    SIG   = calc_sigma(Tt0, Ttz, omega, R)
    KZ    = calc_kz(ctan, omega, R)
    KI    = calc_kz(ctan_in, omega, R)
    dTt   = Ttz - Tt0

    mu_Tt0 = sutherland_viscosity(Tt0)
    RE_AX  = calc_re_ax(mdot, mu_Tt0, R)

    return dict(avg_type=avg_type, pt0=pt0, psz=psz, Tt0=Tt0, Ttz=Ttz,
                mdot=mdot, ctan=ctan, ctan_in=ctan_in, Q=Q, pi=pi, CD=CD,
                sigma=SIG, Kz=KZ, Ki=KI, dTt=dTt, Ma_0=Ma_0, Ma_z=Ma_z,
                mu_Tt0=mu_Tt0, Re_ax=RE_AX)


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------

DIVIDER = "#" + "=" * 57


def write_avg_block(f, sim_name, rpm, s_clear, A, r):
    f.write(f"\n{DIVIDER}\n")
    f.write(f"#  Averaging type: {r['avg_type'].upper()}\n")
    f.write(f"{DIVIDER}\n")
    f.write(f"sim_name:     {sim_name}\n")
    f.write(f"avg_type:     {r['avg_type']}\n")
    f.write(f"rpm:          {rpm:.0f}\n")
    f.write(f"clearance_mm: {s_clear*1000:.4f}\n")
    f.write(f"A_m2:         {A:.6e}\n")
    f.write(f"pt0_Pa:       {r['pt0']:.4f}\n")
    f.write(f"psz_Pa:       {r['psz']:.4f}\n")
    f.write(f"pi:           {r['pi']:.6f}\n")
    f.write(f"Tt0_K:        {r['Tt0']:.4f}\n")
    f.write(f"Ttz_K:        {r['Ttz']:.4f}\n")
    f.write(f"dTt_K:        {r['dTt']:.6f}\n")
    f.write(f"Q_dot_id:     {r['Q']:.6e}\n")
    f.write(f"CD:           {r['CD']:.6f}\n")
    f.write(f"sigma:        {r['sigma']:.6f}\n")
    f.write(f"Kz:           {r['Kz']:.6f}\n")
    f.write(f"Ki:           {r['Ki']:.6f}\n")
    f.write(f"MassFlow:     {r['mdot']:.6f}\n")
    f.write(f"MachInlet:     {r['Ma_0']:.6f}\n")
    f.write(f"MachOutlet:     {r['Ma_z']:.6f}\n")
    f.write(f"mu_Tt0_Pa_s:  {r['mu_Tt0']:.6e}\n")
    f.write(f"Re_ax:        {r['Re_ax']:.6f}\n")

    f.write("\n# --- Source variables used for this block ---\n")
    for quantity, (var_name, zone) in VAR_MAP.items():
        f.write(f"#   {quantity:<7} : {var_name}  ({zone})\n")


def print_avg_block(sim_name, rpm, s_clear, r):
    print(f"{'='*56}")
    print(f"  SIM : {sim_name}   |   avg: {r['avg_type']}")
    print(f"  RPM : {rpm:.0f}   |   s = {s_clear*1000:.2f} mm")
    print(f"{'='*56}")
    print(f"  {'π':<10}  {r['pi']:>12.4f}")
    print(f"  {'ΔTt (K)':<10}  {r['dTt']:>12.4f}")
    print(f"  {'CD':<10}  {r['CD']:>12.4f}")
    print(f"  {'σ':<10}  {r['sigma']:>12.4f}")
    print(f"  {'Kz':<10}  {r['Kz']:>12.4f}")
    print(f"  {'Ki':<10}  {r['Ki']:>12.4f}")
    print(f"  {'Re_ax':<10}  {r['Re_ax']:>12.4f}")
    print()


# ---------------------------------------------------------------------------
# PER-SIM PROCESSING
# ---------------------------------------------------------------------------

def sim_prefix(sim_name: str) -> str:
    """
    Prefix a sim's results are grouped under -- everything before the
    first underscore, e.g. "K2_CLR_230" -> "K2", "PR20000_CLR_123" ->
    "PR20000", "FTH1230_FAN_4" -> "FTH1230". Works for any family/letter
    code (K, PR, FTH, FAN, SH, FH, RF, DF, ...) without needing to know
    the list of codes in advance. Used for BOTH the local VALUES/<prefix>/
    output folder and the HPC sims_valued/<prefix>/ archive folder. If
    there's no underscore at all, the whole name is used as-is.
    """
    return sim_name.split("_", 1)[0]


def process_sim(sim: dict):
    sim_name = sim["name"]
    sim_dir = sim["dir"]

    print(f"[{sim_name}] Reading files from HPC ({sim_dir})...")
    sections = fetch_sim_files(sim_dir, sim_name)

    missing = [k for k, v in sections.items() if v is None]
    if missing:
        raise FileNotFoundError(f"Missing on HPC: {missing}")

    s_clear = read_yaml_value_from_text(sections["geometry"], "clearence")
    R       = read_yaml_value_from_text(sections["geometry"], "inner_height_inlet")
    rpm     = read_yaml_value_from_text(sections["parameters"], "rpm")
    omega   = rpm * 2 * math.pi / 60
    A       = gap_area(R, s_clear)

    z_in  = parse_d0_residual_from_text(sections["inlet"],  f"{sim_name}/d0_flux_inlet.dat")
    z_out = parse_d0_residual_from_text(sections["outlet"], f"{sim_name}/d0_flux_outlet.dat")

    results = []
    for avg_type in AVG_TYPES:
        r = compute_avg(avg_type, z_in, z_out, A, omega, R)
        results.append(r)
        print_avg_block(sim_name, rpm, s_clear, r)

    prefix = sim_prefix(sim_name)

    # --- local VALUES output: VALUES/<prefix>/<sim_name>.yml ---
    sim_out_dir = os.path.join(VALUES_DIR, prefix)
    os.makedirs(sim_out_dir, exist_ok=True)
    out_path = os.path.join(sim_out_dir, f"{sim_name}.yml")
    with open(out_path, "w") as f:
        f.write(f"# Results for simulation: {sim_name}\n")
        f.write(f"# rpm: {rpm:.0f}   clearance: {s_clear*1000:.4f} mm   R: {R:.6f} m\n")
        for r in results:
            write_avg_block(f, sim_name, rpm, s_clear, A, r)
    print(f"  -> written to {out_path}")

    # --- HPC archive: sims_valued/<prefix>/<sim_name> ---
    move_to_valued_on_hpc(sim_dir, sim_name, prefix)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"HPC sims:    {HPC_USER}@{HPC_HOST}:{HPC_SIMS}/<family>/")
    print(f"HPC valued:  {HPC_USER}@{HPC_HOST}:{HPC_SIMS_VALUED}/<prefix>/")
    print(f"Values dir:  {VALUES_DIR}\n")

    finished = get_finished_sims()
    if not finished:
        print("Nothing finished to process.")
        return

    ok, failed = [], []
    for sim in finished:
        try:
            process_sim(sim)
            ok.append(sim["name"])
        except Exception as exc:
            failed.append((sim["name"], str(exc)))
            print(f"  [SKIP] {sim['name']}: {exc}\n")

    print(f"\nDone. {len(ok)} succeeded, {len(failed)} skipped.")
    if failed:
        print("\nSkipped simulations:")
        for name, reason in failed:
            print(f"  {name}: {reason}")


if __name__ == "__main__":
    main()
