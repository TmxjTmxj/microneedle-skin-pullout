from __future__ import print_function

import csv
import json
import math
import os
import shutil

from abaqusConstants import INTEGRATION_POINT, NODAL
from odbAccess import openOdb


ROOT = os.path.abspath(os.getcwd())
ODB_PATH = os.path.join(ROOT, "self_locking_pullout_explicit.odb")
VIEWER_MODELS = os.path.join(os.path.expanduser("~"), ".codex", "mcp", "text-to-cae", "models")
LOCAL_VIEWER = os.path.join(ROOT, "self_locking_browser_viewer")


SURFACE_FACES = {
    "C3D8R": ((1, 2, 3, 4), (5, 8, 7, 6), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1)),
    "C3D8": ((1, 2, 3, 4), (5, 8, 7, 6), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1)),
    "C3D6": ((1, 2, 3), (4, 6, 5), (1, 4, 5, 2), (2, 5, 6, 3), (3, 6, 4, 1)),
    "C3D4": ((1, 2, 3), (1, 4, 2), (2, 4, 3), (3, 4, 1)),
}


def load_curve():
    path = os.path.join(ROOT, "self_locking_force_displacement_curve.csv")
    rows = []
    with open(path, "r") as handle:
        for row in csv.DictReader(handle):
            rows.append({
                "time": float(row["total_time_s"]),
                "u3": float(row["rp_u3_mm"]),
                "rf3": float(row["rp_rf3_N"]),
            })
    return rows


def nearest_curve(rows, total_time):
    return min(rows, key=lambda r: abs(r["time"] - total_time)) if rows else {"u3": 0.0, "rf3": 0.0}


def exterior_faces(instance):
    face_map = {}
    owner = {}
    for elem in instance.elements:
        conn = elem.connectivity
        face_defs = SURFACE_FACES.get(elem.type)
        if not face_defs:
            continue
        for face in face_defs:
            labels = [conn[i - 1] for i in face]
            key = tuple(sorted(labels))
            if key in face_map:
                face_map[key] += 1
            else:
                face_map[key] = 1
                owner[key] = (elem.label, labels)
    faces = []
    label = 1
    for key, count in face_map.items():
        if count == 1:
            elem_label, labels = owner[key]
            faces.append({"label": label, "sourceElement": elem_label, "connectivity": labels, "type": "S%d" % len(labels)})
            label += 1
    return faces


def displacement_map(frame, instance):
    out = {}
    if "U" not in frame.fieldOutputs:
        return out
    for v in frame.fieldOutputs["U"].getSubset(region=instance, position=NODAL).values:
        out[v.nodeLabel] = [float(v.data[0]), float(v.data[1]), float(v.data[2])]
    return out


def stress_map(frame, instance):
    out = {}
    if "S" not in frame.fieldOutputs:
        return out
    for v in frame.fieldOutputs["S"].getSubset(region=instance, position=INTEGRATION_POINT).values:
        out[v.elementLabel] = max(out.get(v.elementLabel, 0.0), float(v.mises))
    return out


def strain_map(frame, instance):
    out = {}
    if "LE" not in frame.fieldOutputs:
        return out
    for v in frame.fieldOutputs["LE"].getSubset(region=instance, position=INTEGRATION_POINT).values:
        data = [float(x) for x in v.data]
        val = math.sqrt(sum(x * x for x in data))
        out[v.elementLabel] = max(out.get(v.elementLabel, 0.0), val)
    return out


def contact_pressure_map(frame, instance):
    key = None
    for name in frame.fieldOutputs.keys():
        if name.strip().startswith("CPRESS"):
            key = name
            break
    node_vals = {}
    if not key:
        return node_vals
    try:
        values = frame.fieldOutputs[key].getSubset(region=instance).values
    except Exception:
        values = frame.fieldOutputs[key].values
    for v in values:
        if hasattr(v, "nodeLabel") and v.nodeLabel:
            data = v.data
            if isinstance(data, float):
                node_vals[v.nodeLabel] = max(node_vals.get(v.nodeLabel, 0.0), float(data))
            else:
                node_vals[v.nodeLabel] = max(node_vals.get(v.nodeLabel, 0.0), float(data[0]))
    return node_vals


def tool_model(instance):
    nodes = [{"label": n.label, "coordinates": [float(x) for x in n.coordinates]} for n in instance.nodes]
    elements = []
    for elem in instance.elements:
        elements.append({
            "label": elem.label,
            "type": elem.type,
            "connectivity": list(elem.connectivity),
            "color": "#a7b0bd",
        })
    return {"nodes": nodes, "elements": elements}


def write_case(case_id, mode, base_nodes, faces, dynamic_frames, metadata, summary):
    case_dir = os.path.join(LOCAL_VIEWER, case_id)
    model_dir = os.path.join(VIEWER_MODELS, "self-locking-needle-%s" % case_id)
    for d in (case_dir, model_dir):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)

    result = {
        "schemaVersion": 1,
        "source": "self_locking_pullout_explicit.odb",
        "analysisType": "dynamic",
        "instance": "SKIN",
        "step": "Insertion + Holding + Pull_out",
        "deformationScale": 1.0,
        "elementType": "surface extracted from swept 3D skin mesh",
        "fieldLabel": mode["label"],
        "toolModel": metadata["toolModel"],
        "nodes": base_nodes,
        "elements": faces,
        "dynamicFrames": dynamic_frames,
        "fieldRanges": mode["ranges"],
    }
    project = {
        "schemaVersion": 1,
        "project": {
            "name": "Self-locking Needle %s" % mode["name"],
            "prompt": "Self-locking microneedle insertion, holding and pull-out in layered Ogden viscoelastic skin.",
            "solver": "Abaqus/Explicit",
            "model": "Rigid RP-controlled barbed/self-locking needle with annular layered skin coupon",
            "source": "self_locking_needle_pullout.py",
        },
        "connection": {"status": "complete", "message": "ODB exported to text-to-cae browser mesh format."},
        "inputs": {
            "geometry": {
                "needle": "Rigid self-locking needle reconstructed from STEP: shaft radius 0.100 mm, barb radius 0.1955 mm",
                "skin": "Annular 3.6 mm diameter x 2.0 mm layered skin coupon with pilot puncture path",
            },
            "material": {"skin": "Ogden hyperelastic + Prony viscoelastic layers", "needle": "Rigid body controlled by RP"},
            "loads": {"description": "Insertion, Holding, Pull-out RP displacement path", "contact": "Hard general contact, friction 0.38"},
            "summary": [
                {"label": {"en": "Max pull-out force", "zh": "最大拔出力"}, "value": {"en": "%.4f N" % summary["maximum_pullout_force_N"], "zh": "%.4f N" % summary["maximum_pullout_force_N"]}},
                {"label": {"en": "Retention force", "zh": "保持力"}, "value": {"en": "%.4f N" % summary["retention_force_N"], "zh": "%.4f N" % summary["retention_force_N"]}},
                {"label": {"en": "Frames", "zh": "帧数"}, "value": {"en": "%d exported frames" % len(dynamic_frames), "zh": "%d 个导出帧" % len(dynamic_frames)}},
            ],
        },
        "workflow": [
            {"step": "Clean", "status": "complete", "detail": "Old ordinary microneedle outputs removed."},
            {"step": "Build", "status": "complete", "detail": "Self-locking needle and skin rebuilt from STEP-derived dimensions."},
            {"step": "Solve", "status": "complete", "detail": "Insertion, holding, and pull-out Explicit analysis completed."},
            {"step": "Export", "status": "complete", "detail": "ODB frames and RF-U data exported for browser viewing."},
        ],
        "outputs": {"status": "complete", "job_name": "self_locking_pullout_explicit", "odb": "self_locking_pullout_explicit.odb"},
    }
    for d in (case_dir, model_dir):
        with open(os.path.join(d, "result_mesh.json"), "w") as handle:
            json.dump(result, handle)
        with open(os.path.join(d, "cae_project.json"), "w") as handle:
            json.dump(project, handle, indent=2)
        for fname in ("self_locking_force_displacement_curve.csv", "self_locking_energy_history.csv", "self_locking_postprocess_summary.json"):
            src = os.path.join(ROOT, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(d, fname))


def main():
    odb = openOdb(ODB_PATH, readOnly=True)
    try:
        skin = odb.rootAssembly.instances["SKIN"]
        needle = odb.rootAssembly.instances["NEEDLE"]
        faces = exterior_faces(skin)
        surface_node_labels = sorted({n for f in faces for n in f["connectivity"]})
        base_node_by_label = {n.label: n for n in skin.nodes}
        base_nodes = []
        for label in surface_node_labels:
            n = base_node_by_label[label]
            base_nodes.append({"label": label, "coordinates": [float(x) for x in n.coordinates], "displacement": [0.0, 0.0, 0.0], "deformed": [float(x) for x in n.coordinates], "visualOnly": False})

        curve = load_curve()
        summary = json.load(open(os.path.join(ROOT, "self_locking_postprocess_summary.json")))
        metadata = {"toolModel": tool_model(needle)}

        modes = {
            "stress": {"name": "stress", "label": "S, Mises (MPa)", "ranges": {"misesMin": 0.0, "misesMax": 0.0, "maxDisplacement": 0.0}},
            "strain": {"name": "strain", "label": "LE equivalent", "ranges": {"misesMin": 0.0, "misesMax": 0.0, "maxDisplacement": 0.0}},
            "displacement": {"name": "displacement", "label": "U magnitude (mm)", "ranges": {"misesMin": 0.0, "misesMax": 0.0, "maxDisplacement": 0.0}},
            "contact-pressure": {"name": "contact pressure", "label": "CPRESS (MPa)", "ranges": {"misesMin": 0.0, "misesMax": 0.0, "maxDisplacement": 0.0}},
        }
        frames_by_mode = {key: [] for key in modes}

        total_offset = 0.0
        frame_number = 0
        for step_name in ("Insertion", "Holding", "Pull_out"):
            step = odb.steps[step_name]
            for idx, frame in enumerate(step.frames):
                if idx % 2 != 0 and idx != len(step.frames) - 1:
                    continue
                total_time = total_offset + float(frame.frameValue)
                u = displacement_map(frame, skin)
                s = stress_map(frame, skin)
                le = strain_map(frame, skin)
                cp = contact_pressure_map(frame, skin)
                curve_row = nearest_curve(curve, total_time)
                frame_nodes = []
                disp_mag = {}
                max_disp = 0.0
                for node in base_nodes:
                    label = node["label"]
                    du = u.get(label, [0.0, 0.0, 0.0])
                    mag = math.sqrt(sum(x * x for x in du))
                    disp_mag[label] = mag
                    max_disp = max(max_disp, mag)
                    xyz = node["coordinates"]
                    frame_nodes.append({
                        "label": label,
                        "coordinates": xyz,
                        "displacement": du,
                        "deformed": [xyz[0] + du[0], xyz[1] + du[1], xyz[2] + du[2]],
                        "visualOnly": False,
                    })
                per_mode_elements = {key: [] for key in modes}
                for face in faces:
                    elem_label = face["sourceElement"]
                    labels = face["connectivity"]
                    values = {
                        "stress": s.get(elem_label, 0.0),
                        "strain": le.get(elem_label, 0.0),
                        "displacement": sum(disp_mag.get(n, 0.0) for n in labels) / float(len(labels)),
                        "contact-pressure": sum(cp.get(n, 0.0) for n in labels) / float(len(labels)),
                    }
                    for key, val in values.items():
                        per_mode_elements[key].append({"label": face["label"], "type": face["type"], "connectivity": labels, "mises": float(val)})
                        modes[key]["ranges"]["misesMax"] = max(modes[key]["ranges"]["misesMax"], float(val))
                        modes[key]["ranges"]["maxDisplacement"] = max(modes[key]["ranges"]["maxDisplacement"], max_disp)
                for key in modes:
                    frames_by_mode[key].append({
                        "frame": frame_number,
                        "timeMs": total_time * 1000.0,
                        "nodes": frame_nodes,
                        "elements": per_mode_elements[key],
                        "toolPose": {"x": 0.0, "y": 0.0, "z": curve_row["u3"], "angleRad": 0.0},
                        "contact": {
                            "indentationMm": max(0.0, -curve_row["u3"]),
                            "forceN": curve_row["rf3"],
                            "phase": step_name,
                        },
                        "fieldRanges": modes[key]["ranges"],
                        "fieldLabel": modes[key]["label"],
                    })
                frame_number += 1
            total_offset += step.timePeriod

        if os.path.isdir(LOCAL_VIEWER):
            shutil.rmtree(LOCAL_VIEWER)
        os.makedirs(LOCAL_VIEWER)
        index = {}
        for key, mode in modes.items():
            write_case(key, mode, base_nodes, faces, frames_by_mode[key], metadata, summary)
            index[key] = "models/self-locking-needle-%s" % key
        with open(os.path.join(LOCAL_VIEWER, "viewer_index.json"), "w") as handle:
            json.dump(index, handle, indent=2)
        print(json.dumps({"exported": index, "surface_nodes": len(base_nodes), "surface_faces": len(faces), "frames": frame_number}, indent=2))
    finally:
        odb.close()


if __name__ == "__main__":
    main()
