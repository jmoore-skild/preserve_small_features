"""Aggregate one part's runs into results/<part>/summary.csv and plots.

    python -m pipeline.collect --part bracket  # writes results/bracket/summary.csv and results/bracket/*.png

Reads runs/<part>/<id>/{mechanical,metrics,validation}.json. M0R runs take their TO data from the matching M0 run.
Run directories starting with "_" and check_full_space are skipped. The reference for specific stiffness
and speedup is the finest M3 run.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pipeline.config import REPO

RUNS = REPO / "runs"
RESULTS = REPO / "results"

# Reference categorical palette, fixed order (dataviz skill; validated light-mode set). Aqua and yellow sit
# below 3:1 on the surface, so every series is also direct-labeled and has its own marker.
STYLE = {
    "M3": dict(color="#2a78d6", marker="o", label="M3 (bore exclusion + rebuild)"),
    "M0R": dict(color="#eb6834", marker="s", label="M0R (default exclusion + rebuild)"),
    "M0": dict(color="#1baf7a", marker="^", label="M0 raw (no rebuild)"),
    "X2": dict(color="#eda100", marker="D", label="X2 (M3, 8 mm exclusion)"),
}
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def series(run_id: str) -> str:
    return "X2" if run_id.endswith("_X2") else run_id.split("_")[0]


def collect(part: str) -> list[dict]:
    rows = []
    runs = RUNS / part
    for d in sorted(p for p in runs.iterdir() if p.is_dir() and not p.name.startswith("_")):
        metrics = load(d / "metrics.json")
        if metrics is None:
            continue
        mech = load(runs / d.name.replace("M0R_", "M0_", 1) / "mechanical.json") or {}
        val = load(d / "validation.json")
        opt = mech.get("optimizer", {})
        s, c, v = metrics["summary"], metrics["connectivity"], metrics["volumes"]
        row = {
            "run": d.name, "series": series(d.name), "h": metrics["h"], "rebuilt": metrics["rebuilt"],
            "elements": mech.get("mesh", {}).get("elements"), "iterations": opt.get("iterations"),
            "cores": mech.get("cores", {}).get("topology"),
            "to_time_s": mech.get("wall_time", {}).get("solve_topology"),
            "volume_fraction": opt["volume_final"] / opt["volume_initial"] if opt else None,
            "surface_max_deviation_mm": s["surface_max_deviation"],
            "surface_max_p99_deviation_mm": s["surface_max_p99_deviation"],
            "surface_max_beyond_fraction": s["surface_max_beyond_fraction"],
            "surface_regions_failing": len(s["surface_regions_failing"]),
            "fix_load_connected": c["fix_load_connected"],
            "passive_attached": sum(c["passive_attached"].values()),
            "seam_flags": len(s["seam_flags"]),
            "detached": len(s["detached"]),
            "pass": all(metrics["pass"].values()) if metrics["rebuilt"] else None,
            "V_opt": v.get("opt"), "V_added": v.get("added"), "V_trimmed": v.get("trimmed"),
            "V_final": v.get("final"), "V_islands": v.get("islands"),
        }
        if "legacy_beam" in metrics:
            row.update({
                "bore_radius_error_mm": s["bore_max_abs_radius_error"],
                "bore_axis_offset_mm": s["bore_max_axis_offset"],
                "seat_deviation_mm": s["seat_max_deviation"], "seat_misses": s["seat_misses"],
                "legacy_seam_flags": len(s["legacy_seam_flags"]),
            })
        if val and val.get("status") == "ok":
            row.update({
                "pilot_u_along_load_mm": val["pilot_u_along_load"][0] if len(val["pilot_u_along_load"]) == 1
                else val["pilot_u_along_load"],
                "converged": val["converged"],
                "stiffness_N_per_mm": val["stiffness"], "mass_kg": val["mass"],
                "specific_stiffness": val["specific_stiffness"], "strain_energy_Nmm": val["strain_energy"],
                "optimizer_strain_energy_Nmm": opt.get("compliance_final"),
                "model_form_gap": val["strain_energy"] / opt["compliance_final"] - 1 if opt else None,
                "member_vm_max_MPa_indicative": val["stress"]["member_vm_max"],
            })
        rows.append(row)

    ref = min((r for r in rows if r["series"] == "M3" and "specific_stiffness" in r), key=lambda r: r["h"],
              default=None)
    for r in rows:
        r["reference"] = ref["run"] if ref else None
        if ref and "specific_stiffness" in r:
            r["specific_stiffness_vs_ref"] = r["specific_stiffness"] / ref["specific_stiffness"] - 1
        if ref and r.get("to_time_s"):
            r["speedup_vs_ref"] = ref["to_time_s"] / r["to_time_s"]
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- plots

def _axes(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_2)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


DODGE = {"M0": 1.0, "M0R": 0.93, "M3": 1.0, "X2": 1.07}   # x factors: rebuilt runs coincide in y


def _line_by_h(ax, rows, key, floor):
    """One line per series over h. The rebuilt series overlap, so they are dodged in x and labeled once
    as a group; raw M0 is labeled directly."""
    for name in ("M0", "M0R", "M3", "X2"):
        pts = sorted((r["h"] * DODGE[name], max(r[key], floor)) for r in rows if r["series"] == name)
        if not pts:
            continue
        st = STYLE[name]
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=st["color"], marker=st["marker"], markersize=8, linewidth=2,
                markeredgecolor=SURFACE, markeredgewidth=2, label=st["label"])
        if name == "M0":
            ax.annotate("M0 raw", (xs[-1], ys[-1]), textcoords="offset points", xytext=(0, 9), ha="center",
                        color=INK_2, fontsize=9)
    rebuilt = [max(r[key], floor) for r in rows if r["rebuilt"]]
    if rebuilt:
        ax.annotate("rebuilt: M3, M0R, X2", (1, max(rebuilt)), textcoords="offset points", xytext=(0, 9),
                    ha="left", color=INK_2, fontsize=9)


def plot_fidelity(rows, path):
    import matplotlib.pyplot as plt
    floor = 1e-5                                     # zero seat deviation drawn at the floor (log axis)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), facecolor=SURFACE)
    for ax, key, title in ((axes[0], "surface_max_deviation_mm", "Preserved surface, max deviation (mm)"),
                           (axes[1], "surface_max_p99_deviation_mm", "Preserved surface, 99th percentile (mm)")):
        _axes(ax, title)
        _line_by_h(ax, rows, key, floor)
        ax.axhline(0.02, color=INK_2, linewidth=1, linestyle="--")
        ax.annotate("pass limit 0.02 mm", (1, 0.02), textcoords="offset points", xytext=(0, 4), ha="left",
                    color=INK_2, fontsize=8)
        ax.set_yscale("log")
        ax.set_xscale("log")
        ax.set_ylim(floor / 2, 60)
        ax.set_xlim(0.8, 5)
        ax.set_xticks([1, 2, 4], ["1", "2", "4"])
        ax.minorticks_off()
        ax.set_xlabel("TO mesh size h (mm)", color=INK_2, fontsize=9)
    axes[1].set_title(f"Preserved surface, 99th pct. (mm; 0 drawn at {floor:g})", loc="left", color=INK,
                      fontsize=11)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=8, loc="lower center", ncol=4)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_stiffness_vs_time(rows, path):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4.5), facecolor=SURFACE)
    _axes(ax, "Validated specific stiffness vs TO solve time")
    pts = [r for r in rows if "specific_stiffness" in r and r.get("to_time_s")]
    ref = next((r for r in pts if r["run"] == r["reference"]), None)
    if ref:
        k = ref["specific_stiffness"]
        ax.axhspan(0.9 * k, 1.1 * k, color=GRID, alpha=0.6, linewidth=0)
        ax.annotate("reference ±10 %", (max(r["to_time_s"] for r in pts), 1.1 * k), textcoords="offset points",
                    xytext=(0, -12), ha="right", color=INK_2, fontsize=8)
    for name in ("M0R", "M3", "X2"):
        sel = [r for r in pts if r["series"] == name]
        if not sel:
            continue
        st = STYLE[name]
        ax.scatter([r["to_time_s"] for r in sel], [r["specific_stiffness"] for r in sel], s=64,
                   color=st["color"], marker=st["marker"], edgecolors=SURFACE, linewidths=2, label=st["label"],
                   zorder=3)
        for r in sel:
            # M0R_h4 sits just left of and below X2; its label goes under the marker to clear X2's.
            below = r["series"] == "M0R" and r["h"] == 4
            ax.annotate(r["run"], (r["to_time_s"], r["specific_stiffness"]), textcoords="offset points",
                        xytext=(0, -14) if below else (7, -3), ha="center" if below else "left",
                        color=INK_2, fontsize=8)
    lo = min(r["specific_stiffness"] for r in pts)
    ax.set_ylim(lo - 80, None)
    ax.set_xlabel("TO solve wall time (s, 24 cores)", color=INK_2, fontsize=9)
    ax.set_ylabel("N/mm per kg", color=INK_2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_volumes(rows, path):
    import matplotlib.pyplot as plt
    reb = [r for r in rows if r["rebuilt"]]
    reb.sort(key=lambda r: (r["series"], -r["h"]))
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.45 * len(reb) + 1.4), sharey=True, facecolor=SURFACE)
    names = [r["run"] for r in reb]
    for ax, key, title in ((axes[0], "V_added", "Added by ∪ keep (mm³)"),
                           (axes[1], "V_trimmed", "Trimmed by ∩ space (mm³)")):
        _axes(ax, title)
        ax.grid(True, axis="x", color=GRID)
        ax.grid(False, axis="y")
        vals = [r[key] for r in reb]
        ax.barh(names, vals, height=0.6, color=[STYLE[r["series"]]["color"] for r in reb],
                edgecolor=SURFACE, linewidth=2)
        for n, val in zip(names, vals):
            ax.annotate(f"{val:,.0f}", (val, n), textcoords="offset points", xytext=(4, 0), va="center",
                        color=INK_2, fontsize=8)
        ax.set_xlim(0, max(vals) * 1.25)
    axes[0].invert_yaxis()
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", required=True)
    a = ap.parse_args(argv)
    rows = collect(a.part)
    out = RESULTS / a.part
    out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out / "summary.csv")
    plot_fidelity(rows, out / "fidelity_vs_h.png")
    plot_stiffness_vs_time(rows, out / "stiffness_vs_time.png")
    plot_volumes(rows, out / "volume_bookkeeping.png")
    print(f"{len(rows)} runs -> results/{a.part}/summary.csv, fidelity_vs_h.png, stiffness_vs_time.png, "
          f"volume_bookkeeping.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
