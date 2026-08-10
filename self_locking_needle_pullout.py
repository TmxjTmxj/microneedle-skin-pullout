from __future__ import print_function

import json
import math
import os
import re
import shutil
import sys
import time

from abaqus import *
from abaqusConstants import *
import interaction
import mesh
import regionToolset
import step


ROOT = os.path.abspath(os.getcwd())
JOB_NAME = "self_locking_pullout_explicit"
MODEL_NAME = "SelfLockingNeedlePullout"


def read_step_metadata(path):
    text = open(path, "rb").read().decode("utf-8", "ignore")
    unit = "unknown"
    if ".MICRO." in text:
        unit = "um"
    elif ".MILLI." in text:
        unit = "mm"
    pts = []
    pattern = r"CARTESIAN_POINT\s*\([^,]*,\s*\(([^)]*)\)\s*\)"
    for match in re.finditer(pattern, text):
        vals = []
        for token in re.findall(r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?", match.group(1)):
            vals.append(float(token.replace("D", "E")))
        if len(vals) == 3:
            pts.append(vals)
    if not pts:
        return {"file": os.path.basename(path), "unit": unit, "point_count": 0}
    low = [min(p[i] for p in pts) for i in range(3)]
    high = [max(p[i] for p in pts) for i in range(3)]
    size = [high[i] - low[i] for i in range(3)]
    return {
        "file": os.path.basename(path),
        "unit": unit,
        "point_count": len(pts),
        "low": low,
        "high": high,
        "size": size,
    }


def safe_remove_job_files(job_name):
    for ext in (
        ".abq", ".com", ".dat", ".fil", ".inp", ".lck", ".log", ".mdl",
        ".msg", ".odb", ".pac", ".prt", ".res", ".sel", ".sim", ".sta",
        ".stt",
    ):
        path = os.path.join(ROOT, job_name + ext)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    for suffix in (".msg.1", ".msg.2", ".msg.3", ".msg.4", ".cid", ".simlog"):
        path = os.path.join(ROOT, job_name + suffix)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    simdir = os.path.join(ROOT, job_name + ".simdir")
    if os.path.isdir(simdir):
        shutil.rmtree(simdir, ignore_errors=True)


def make_material(model, name, mu, alpha, d1, prony):
    mat = model.Material(name=name)
    mat.Density(table=((1.10e-9,),))
    # Literature-level engineering estimates for hydrated skin layers in MPa.
    # Ogden N=1 captures large elastic stretch; Prony terms capture relaxation.
    mat.Hyperelastic(
        materialType=ISOTROPIC,
        testData=OFF,
        type=OGDEN,
        n=1,
        table=((mu, alpha, d1),),
    )
    mat.Viscoelastic(domain=TIME, time=PRONY, table=(prony,))
    return mat


def make_skin_part(model, metadata):
    skin_radius = 1.8
    skin_t = 2.0
    pilot_r = 0.112

    sketch = model.ConstrainedSketch(name="skin_annulus_profile", sheetSize=10.0)
    sketch.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(skin_radius, 0.0))
    sketch.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(pilot_r, 0.0))

    part = model.Part(name="SKIN_ANNULAR_COUPON", dimensionality=THREE_D, type=DEFORMABLE_BODY)
    part.BaseSolidExtrude(sketch=sketch, depth=skin_t)
    del model.sketches["skin_annulus_profile"]

    # Partition into hypodermis, dermis and epidermis through the thickness.
    for z in (0.50, 1.92):
        datum = part.DatumPlaneByPrincipalPlane(principalPlane=XYPLANE, offset=z)
        part.PartitionCellByDatumPlane(datumPlane=part.datums[datum.id], cells=part.cells[:])

    part.Set(cells=part.cells.getByBoundingBox(zMin=1.919, zMax=2.001), name="EPIDERMIS_CELLS")
    part.Set(cells=part.cells.getByBoundingBox(zMin=0.499, zMax=1.921), name="DERMIS_CELLS")
    part.Set(cells=part.cells.getByBoundingBox(zMin=-0.001, zMax=0.501), name="HYPODERMIS_CELLS")

    part.SectionAssignment(region=part.sets["EPIDERMIS_CELLS"], sectionName="EPIDERMIS_SECTION")
    part.SectionAssignment(region=part.sets["DERMIS_CELLS"], sectionName="DERMIS_SECTION")
    part.SectionAssignment(region=part.sets["HYPODERMIS_CELLS"], sectionName="HYPODERMIS_SECTION")

    part.seedPart(size=0.22, deviationFactor=0.08, minSizeFactor=0.08)
    fine_edges = []
    for edge in part.edges:
        x, y, z = edge.pointOn[0]
        r = math.sqrt(x * x + y * y)
        if r < 0.20:
            fine_edges.append(edge)
    if fine_edges:
        part.seedEdgeBySize(edges=fine_edges, size=0.055, deviationFactor=0.05, minSizeFactor=0.05, constraint=FINER)
    try:
        part.setMeshControls(regions=part.cells[:], elemShape=HEX, technique=SWEEP)
        elems = (
            mesh.ElemType(elemCode=C3D8R, elemLibrary=EXPLICIT, secondOrderAccuracy=OFF, hourglassControl=DEFAULT),
            mesh.ElemType(elemCode=C3D6, elemLibrary=EXPLICIT),
            mesh.ElemType(elemCode=C3D4, elemLibrary=EXPLICIT),
        )
    except Exception:
        part.setMeshControls(regions=part.cells[:], elemShape=TET, technique=FREE)
        elems = (mesh.ElemType(elemCode=C3D4, elemLibrary=EXPLICIT),)
    part.setElementType(regions=(part.cells[:],), elemTypes=elems)
    part.generateMesh()

    part.Set(faces=part.faces.getByBoundingBox(zMin=-0.001, zMax=0.001), name="BOTTOM_FACE")

    metadata["skin_model"] = {
        "source": "skin.STEP",
        "source_unit": "mm",
        "source_size_mm": metadata["step_files"]["skin.STEP"]["size"],
        "rebuilt_coupon": "annular cylindrical coupon",
        "rebuilt_coupon_mm": [2.0 * skin_radius, 2.0 * skin_radius, skin_t],
        "pilot_channel_radius_mm": pilot_r,
        "reason_for_channel": "Represents the puncture path made by the sharp tip so the pull-out/self-locking response can be isolated.",
        "mesh_strategy": "Swept reduced-integration solid mesh where Abaqus permits; local fine seeding around the puncture channel.",
    }
    return part


def make_needle_part(model, metadata):
    # Dimensions decoded from Self-locking needle.STEP:
    # shaft radius 100 um, barb radius 195.5 um, conical lead length about 730 um.
    shaft_r = 0.100
    barb_r = 0.1955
    lead_len = 0.730
    shaft_len = 6.0

    sketch = model.ConstrainedSketch(name="self_locking_needle_profile", sheetSize=12.0)
    sketch.ConstructionLine(point1=(0.0, -0.2), point2=(0.0, shaft_len + 0.4))
    sketch.Line(point1=(0.0, 0.0), point2=(barb_r, lead_len))
    sketch.Line(point1=(barb_r, lead_len), point2=(shaft_r, lead_len))
    sketch.Line(point1=(shaft_r, lead_len), point2=(shaft_r, shaft_len))
    sketch.Line(point1=(shaft_r, shaft_len), point2=(0.0, shaft_len))
    sketch.Line(point1=(0.0, shaft_len), point2=(0.0, 0.0))

    part = model.Part(name="SELF_LOCKING_NEEDLE_RIGID", dimensionality=THREE_D, type=DEFORMABLE_BODY)
    part.BaseSolidRevolve(sketch=sketch, angle=360.0, flipRevolveDirection=OFF)
    del model.sketches["self_locking_needle_profile"]

    mat = model.Material(name="SOLIDIFIED_HYALURONIC_ACID_DUMMY")
    # The needle is constrained as a rigid body; these elastic values only satisfy section requirements.
    mat.Density(table=((1.35e-9,),))
    mat.Elastic(table=((2500.0, 0.35),))
    model.HomogeneousSolidSection(name="NEEDLE_DUMMY_SECTION", material=mat.name, thickness=None)
    part.Set(cells=part.cells[:], name="NEEDLE_CELLS")
    part.SectionAssignment(region=part.sets["NEEDLE_CELLS"], sectionName="NEEDLE_DUMMY_SECTION")

    part.seedPart(size=0.055, deviationFactor=0.08, minSizeFactor=0.08)
    part.setMeshControls(regions=part.cells[:], elemShape=TET, technique=FREE)
    elem = mesh.ElemType(elemCode=C3D4, elemLibrary=EXPLICIT)
    part.setElementType(regions=(part.cells[:],), elemTypes=(elem,))
    part.generateMesh()

    metadata["needle_model"] = {
        "source": "Self-locking needle.STEP",
        "source_unit": "um",
        "source_size": metadata["step_files"]["Self-locking needle.STEP"]["size"],
        "axis": "STEP Y axis reconstructed as model Z axis after assembly rotation",
        "shaft_radius_mm": shaft_r,
        "barb_radius_mm": barb_r,
        "barb_overhang_mm": barb_r - shaft_r,
        "lead_cone_length_mm": lead_len,
        "rear_locking_feature": "Flat annular shoulder from barb radius to shaft radius; pull-out is opposite the easy insertion cone.",
        "modeling": "Meshed solid contact shape constrained as rigid body to RP",
    }
    return part


def build_model():
    safe_remove_job_files(JOB_NAME)

    for name in list(mdb.models.keys()):
        if name != "Model-1":
            del mdb.models[name]
    model = mdb.Model(name=MODEL_NAME)
    if "Model-1" in mdb.models:
        del mdb.models["Model-1"]

    metadata = {
        "unit_system": "mm-N-s-tonne-MPa",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "step_files": {},
    }
    for fname in ("Self-locking needle.STEP", "skin.STEP"):
        metadata["step_files"][fname] = read_step_metadata(os.path.join(ROOT, fname))

    make_material(model, "EPIDERMIS_OGDEN_VISCO", 0.075, 8.5, 1.50, (0.12, 0.02, 0.020))
    make_material(model, "DERMIS_OGDEN_VISCO", 0.030, 7.5, 0.80, (0.18, 0.04, 0.050))
    make_material(model, "HYPODERMIS_OGDEN_VISCO", 0.006, 6.0, 8.00, (0.22, 0.06, 0.100))
    model.HomogeneousSolidSection(name="EPIDERMIS_SECTION", material="EPIDERMIS_OGDEN_VISCO", thickness=None)
    model.HomogeneousSolidSection(name="DERMIS_SECTION", material="DERMIS_OGDEN_VISCO", thickness=None)
    model.HomogeneousSolidSection(name="HYPODERMIS_SECTION", material="HYPODERMIS_OGDEN_VISCO", thickness=None)

    skin = make_skin_part(model, metadata)
    needle = make_needle_part(model, metadata)

    asm = model.rootAssembly
    asm.DatumCsysByDefault(CARTESIAN)
    skin_inst = asm.Instance(name="SKIN", part=skin, dependent=ON)
    needle_inst = asm.Instance(name="NEEDLE", part=needle, dependent=ON)

    # Revolve axis is sketch Y; rotate to model Z so the needle moves through the skin thickness.
    asm.rotate(instanceList=("NEEDLE",), axisPoint=(0.0, 0.0, 0.0), axisDirection=(1.0, 0.0, 0.0), angle=90.0)
    # Tip starts 0.05 mm above the skin top surface at z=2.0.
    asm.translate(instanceList=("NEEDLE",), vector=(0.0, 0.0, 2.05))

    rp = asm.ReferencePoint(point=(0.0, 0.0, 8.20))
    rp_region = regionToolset.Region(referencePoints=(asm.referencePoints[rp.id],))
    asm.Set(referencePoints=(asm.referencePoints[rp.id],), name="NEEDLE_RP")
    asm.Set(elements=needle_inst.elements[:], name="NEEDLE_RIGID_ELEMENTS")
    body_region = regionToolset.Region(elements=needle_inst.elements[:])
    model.RigidBody(name="NEEDLE_RIGID_BODY", refPointRegion=rp_region, bodyRegion=body_region)

    # Boundary conditions on skin: bottom fixed, lateral faces constrained in their normal direction.
    model.EncastreBC(name="SKIN_BOTTOM_FIXED", createStepName="Initial", region=skin_inst.sets["BOTTOM_FACE"])

    # Three physical stages: insertion, relaxation/holding, pull-out.
    insertion_t = 0.10
    holding_t = 0.18
    pullout_t = 0.16
    insertion_depth = 1.35
    final_retract = 0.20
    mass_scaling = ((SEMI_AUTOMATIC, MODEL, AT_BEGINNING, 0.0, 1.0e-6, BELOW_MIN, 0, 0, 0.0, 0.0, 0, None),)
    model.ExplicitDynamicsStep(
        name="Insertion",
        previous="Initial",
        timePeriod=insertion_t,
        improvedDtMethod=ON,
        massScaling=mass_scaling,
    )
    model.ExplicitDynamicsStep(
        name="Holding",
        previous="Insertion",
        timePeriod=holding_t,
        improvedDtMethod=ON,
        massScaling=mass_scaling,
    )
    model.ExplicitDynamicsStep(
        name="Pull_out",
        previous="Holding",
        timePeriod=pullout_t,
        improvedDtMethod=ON,
        massScaling=mass_scaling,
    )

    total_path = (
        (0.0, 0.0),
        (0.02, -0.08),
        (insertion_t, -insertion_depth),
        (insertion_t + holding_t, -insertion_depth),
        (insertion_t + holding_t + pullout_t, final_retract),
    )
    model.SmoothStepAmplitude(name="RP_TOTAL_Z_PATH", timeSpan=TOTAL, data=total_path)
    model.DisplacementBC(
        name="NEEDLE_RP_GUIDE",
        createStepName="Initial",
        region=rp_region,
        u1=0.0,
        u2=0.0,
        ur1=0.0,
        ur2=0.0,
        ur3=0.0,
    )
    model.DisplacementBC(
        name="NEEDLE_RP_Z_PATH",
        createStepName="Insertion",
        region=rp_region,
        u3=1.0,
        amplitude="RP_TOTAL_Z_PATH",
    )

    prop = model.ContactProperty("BARB_SKIN_HARD_FRICTION")
    prop.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON, constraintEnforcementMethod=DEFAULT)
    prop.TangentialBehavior(
        formulation=PENALTY,
        directionality=ISOTROPIC,
        slipRateDependency=OFF,
        pressureDependency=OFF,
        temperatureDependency=OFF,
        dependencies=0,
        table=((0.38,),),
        shearStressLimit=None,
        maximumElasticSlip=FRACTION,
        fraction=0.005,
        elasticSlipStiffness=None,
    )
    contact = model.ContactExp(name="GENERAL_CONTACT_NEEDLE_SKIN", createStepName="Insertion")
    contact.includedPairs.setValuesInStep(stepName="Insertion", useAllstar=ON)
    contact.contactPropertyAssignments.appendInStep(stepName="Insertion", assignments=((GLOBAL, SELF, "BARB_SKIN_HARD_FRICTION"),))

    # Output: keep ODB useful for browser animation but not unnecessarily huge.
    model.fieldOutputRequests["F-Output-1"].setValues(
        variables=("U", "S", "LE", "CSTRESS", "CSTATUS"),
        numIntervals=45,
    )
    model.historyOutputRequests["H-Output-1"].setValues(
        variables=("ALLKE", "ALLIE", "ALLAE", "ALLVD"),
        numIntervals=120,
    )
    model.HistoryOutputRequest(
        name="RP_FORCE_DISPLACEMENT",
        createStepName="Insertion",
        variables=("U3", "RF3"),
        region=rp_region,
        numIntervals=160,
    )

    metadata["analysis"] = {
        "type": "Abaqus/Explicit",
        "steps": [
            {"name": "Insertion", "time_s": insertion_t, "target_rp_u3_mm": -insertion_depth},
            {"name": "Holding", "time_s": holding_t, "target_rp_u3_mm": -insertion_depth},
            {"name": "Pull_out", "time_s": pullout_t, "target_rp_u3_mm": final_retract},
        ],
        "contact": {"normal": "Hard Contact", "tangential_friction": 0.38, "sliding": "finite/general contact"},
        "field_outputs": ["U", "S", "LE", "CSTRESS/CPRESS", "CSTATUS"],
        "history_outputs": ["RP U3", "RP RF3", "ALLKE", "ALLIE", "ALLAE", "ALLVD"],
    }
    metadata["mesh"] = {
        "skin_nodes": len(skin.nodes),
        "skin_elements": len(skin.elements),
        "needle_nodes": len(needle.nodes),
        "needle_elements": len(needle.elements),
        "skin_global_seed_mm": 0.22,
        "skin_channel_seed_mm": 0.055,
        "needle_seed_mm": 0.055,
        "explicit_mass_scaling_target_dt_s": 1.0e-6,
    }

    cae_path = os.path.join(ROOT, "self_locking_pullout_model.cae")
    mdb.saveAs(pathName=cae_path)
    with open(os.path.join(ROOT, "self_locking_model_metadata.json"), "w") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    job = mdb.Job(
        name=JOB_NAME,
        model=MODEL_NAME,
        description="Self-locking rigid microneedle insertion-hold-pullout in layered Ogden viscoelastic skin",
        type=ANALYSIS,
        explicitPrecision=SINGLE,
        nodalOutputPrecision=SINGLE,
        multiprocessingMode=DEFAULT,
        numCpus=4,
        numDomains=4,
        memory=85,
        memoryUnits=PERCENTAGE,
    )
    job.writeInput()
    submit_from_cae = os.environ.get("SELF_LOCKING_SUBMIT_FROM_CAE", "").strip() == "1"
    if submit_from_cae:
        print("SUBMITTING_JOB_FROM_CAE %s" % JOB_NAME)
        job.submit(consistencyChecking=OFF)
        job.waitForCompletion()
    else:
        print("BUILD_ONLY_DONE %s.inp" % JOB_NAME)
        print("Run solve with: powershell -ExecutionPolicy Bypass -File run_self_locking_solve.ps1")
    mdb.save()
    print("CAE_SCRIPT_COMPLETE %s" % JOB_NAME)


if __name__ == "__main__":
    build_model()
