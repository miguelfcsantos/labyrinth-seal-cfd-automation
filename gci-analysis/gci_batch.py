"""
GCI Batch Runner — all prefix / row / triplet combinations
===========================================================

Mesh labelling inside each GCI run:
  MESH_3 (coarse) = highest M-index of the triplet
  MESH_2 (medium) = middle M-index
  MESH_1 (fine)   = lowest M-index  (M1 is always the finest mesh)

Grid of cell counts  (columns = M5 … M1, left = coarsest):
                  M5          M4          M3          M2          M1
  AA  -   ---     1_015_740   2_257_920   4_934_700   10_827_120
  AB  - 721_200   1_576_980   3_439_020   7_618_800   16_690_560
  BA  -   ---       520_860   1_433_640   3_955_620   10_827_120
  BB  - 360_000   1_015_740   2_790_840   7_618_800   20_790_840
  CA  -   ---       279_300     947_280   3_230_220   10_827_120
  CB  -   ---       188_400     665_640   2_257_920    7_618_800

Prefixes (no model subvariants anymore — one GCI run per row/triplet):
  wLO  →  input files look like values_wLOCB2.yml
  w    →  input files look like values_wCB2.yml

Output layout:
  RESULTS/
    failed_simulations.yml
    wLOAA/
      432/
        gci_table_flux.txt
        gci_asymptotic_flux.png
        gci_results_flux.yml
        ...
      543/
        ...
    wAB/
      321/
        ...
"""

import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

VALUES_DIR = Path("/localdata1/corr_mi/VALUES")
OUTPUT_DIR = Path("/localdata1/corr_mi/VALUES/RESULTS")

QUANTITIES    = ["CD", "sigma", "Kz"]
AVG_TYPES     = ["flux", "mass", "area"]
SAFETY_FACTOR = 1.25

P_SOLVER_TOL      = 1e-6
P_SOLVER_MAX_ITER = 1000

# Cell counts: CELL_COUNTS[row][m_index]  — None means that mesh does not exist
#   m_index 1 = finest, 5 = coarsest
CELL_COUNTS = {
    "AA": {1: 10_827_120, 2: 4_934_700,  3: 2_257_920, 4: 1_015_740, 5: None},
    "AB": {1: 16_690_560, 2: 7_618_800,  3: 3_439_020, 4: 1_576_980, 5: 721_200},
    "BA": {1: 10_827_120, 2: 3_955_620,  3: 1_433_640, 4: 520_860,   5: None},
    "BB": {1: 20_790_840, 2: 7_618_800,  3: 2_790_840, 4: 1_015_740, 5: 360_000},
    "CA": {1: 10_827_120, 2: 3_230_220,  3: 947_280,   4: 279_300,   5: None},
    "CB": {1: 7_618_800,  2: 2_257_920,  3: 665_640,   4: 188_400,   5: None},
}

# Triplets as (coarse_idx, medium_idx, fine_idx) — fine is always M1
TRIPLETS = [
    (3, 2, 1),   # 321
    (4, 3, 2),   # 432
    (5, 4, 3),   # 543
]

# Prefixes used to build the VALUES filename: values_{PREFIX}{ROW}{MeshIndex}.yml
PREFIXES = ["wLO", "w"]

# ── Parser ────────────────────────────────────────────────────────────────────

def parse_all_blocks(path: Path, quantities: list) -> dict:
    """
    Parse every avg_type block from a mesh YAML file.
    Returns { avg_type: {qty: float, ...}, ... }
    """
    lines = path.read_text().splitlines()
    blocks = {}
    current_kv   = None
    current_avg  = None
    kv_line_re   = re.compile(r'^(\w+)\s*:\s*(\S+)')

    for line in lines:
        stripped = line.strip()
        if stripped == "":
            continue
        if stripped.startswith("#"):
            if current_kv is not None and current_avg is not None:
                blocks[current_avg] = current_kv
            current_kv  = None
            current_avg = None
            continue
        m = kv_line_re.match(stripped)
        if not m:
            if current_kv is not None and current_avg is not None:
                blocks[current_avg] = current_kv
            current_kv  = None
            current_avg = None
            continue
        key, val = m.group(1), m.group(2)
        if current_kv is None:
            current_kv = {}
        current_kv[key] = val
        if key == "avg_type":
            current_avg = val

    if current_kv is not None and current_avg is not None:
        blocks[current_avg] = current_kv

    if not blocks:
        raise ValueError(f"No result blocks found in: {path}")

    parsed = {}
    for avg_name, kv in blocks.items():
        missing = [q for q in quantities if q not in kv]
        if missing:
            raise ValueError(
                f"Quantities {missing} not found in '{avg_name}' block of: {path}\n"
                f"Keys found: {sorted(kv.keys())}"
            )
        parsed[avg_name] = {qty: float(kv[qty]) for qty in quantities}

    return parsed

# ── GCI helpers ───────────────────────────────────────────────────────────────

def refinement_ratio_3d(n_fine: int, n_coarse: int) -> float:
    return (n_fine / n_coarse) ** (1.0 / 3.0)


def solve_apparent_order(eps21, eps32, r21, r32):
    if abs(eps21) < 1e-14 or abs(eps32) < 1e-14:
        return None, "degenerate"
    q_ratio = eps32 / eps21
    s = np.sign(q_ratio)
    if q_ratio <= 0:
        return None, "oscillatory"
    p = abs(np.log(abs(q_ratio)) / np.log(r21))
    for _ in range(P_SOLVER_MAX_ITER):
        r21p = r21 ** p
        r32p = r32 ** p
        d21  = r21p - s
        d32  = r32p - s
        if abs(d21) < 1e-14 or abs(d32) < 1e-14:
            return None, "singular"
        p_new = (1.0 / np.log(r21)) * abs(
            np.log(abs(q_ratio)) + np.log(abs(d21 / d32))
        )
        if abs(p_new - p) < P_SOLVER_TOL:
            return p_new, "converged"
        p = p_new
    return p, "max_iter_reached"


def gci_triplet(f1, f2, f3, r21, r32, qty_name):
    eps21 = f2 - f1
    eps32 = f3 - f2
    base  = dict(qty=qty_name, f1=f1, f2=f2, f3=f3,
                 eps21=eps21, eps32=eps32, r21=r21, r32=r32)

    if abs(eps21) < 1e-14 and abs(eps32) < 1e-14:
        return {**base, "p": None, "p_status": "degenerate",
                "f_extrap": f1, "GCI_fine_%": 0.0,
                "GCI_medium_%": 0.0, "asymptotic_check": 1.0,
                "note": "All three values identical."}

    p, p_status = solve_apparent_order(eps21, eps32, r21, r32)

    status_notes = {
        "converged":        "",
        "oscillatory":      "Oscillatory convergence. GCI not reliable.",
        "degenerate":       "One epsilon is zero — cannot compute p.",
        "singular":         "Iteration hit a singularity (r^p ≈ s).",
        "max_iter_reached": f"p solver did not converge in {P_SOLVER_MAX_ITER} iterations.",
    }
    note = status_notes.get(p_status, f"Unknown status: {p_status}")

    if p is not None:
        r21p       = r21 ** p
        r32p       = r32 ** p
        f_extrap   = f1 + (f1 - f2) / (r21p - 1.0)
        GCI_fine   = SAFETY_FACTOR * abs(eps21) / (r21p - 1.0) / abs(f1) * 100.0
        GCI_medium = SAFETY_FACTOR * abs(eps32) / (r32p - 1.0) / abs(f2) * 100.0
        asymptotic = GCI_medium / (r21p * GCI_fine)
    else:
        f_extrap = GCI_fine = GCI_medium = asymptotic = None

    return {**base, "p": p, "p_status": p_status,
            "f_extrap": f_extrap, "GCI_fine_%": GCI_fine,
            "GCI_medium_%": GCI_medium, "asymptotic_check": asymptotic,
            "note": note}

# ── Formatting ────────────────────────────────────────────────────────────────

def fmt(v, decimals=6):
    return "N/A" if v is None else f"{v:.{decimals}f}"

def fmt_pct(v):
    return "N/A" if v is None else f"{v:.4f}%"

# ── Summary table ─────────────────────────────────────────────────────────────

def build_table(all_results):
    col_headers = ["Quantity", "f1 (fine)", "f2 (medium)", "f3 (coarse)",
                   "p", "GCI fine %", "GCI med %", "Asymptotic", "Status"]
    rows = []
    for r in all_results:
        rows.append([
            r["qty"],
            fmt(r["f1"], 6), fmt(r["f2"], 6), fmt(r["f3"], 6),
            fmt(r["p"], 4) if r["p"] is not None else "N/A",
            fmt_pct(r["GCI_fine_%"]),
            fmt_pct(r["GCI_medium_%"]),
            fmt(r["asymptotic_check"], 4) if r["asymptotic_check"] is not None else "N/A",
            r["p_status"],
        ])

    col_w = [max(len(col_headers[i]), *(len(row[i]) for row in rows))
             for i in range(len(col_headers))]
    sep  = "+" + "+".join("-" * (w + 2) for w in col_w) + "+"
    hdr  = "|" + "|".join(f" {col_headers[i]:<{col_w[i]}} " for i in range(len(col_headers))) + "|"
    lines = [sep, hdr, sep.replace("-", "=").replace("+", "+")]
    for row in rows:
        lines.append("|" + "|".join(f" {row[i]:<{col_w[i]}} " for i in range(len(col_headers))) + "|")
        lines.append(sep)

    note_lines = ["\nNotes:"]
    for r in all_results:
        if r.get("note"):
            note_lines.append(f"  {r['qty']}: {r['note']}")
    if len(note_lines) == 1:
        note_lines.append("  None.")
    lines += note_lines
    return "\n".join(lines)

# ── Plotter ───────────────────────────────────────────────────────────────────

def plot_asymptotic(all_results, out_path, avg_type, title_extra=""):
    labels = [r["qty"] for r in all_results]
    values = [r["asymptotic_check"] for r in all_results]

    x_valid, y_valid, x_none = [], [], []
    for i, (lbl, val) in enumerate(zip(labels, values)):
        if val is not None:
            x_valid.append(i); y_valid.append(val)
        else:
            x_none.append(i)

    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 1.4), 5))
    ax.axhline(1.0, color="red", linestyle="--", linewidth=1.2, label="Ideal (1.0)", zorder=1)
    ax.axhspan(0.95, 1.05, alpha=0.12, color="green", label="±5 % band")

    if x_valid:
        ax.scatter(x_valid, y_valid, color="#1f77b4", s=90, zorder=3, label="Asymptotic check")
        ax.plot(x_valid, y_valid, color="#1f77b4", linewidth=1.0, zorder=2)
        for xi, yi in zip(x_valid, y_valid):
            ax.annotate(f"{yi:.4f}", (xi, yi), textcoords="offset points",
                        xytext=(0, 9), ha="center", fontsize=9)
    if x_none:
        ax.scatter(x_none, [0]*len(x_none), color="grey", s=60, marker="x",
                   zorder=3, label="N/A")
        for xi in x_none:
            ax.annotate("N/A", (xi, 0), textcoords="offset points",
                        xytext=(0, 9), ha="center", fontsize=9, color="grey")

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_xlabel("Quantity", fontsize=11)
    ax.set_ylabel("Asymptotic check  (GCI_med / r²¹ᵖ · GCI_fine)", fontsize=10)
    ax.set_title(
        f"GCI Asymptotic Convergence Check — {title_extra}  avg_type: {avg_type}\n"
        r"Ideal $\approx$ 1.0  (Celik et al. 2008)", fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

# ── Per-row/triplet GCI run ───────────────────────────────────────────────────

def run_and_write(
    avg_type: str,
    data: dict,          # {coarse|medium|fine: {qty: float}}
    r21: float, r32: float,
    out_dir: Path,
    triplet_label: str,  # e.g. "321"
    mesh_info: dict,     # {coarse|medium|fine: {label, cell_count, path}}
):
    all_results = []
    for qty in QUANTITIES:
        f1 = data["fine"][avg_type][qty]
        f2 = data["medium"][avg_type][qty]
        f3 = data["coarse"][avg_type][qty]
        all_results.append(gci_triplet(f1, f2, f3, r21, r32, qty))

    table_str  = build_table(all_results)
    title_info = f"triplet {triplet_label}"

    stem_txt = out_dir / f"gci_table_{avg_type}.txt"
    stem_png = out_dir / f"gci_asymptotic_{avg_type}.png"
    stem_yml = out_dir / f"gci_results_{avg_type}.yml"

    # ── text table ──
    stem_txt.write_text(
        "GCI Analysis Summary Table\n"
        "Method: Celik et al. (2008), ASME J. Fluids Eng. 130(7):078001\n"
        f"Averaging type  : {avg_type}\n"
        f"Triplet         : {triplet_label}\n"
        f"Coarse (MESH_3) : {mesh_info['coarse']['path']}  ({mesh_info['coarse']['cell_count']} cells)\n"
        f"Medium (MESH_2) : {mesh_info['medium']['path']}  ({mesh_info['medium']['cell_count']} cells)\n"
        f"Fine   (MESH_1) : {mesh_info['fine']['path']}  ({mesh_info['fine']['cell_count']} cells)\n"
        f"r21 = {r21:.6f}   r32 = {r32:.6f}\n\n"
        + table_str + "\n"
    )

    # ── plot ──
    plot_asymptotic(all_results, stem_png, avg_type, title_extra=title_info)

    # ── YAML ──
    yaml_lines = [
        "# GCI Analysis Results\n",
        "# Method: Celik et al. (2008), ASME J. Fluids Eng. 130(7):078001\n",
        f"# avg_type       : {avg_type}\n",
        f"# triplet        : {triplet_label}\n",
        f"# coarse (MESH_3): {mesh_info['coarse']['path']}  ({mesh_info['coarse']['cell_count']} cells)\n",
        f"# medium (MESH_2): {mesh_info['medium']['path']}  ({mesh_info['medium']['cell_count']} cells)\n",
        f"# fine   (MESH_1): {mesh_info['fine']['path']}  ({mesh_info['fine']['cell_count']} cells)\n",
        f"# r21 (fine/medium)  : {r21:.6f}\n",
        f"# r32 (medium/coarse): {r32:.6f}\n",
        "#\n",
    ]
    for res in all_results:
        yaml_lines.append(f"\n# --- avg_type: {avg_type}  qty: {res['qty']} ---\n")
        for key in ["qty", "f1", "f2", "f3", "eps21", "eps32", "r21", "r32",
                    "p", "p_status", "f_extrap",
                    "GCI_fine_%", "GCI_medium_%", "asymptotic_check", "note"]:
            val = res.get(key)
            if val is None:
                val = "null"
            elif isinstance(val, float):
                val = f"{val:.8g}"
            elif isinstance(val, str) and val == "":
                continue
            yaml_lines.append(f"{avg_type}_{res['qty']}_{key}: {val}\n")
    stem_yml.write_text("".join(yaml_lines))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    failed = []   # list of dicts describing every missing/broken case

    rows      = list(CELL_COUNTS.keys())               # AA AB BA BB CA CB
    triplets  = TRIPLETS                                # (3,2,1) (4,3,2) (5,4,3)
    prefixes  = PREFIXES                                # wLO, w

    for prefix in prefixes:

        for row in rows:
            counts = CELL_COUNTS[row]

            for (ci, mi, fi) in triplets:
                triplet_label = f"{ci}{mi}{fi}"

                # Check all three mesh indices exist for this row
                if any(counts[idx] is None for idx in (ci, mi, fi)):
                    # Not an error — this triplet simply doesn't exist for this row
                    continue

                n_coarse = counts[ci]
                n_medium = counts[mi]
                n_fine   = counts[fi]

                r21 = refinement_ratio_3d(n_fine,   n_medium)
                r32 = refinement_ratio_3d(n_medium, n_coarse)

                # Output folder:  RESULTS / {PREFIX}{ROW} / {triplet_label}
                result_prefix = prefix + row   # e.g. wLOAB, wAB
                triplet_dir   = OUTPUT_DIR / result_prefix / triplet_label
                triplet_dir.mkdir(parents=True, exist_ok=True)

                # Build the three file paths: values_{PREFIX}{ROW}{MeshIndex}.yml
                def make_path(idx):
                    name = f"values_{prefix}{row}{idx}.yml"
                    return VALUES_DIR / name

                paths = {
                    "coarse": make_path(ci),
                    "medium": make_path(mi),
                    "fine":   make_path(fi),
                }
                cells = {
                    "coarse": n_coarse,
                    "medium": n_medium,
                    "fine":   n_fine,
                }

                # Check existence
                missing_files = [str(p) for p in paths.values() if not p.exists()]
                if missing_files:
                    for mf in missing_files:
                        failed.append({
                            "simulation": f"{result_prefix}{triplet_label}",
                            "missing_file": mf,
                        })
                        print(f"[SKIP] {result_prefix} triplet {triplet_label}"
                              f" — missing: {mf}")
                    continue

                # Parse all avg_type blocks from each file
                try:
                    parsed = {}
                    mesh_info = {}
                    for role, path in paths.items():
                        parsed[role]    = parse_all_blocks(path, QUANTITIES)
                        mesh_info[role] = {
                            "path":       path,
                            "cell_count": cells[role],
                        }
                except Exception as e:
                    failed.append({
                        "simulation": f"{result_prefix}{triplet_label}",
                        "error": str(e),
                    })
                    print(f"[ERROR] {result_prefix} triplet {triplet_label}: {e}")
                    continue

                # Check all avg_types present in every mesh file
                avg_types_ok = True
                for role, blocks in parsed.items():
                    missing_avg = [a for a in AVG_TYPES if a not in blocks]
                    if missing_avg:
                        failed.append({
                            "simulation": f"{result_prefix}{triplet_label}",
                            "error": f"Missing avg_type blocks {missing_avg} in {paths[role]}",
                        })
                        print(f"[ERROR] {result_prefix} triplet {triplet_label}"
                              f" — missing avg_types {missing_avg} in {role} file")
                        avg_types_ok = False
                if not avg_types_ok:
                    continue

                # Run GCI for each avg_type
                for avg_type in AVG_TYPES:
                    data_for_gci = {
                        role: parsed[role][avg_type]
                        for role in ("coarse", "medium", "fine")
                    }
                    try:
                        run_and_write(
                            avg_type    = avg_type,
                            data        = {role: {avg_type: data_for_gci[role]}
                                           for role in data_for_gci},
                            r21         = r21,
                            r32         = r32,
                            out_dir     = triplet_dir,
                            triplet_label=triplet_label,
                            mesh_info   = mesh_info,
                        )
                        print(f"[OK]   {result_prefix}/{triplet_label} avg={avg_type}")
                    except Exception as e:
                        failed.append({
                            "simulation": f"{result_prefix}{triplet_label}_{avg_type}",
                            "error": str(e),
                        })
                        print(f"[ERROR] {result_prefix} triplet {triplet_label}"
                              f" avg={avg_type}: {e}")

    # ── Write failed_simulations.yml ──────────────────────────────────────────
    failed_path = OUTPUT_DIR / "failed_simulations.yml"
    if failed:
        lines = ["# Failed / missing simulations\n#\n"]
        for i, entry in enumerate(failed):
            lines.append(f"# entry {i+1}\n")
            for k, v in entry.items():
                lines.append(f"{k}: {v}\n")
            lines.append("\n")
        failed_path.write_text("".join(lines))
        print(f"\n{len(failed)} failure(s) written to {failed_path}")
    else:
        failed_path.write_text("# No failed simulations.\n")
        print("\nAll simulations completed successfully.")


if __name__ == "__main__":
    main()
