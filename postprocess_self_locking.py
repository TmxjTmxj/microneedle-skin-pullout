from __future__ import print_function

import csv
import json
import math
import os

from odbAccess import openOdb


ROOT = os.path.abspath(os.getcwd())
ODB_PATH = os.path.join(ROOT, "self_locking_pullout_explicit.odb")


def find_history_key(region, prefix):
    for key in region.historyOutputs.keys():
        if key == prefix or key.startswith(prefix):
            return key
    return None


def main():
    odb = openOdb(ODB_PATH, readOnly=True)
    try:
        rp_region = None
        assembly_region = None
        for name, region in odb.steps["Insertion"].historyRegions.items():
            keys = region.historyOutputs.keys()
            if find_history_key(region, "RF3") and find_history_key(region, "U3"):
                rp_region = name
            if find_history_key(region, "ALLKE") and find_history_key(region, "ALLIE"):
                assembly_region = name
        if rp_region is None:
            raise RuntimeError("Could not find RP history region with RF3/U3")
        if assembly_region is None:
            raise RuntimeError("Could not find assembly energy history region")

        curve_rows = []
        energy_rows = []
        total_offset = 0.0
        for step_name in ("Insertion", "Holding", "Pull_out"):
            step = odb.steps[step_name]
            rp = step.historyRegions[rp_region]
            asm = step.historyRegions[assembly_region]
            u_key = find_history_key(rp, "U3")
            rf_key = find_history_key(rp, "RF3")
            u_data = rp.historyOutputs[u_key].data
            rf_data = rp.historyOutputs[rf_key].data
            n = min(len(u_data), len(rf_data))
            for i in range(n):
                t = float(u_data[i][0])
                u3 = float(u_data[i][1])
                rf3 = float(rf_data[i][1])
                curve_rows.append({
                    "step": step_name,
                    "step_time_s": t,
                    "total_time_s": total_offset + t,
                    "rp_u3_mm": u3,
                    "rp_rf3_N": rf3,
                    "insertion_depth_mm": max(0.0, -u3),
                    "pullout_displacement_mm": max(0.0, u3 + 1.35) if step_name == "Pull_out" else 0.0,
                    "pullout_resistance_N": max(0.0, rf3) if step_name == "Pull_out" else 0.0,
                    "absolute_force_N": abs(rf3),
                })

            energy_keys = {k: find_history_key(asm, k) for k in ("ALLKE", "ALLIE", "ALLAE", "ALLVD")}
            energy_data = {}
            for key, hist_key in energy_keys.items():
                if hist_key:
                    energy_data[key] = asm.historyOutputs[hist_key].data
            max_len = max([len(v) for v in energy_data.values()] or [0])
            for i in range(max_len):
                row = {"step": step_name}
                t = None
                for key, data in energy_data.items():
                    if i < len(data):
                        t = float(data[i][0])
                        row[key] = float(data[i][1])
                    else:
                        row[key] = ""
                if t is not None:
                    row["step_time_s"] = t
                    row["total_time_s"] = total_offset + t
                    allie = row.get("ALLIE", 0.0) or 0.0
                    allke = row.get("ALLKE", 0.0) or 0.0
                    row["ALLKE_over_abs_ALLIE"] = abs(allke) / max(abs(allie), 1.0e-30)
                    energy_rows.append(row)
            total_offset += step.timePeriod

        curve_csv = os.path.join(ROOT, "self_locking_force_displacement_curve.csv")
        with open(curve_csv, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "step", "step_time_s", "total_time_s", "rp_u3_mm", "rp_rf3_N",
                "insertion_depth_mm", "pullout_displacement_mm",
                "pullout_resistance_N", "absolute_force_N",
            ])
            writer.writeheader()
            writer.writerows(curve_rows)

        energy_csv = os.path.join(ROOT, "self_locking_energy_history.csv")
        with open(energy_csv, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "step", "step_time_s", "total_time_s", "ALLKE", "ALLIE", "ALLAE", "ALLVD",
                "ALLKE_over_abs_ALLIE",
            ])
            writer.writeheader()
            writer.writerows(energy_rows)

        pull_rows = [r for r in curve_rows if r["step"] == "Pull_out"]
        hold_rows = [r for r in curve_rows if r["step"] == "Holding"]
        max_pull = max(pull_rows, key=lambda r: r["pullout_resistance_N"]) if pull_rows else None
        max_abs_pull = max(pull_rows, key=lambda r: r["absolute_force_N"]) if pull_rows else None
        retention = None
        if hold_rows:
            tail = hold_rows[int(max(0, len(hold_rows) * 0.75)):]
            retention = sum(abs(r["rp_rf3_N"]) for r in tail) / max(len(tail), 1)

        energy_ratios = [
            r["ALLKE_over_abs_ALLIE"]
            for r in energy_rows
            if isinstance(r.get("ALLKE_over_abs_ALLIE"), float)
            and math.isfinite(r["ALLKE_over_abs_ALLIE"])
            and abs(r.get("ALLIE", 0.0) or 0.0) > 1.0e-8
        ]
        pullout_energy_ratios = [
            r["ALLKE_over_abs_ALLIE"]
            for r in energy_rows
            if r.get("step") == "Pull_out"
            and isinstance(r.get("ALLKE_over_abs_ALLIE"), float)
            and math.isfinite(r["ALLKE_over_abs_ALLIE"])
            and abs(r.get("ALLIE", 0.0) or 0.0) > 1.0e-8
        ]
        summary = {
            "odb": ODB_PATH,
            "rp_history_region": rp_region,
            "energy_history_region": assembly_region,
            "steps": list(odb.steps.keys()),
            "force_displacement_csv": curve_csv,
            "energy_csv": energy_csv,
            "maximum_pullout_force_N": max_pull["pullout_resistance_N"] if max_pull else None,
            "maximum_pullout_force_time_s": max_pull["total_time_s"] if max_pull else None,
            "maximum_pullout_force_rp_u3_mm": max_pull["rp_u3_mm"] if max_pull else None,
            "maximum_abs_pullout_reaction_N": max_abs_pull["absolute_force_N"] if max_abs_pull else None,
            "retention_force_N": retention,
            "max_energy_ratio_ALLKE_abs_ALLIE": max(energy_ratios) if energy_ratios else None,
            "final_energy_ratio_ALLKE_abs_ALLIE": energy_ratios[-1] if energy_ratios else None,
            "max_pullout_energy_ratio_ALLKE_abs_ALLIE": max(pullout_energy_ratios) if pullout_energy_ratios else None,
            "field_outputs_final": list(odb.steps["Pull_out"].frames[-1].fieldOutputs.keys()),
            "frame_counts": {name: len(odb.steps[name].frames) for name in odb.steps.keys()},
        }
        with open(os.path.join(ROOT, "self_locking_postprocess_summary.json"), "w") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
        print(json.dumps(summary, indent=2, sort_keys=True))
    finally:
        odb.close()


if __name__ == "__main__":
    main()
