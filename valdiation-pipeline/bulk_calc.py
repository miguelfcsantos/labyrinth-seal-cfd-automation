import math
import re
import sys
import os

MERGE_DONE_DIR = "/your/path/local/merge_done"
VALUES_DIR     = "/your/path/VALUES"

# Reference values (no longer used for comparison, kept commented for context)
# CD_ref = 0.2776
# σ_ref  = 0.4283
# Kz_ref = 0.2836

# Fixed constants
KAPPA = 1.4
R_GAS = 287.0
CP    = 1005.0


# Averaging types to process (residual files currently only produced for 'flux')
AVG_TYPES = ["flux"]

# ============================================================
#  READERS
# ============================================================

def read_yaml_value(filepath, key):
    """Read a top-level 'key: value' from a yml file, ignoring inline comments."""
    with open(filepath) as f:
        for line in f:
            if line.startswith(key + ":"):
                value = line.split(":", 1)[1].strip()
                value = value.split("#")[0].strip()
                return float(value)
    raise ValueError(f"Key '{key}' not found in {filepath}")


def parse_d0_residual(filepath):
    """
    Parse a single-zone residual .dat file (row-based Tecplot format), e.g.
    d0_flux_inlet.dat / d0_flux_outlet.dat.

    These files hold one row per timestep/iteration (a convergence history),
    starting at TimeStep=0 (the initial guess). We want the LAST row, i.e.
    the most converged/final values -- not the first.

    Returns a dict mapping VARIABLES names -> the last data row's values.
    """
    with open(filepath) as f:
        lines = f.readlines()

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
        raise ValueError(f"No VARIABLES declaration found in {filepath}")

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
        raise ValueError(f"No data rows found in {filepath}")
    if len(numbers) % n_vars != 0:
        raise ValueError(
            f"Data in {filepath} doesn't split evenly into rows of {n_vars} "
            f"variables (got {len(numbers)} numbers total) -- check for "
            f"malformed/truncated rows."
        )

    last_row = numbers[-n_vars:]
    return dict(zip(variables, last_row))


# ============================================================
#  CALCULATIONS
# ============================================================

def gap_area(R, s):
    """Annular gap area, thesis p.45."""
    return 2.0 * math.pi * R * s

def calc_Q_dot_id(pt0, psz, Tt0):
    """Ideal mass-flow function Q based on pressure ratio pt0/psz."""
    pi   =  pt0 / psz
    #pi_c = (2.0 / (KAPPA + 1.0)) ** (KAPPA / (KAPPA - 1.0))
    Q    = (math.sqrt(2*KAPPA / (R_GAS*(KAPPA-1)) *
                      (1 - (1/pi)**((KAPPA-1)/KAPPA)))
            * (1/pi)**(1/KAPPA))
    return Q, pi

def calc_cd(mdot, Q, pt0, A, Tt0):
    return mdot / (Q * pt0 * A / math.sqrt(Tt0))

def calc_sigma(Tt0, Ttz, omega, R):
    return CP * (Ttz - Tt0) / (omega**2 * R**2)

def calc_kz(c_tan, omega, R):
    return c_tan / (omega * R)


# ============================================================
#  SINGLE AVERAGING-TYPE COMPUTATION
# ============================================================

# Mapping of the quantities the math needs -> (variable name in the residual
# file, which zone file it comes from). NOTE: the residual files only expose
# the *absolute-frame* stagnation quantities (PressureStagnationAbs /
# TemperatureStagnationAbs) -- there is no relative-frame stagnation variable
# in this file, so those are used for pt0/Tt0/Ttz. VelocityTheta (relative
# frame, no "Abs") is used for ctan, matching what the previous POST-based
# version used for the Kz calculation.
#
# "ctan"    -> outlet swirl velocity, used for Kz (outlet swirl coefficient)
# "ctan_in" -> inlet swirl velocity,  used for Ki (inlet swirl coefficient)
VAR_MAP = {
    "pt0":     ("PressureStagnationAbs",     "inlet"),
    "Tt0":     ("TemperatureStagnationAbs",  "inlet"),
    "psz":     ("Pressure",                  "outlet"),
    "Ttz":     ("TemperatureStagnationAbs",  "outlet"),
    "mdot":    ("MassFlow",                  "outlet"),
    "ctan":    ("VelocityTheta",             "outlet"),
    "ctan_in": ("VelocityTheta",             "inlet"),
}


def compute_avg(avg_type, base, s_clear, omega, A, R):
    """
    Read the inlet/outlet residual files for one averaging type and return a
    dict of results. Returns None if either file does not exist (with a
    warning).
    """
    f_in  = os.path.join(base, "output", "residual", f"d0_{avg_type}_inlet.dat")
    f_out = os.path.join(base, "output", "residual", f"d0_{avg_type}_outlet.dat")

    for f_prim in (f_in, f_out):
        if not os.path.isfile(f_prim):
            print(f"    [WARN] Missing file for avg_type='{avg_type}': {f_prim}")
            return None

    z_in  = parse_d0_residual(f_in)
    z_out = parse_d0_residual(f_out)
    zones = {"inlet": z_in, "outlet": z_out}

    values = {}
    for quantity, (var_name, zone) in VAR_MAP.items():
        try:
            values[quantity] = zones[zone][var_name]
        except KeyError:
            raise KeyError(
                f"Variable '{var_name}' not found in {zone} file for avg_type='{avg_type}' "
                f"({f_in if zone == 'inlet' else f_out})"
            )

    pt0, Tt0 = values["pt0"], values["Tt0"]
    psz, Ttz = values["psz"], values["Ttz"]
    mdot = abs(values["mdot"])
    ctan    = values["ctan"]
    ctan_in = values["ctan_in"]

    Q, pi = calc_Q_dot_id(pt0, psz, Tt0)
    CD    = calc_cd(mdot, Q, pt0, A, Tt0)
    SIG   = calc_sigma(Tt0, Ttz, omega, R)
    KZ    = calc_kz(ctan, omega, R)
    KI    = calc_kz(ctan_in, omega, R)
    dTt   = Ttz - Tt0

    return dict(avg_type=avg_type, pt0=pt0, psz=psz, Tt0=Tt0, Ttz=Ttz,
                mdot=mdot, ctan=ctan, ctan_in=ctan_in, Q=Q, pi=pi, CD=CD,
                sigma=SIG, Kz=KZ, Ki=KI, dTt=dTt)


# ============================================================
#  OUTPUT HELPERS
# ============================================================

DIVIDER = "#" + "=" * 57

def write_avg_block(f, sim_name, rpm, s_clear, A, r):
    """Write one averaging-type block to an open file handle."""
    f.write(f"\n{DIVIDER}\n")
    f.write(f"#  Averaging type: {r['avg_type'].upper()}\n")
    f.write(f"{DIVIDER}\n")
    f.write(f"# Calculated values for simulation: {sim_name}\n")
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
    f.write(f"\n")
    f.write(f"# --- Source variables used for this block ---\n")
    f.write(f"#  (quantity: variable name in residual file, zone file)\n")
    for quantity, (var_name, zone) in VAR_MAP.items():
        f.write(f"#   {quantity:<7} : {var_name}  ({zone})\n")
    f.write(f"\n")
    f.write(f"#   SIM : {sim_name}   |   avg: {r['avg_type']}\n")
    f.write(f"#   RPM : {rpm:.0f}   |   s = {s_clear*1000:.2f} mm\n")
    f.write(f"#   {'Quantity':<10}  {'Value':>12}\n")
    f.write(f"#   {'-'*26}\n")
    f.write(f"#   {'π':<10}  {r['pi']:>12.4f}\n")
    f.write(f"#   {'ΔTt (K)':<10}  {r['dTt']:>12.4f}\n")
    f.write(f"#   {'CD':<10}  {r['CD']:>12.4f}\n")
    f.write(f"#   {'σ':<10}  {r['sigma']:>12.4f}\n")
    f.write(f"#   {'Kz':<10}  {r['Kz']:>12.4f}\n")
    f.write(f"#   {'Ki':<10}  {r['Ki']:>12.4f}\n")


def print_avg_block(sim_name, rpm, s_clear, r):
    """Print one averaging-type block to stdout."""
    print(f"{'='*56}")
    print(f"  SIM : {sim_name}   |   avg: {r['avg_type']}")
    print(f"  RPM : {rpm:.0f}   |   s = {s_clear*1000:.2f} mm")
    print(f"{'='*56}")
    print(f"  {'Quantity':<10}  {'Value':>12}")
    print(f"  {'-'*26}")
    print(f"  {'π':<10}  {r['pi']:>12.4f}")
    print(f"  {'ΔTt (K)':<10}  {r['dTt']:>12.4f}")
    print(f"  {'CD':<10}  {r['CD']:>12.4f}")
    print(f"  {'σ':<10}  {r['sigma']:>12.4f}")
    print(f"  {'Kz':<10}  {r['Kz']:>12.4f}")
    print(f"  {'Ki':<10}  {r['Ki']:>12.4f}")
    print()


# ============================================================
#  PER-SIM PROCESSING
# ============================================================

def process_sim(sim_name):
    base     = os.path.join(MERGE_DONE_DIR, sim_name)
    geo_file = os.path.join(base, "geometry_used.yml")
    par_file = os.path.join(base, "parameters_used.yml")

    for path in (geo_file, par_file):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Missing: {path}")

    s_clear = read_yaml_value(geo_file, "clearence")          # [m]
    R       = read_yaml_value(geo_file, "inner_height_inlet") # rotor tip radius [m]
    rpm     = read_yaml_value(par_file, "rpm")
    omega   = rpm * 2 * math.pi / 60
    A       = gap_area(R, s_clear)

    # compute results for each averaging type (skip missing files gracefully)
    results = []
    for avg_type in AVG_TYPES:
        r = compute_avg(avg_type, base, s_clear, omega, A, R)
        if r is not None:
            results.append(r)

    if not results:
        raise RuntimeError("No averaging-type files found for this simulation.")

    # --- write output files ---
    def _write_all(f):
        f.write(f"# Results for simulation: {sim_name}\n")
        f.write(f"# rpm: {rpm:.0f}   clearance: {s_clear*1000:.4f} mm   R: {R:.6f} m\n")
        for r in results:
            write_avg_block(f, sim_name, rpm, s_clear, A, r)

    out_path = os.path.join(base, "calculated_values.yml")
    with open(out_path, "w") as f:
        _write_all(f)

    os.makedirs(VALUES_DIR, exist_ok=True)
    values_path = os.path.join(VALUES_DIR, f"values_{sim_name}.yml")
    with open(values_path, "w") as f:
        _write_all(f)

    # print to stdout
    for r in results:
        print_avg_block(sim_name, rpm, s_clear, r)
    print(f"  → written to calculated_values.yml")
    print(f"  → written to {VALUES_DIR}/values_{sim_name}.yml\n")

    return results


# ============================================================
#  MAIN
# ============================================================

sim_dirs = sorted([
    d for d in os.listdir(MERGE_DONE_DIR)
    if os.path.isdir(os.path.join(MERGE_DONE_DIR, d))
])

if not sim_dirs:
    print(f"No subdirectories found in {MERGE_DONE_DIR}")
    sys.exit(1)

print(f"\nFound {len(sim_dirs)} simulation(s) in {MERGE_DONE_DIR}\n")

ok, failed = [], []

for sim_name in sim_dirs:
    try:
        results = process_sim(sim_name)
        ok.append(sim_name)
    except Exception as e:
        failed.append((sim_name, str(e)))
        print(f"  [SKIP] {sim_name}: {e}\n")

print(f"\nDone. {len(ok)} succeeded, {len(failed)} skipped.")
if failed:
    print("\nSkipped simulations:")
    for name, reason in failed:
        print(f"  {name}: {reason}")
