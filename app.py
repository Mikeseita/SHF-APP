import json
import os
import sys

from flask import Flask, jsonify, request, render_template, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chart_engine import mtow_chart, vmcg_chart, weight_panel_chart as wpc, fig327_chart as f327, vmca_chart, fig331_chart as f331, v2norm_chart as v2n, vref_chart as vref

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AIRPORTS_FILE = os.path.join(DATA_DIR, "airports.json")
OVERLAY_DIR = os.path.join(BASE_DIR, "static", "overlays")
os.makedirs(OVERLAY_DIR, exist_ok=True)

app = Flask(__name__)

WEIGHTS = [19000, 18500, 18000, 17500, 17000, 16500, 16000]
TEMPS = [30, 20, 10]
GRADIENT_REQUIRED = 2.3
DRAG_INDEX_FIXED = 18

CFL_CAL = wpc.load_cal("cfl_calibration.json")["cfl_flap10_shf_to"]
TORUN_CAL = wpc.load_cal("torun_calibration.json")["torun_flap10_shf_to"]
CFL_POINTS = wpc.load_cal("cfl_panel2_points.json")
TORUN_POINTS = wpc.load_cal("torun_panel2_points.json")
CFL_TORUN_ENABLED = True

FIG327_CHART_KEY = "vre_vcef_flap10_shf_to"
FIG327_ENABLED = True

VMCA_CHART_KEY = "vmca_flap10_shf_to"
VMCA_ENABLED = True

FIG331_CHART_KEY = "vr_v2_flap10_shf_to"
FIG331_ENABLED = True

V2NORM_CHART_KEY = "v2norm_flap10_normal_to"
V2NORM_ENABLED = True

VREF_CHART_KEY = "vref_flap23_shf_ldg"
VREF_ENABLED = True

LDGROLL_CAL = wpc.load_cal("ldgroll_calibration.json")["ldgroll_flap23_shf_ldg"]
LDGROLL_POINTS = wpc.load_cal("ldgroll_panel2_points.json")
LDGROLL_ENABLED = True


def load_airports():
    if not os.path.exists(AIRPORTS_FILE):
        return {}
    with open(AIRPORTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_airports(data):
    with open(AIRPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/airports", methods=["GET"])
def get_airports():
    return jsonify(load_airports())


@app.route("/api/airports", methods=["POST"])
def upsert_airport():
    payload = request.get_json(force=True)
    icao = payload.get("icao", "").strip().upper()
    if not icao:
        return jsonify({"error": "ICAO em falta"}), 400
    airports = load_airports()
    airports[icao] = {
        "rwy": payload.get("rwy", ""),
        "elev_ft": float(payload.get("elev_ft", 0)),
        "ldg_ft": float(payload.get("ldg_ft", 0)) if payload.get("ldg_ft") not in (None, "") else None,
        "to_ft": float(payload.get("to_ft", 0)) if payload.get("to_ft") not in (None, "") else None,
        "width_ft": float(payload.get("width_ft", 0)) if payload.get("width_ft") not in (None, "") else None,
    }
    save_airports(airports)
    return jsonify(airports[icao])


@app.route("/api/airports/<icao>", methods=["DELETE"])
def delete_airport(icao):
    airports = load_airports()
    airports.pop(icao.upper(), None)
    save_airports(airports)
    return jsonify({"ok": True})


@app.route("/api/compute", methods=["POST"])
def compute():
    payload = request.get_json(force=True)
    icao = payload.get("icao", "").strip().upper()
    flap = payload.get("flap", 10)

    airports = load_airports()
    if icao not in airports:
        return jsonify({"error": f"Aeródromo {icao} não encontrado na base de dados"}), 404
    ap = airports[icao]
    elevation_ft = ap["elev_ft"]

    chart_key = "mtow_1eng_flap10_shf_to" if flap == 10 else None
    if chart_key is None or chart_key not in mtow_chart.CAL:
        return jsonify({"error": f"Ainda não tenho os gráficos digitalizados para Flap {flap}"}), 400

    runway_width_ft = ap.get("width_ft")
    runway_to_ft = ap.get("to_ft")

    results = {}
    for temp in TEMPS:
        construction = mtow_chart.compute_mtow_1eng(
            chart_key, elevation_ft, temp, GRADIENT_REQUIRED, DRAG_INDEX_FIXED
        )
        overlay_name = f"{icao}_{flap}_{temp}_mtow.png"
        overlay_path = os.path.join(OVERLAY_DIR, overlay_name)
        mtow_chart.draw_overlay(chart_key, construction, overlay_path)
        mtow_val = construction["weight_kg"]

        vmcg_c = vmcg_chart.compute_vmcg("vmcg_flap10_shf_to", elevation_ft, temp, runway_width_ft)
        vmcg_overlay_name = f"{icao}_{flap}_{temp}_vmcg.png"
        vmcg_chart.draw_overlay("vmcg_flap10_shf_to", vmcg_c, os.path.join(OVERLAY_DIR, vmcg_overlay_name))

        rows = []
        for w in WEIGHTS:
            row = {
                "weight": w,
                "exceeds_mtow": w > mtow_val,
                "cfl_kft": None,
                "cfl_overlay_url": None,
                "to_run_kft": None,
                "to_run_overlay_url": None,
                "vcef": None,
                "vcef_overlay_url": None,
                "vre_overlay_url": None,
                "vmca_overlay_url": None,
                "ldg_roll": None,
                "ldg_roll_overlay_url": None,
                "v1": None,
                "vre": None,
                "vmca": None,
                "vr": None,
                "vr_overlay_url": None,
                "v2shf": None,
                "v2shf_overlay_url": None,
                "vref_shf": None,
                "vref_shf_overlay_url": None,
                "v2norm": None,
                "v2norm_overlay_url": None,
                "v2shf_17": None,
            }
            if CFL_TORUN_ENABLED:
                cfl_c = wpc.compute_value(CFL_CAL, elevation_ft, temp, w, panel2_points=CFL_POINTS)
                cfl_overlay_name = f"{icao}_{flap}_{temp}_{w}_cfl.png"
                wpc.draw_overlay(CFL_CAL, cfl_c, CFL_CAL["source_image"], os.path.join(OVERLAY_DIR, cfl_overlay_name))

                torun_c = wpc.compute_value(TORUN_CAL, elevation_ft, temp, w, panel2_points=TORUN_POINTS)
                torun_overlay_name = f"{icao}_{flap}_{temp}_{w}_torun.png"
                wpc.draw_overlay(TORUN_CAL, torun_c, TORUN_CAL["source_image"], os.path.join(OVERLAY_DIR, torun_overlay_name))

                row["cfl_kft"] = cfl_c["value"] if cfl_c["reliable"] else None
                row["cfl_overlay_url"] = f"/static/overlays/{cfl_overlay_name}"
                row["cfl_reliable"] = cfl_c["reliable"]
                row["to_run_kft"] = torun_c["value"] if torun_c["reliable"] else None
                row["to_run_overlay_url"] = f"/static/overlays/{torun_overlay_name}"
                row["to_run_reliable"] = torun_c["reliable"]

            if FIG327_ENABLED and runway_to_ft:
                vre_c = f327.compute_speed(
                    FIG327_CHART_KEY, elevation_ft, temp, w, runway_to_ft / 1000.0
                )
                vre_overlay_name = f"{icao}_{flap}_{temp}_{w}_vre.png"
                f327.draw_overlay(FIG327_CHART_KEY, vre_c, os.path.join(OVERLAY_DIR, vre_overlay_name))
                row["vre"] = vre_c["speed_label"]
                row["vre_overlay_url"] = f"/static/overlays/{vre_overlay_name}"

                if row["cfl_kft"]:
                    vcef_c = f327.compute_speed(
                        FIG327_CHART_KEY, elevation_ft, temp, w, row["cfl_kft"]
                    )
                    vcef_overlay_name = f"{icao}_{flap}_{temp}_{w}_vcef.png"
                    f327.draw_overlay(FIG327_CHART_KEY, vcef_c, os.path.join(OVERLAY_DIR, vcef_overlay_name))
                    row["vcef"] = vcef_c["speed_label"]
                    row["vcef_overlay_url"] = f"/static/overlays/{vcef_overlay_name}"

            if VMCA_ENABLED:
                vmca_c = vmca_chart.compute_vmca(VMCA_CHART_KEY, elevation_ft, temp, w)
                vmca_overlay_name = f"{icao}_{flap}_{temp}_{w}_vmca.png"
                vmca_chart.draw_overlay(VMCA_CHART_KEY, vmca_c, os.path.join(OVERLAY_DIR, vmca_overlay_name))
                row["vmca"] = vmca_c["vmca_kt"]
                row["vmca_overlay_url"] = f"/static/overlays/{vmca_overlay_name}"

            if FIG331_ENABLED:
                f331_c = f331.compute_vr_v2(FIG331_CHART_KEY, elevation_ft, temp, w)
                f331_overlay_name = f"{icao}_{flap}_{temp}_{w}_vrv2.png"
                f331.draw_overlay(FIG331_CHART_KEY, f331_c, os.path.join(OVERLAY_DIR, f331_overlay_name))
                row["vr"] = f331_c["vr_final_kt"]
                row["vr_overlay_url"] = f"/static/overlays/{f331_overlay_name}"
                row["v2shf"] = f331_c["v2_final_kt"]
                row["v2shf_overlay_url"] = f"/static/overlays/{f331_overlay_name}"
                row["v2shf_17"] = round(f331_c["v2_final_kt"] + 17, 1)

            if V2NORM_ENABLED:
                v2n_c = v2n.compute_v2norm(V2NORM_CHART_KEY, elevation_ft, temp, w)
                v2n_overlay_name = f"{icao}_{flap}_{temp}_{w}_v2norm.png"
                v2n.draw_overlay(V2NORM_CHART_KEY, v2n_c, os.path.join(OVERLAY_DIR, v2n_overlay_name))
                row["v2norm"] = v2n_c["v2norm_final_kt"]
                row["v2norm_overlay_url"] = f"/static/overlays/{v2n_overlay_name}"

            if VREF_ENABLED:
                vref_c = vref.compute_vref(VREF_CHART_KEY, w)
                vref_overlay_name = f"{icao}_{flap}_{temp}_{w}_vref.png"
                vref.draw_overlay(VREF_CHART_KEY, vref_c, os.path.join(OVERLAY_DIR, vref_overlay_name))
                row["vref_shf"] = vref_c["vref_shf_kt"]
                row["vref_shf_overlay_url"] = f"/static/overlays/{vref_overlay_name}"

            if LDGROLL_ENABLED:
                ldgroll_c = wpc.compute_value(
                    LDGROLL_CAL, elevation_ft, temp, w,
                    panel2_points=LDGROLL_POINTS, y0_key="y_at_2_5kft", y0_value=2.5,
                )
                ldgroll_overlay_name = f"{icao}_{flap}_{temp}_{w}_ldgroll.png"
                wpc.draw_overlay(LDGROLL_CAL, ldgroll_c, LDGROLL_CAL["source_image"], os.path.join(OVERLAY_DIR, ldgroll_overlay_name))
                row["ldg_roll"] = round(ldgroll_c["value"] * 1000) if ldgroll_c["reliable"] else None
                row["ldg_roll_overlay_url"] = f"/static/overlays/{ldgroll_overlay_name}"
            rows.append(row)

        results[str(temp)] = {
            "mtow_1eng_kg": mtow_val,
            "construction": construction,
            "overlay_url": f"/static/overlays/{overlay_name}",
            "vmcg_kt": vmcg_c["vmcg_kt"],
            "vmcg_overlay_url": f"/static/overlays/{vmcg_overlay_name}",
            "rows": rows,
        }

    return jsonify({
        "icao": icao,
        "airport": ap,
        "flap": flap,
        "gradient_required": GRADIENT_REQUIRED,
        "drag_index": DRAG_INDEX_FIXED,
        "weights": WEIGHTS,
        "temps": TEMPS,
        "results": results,
        "cfl_torun_enabled": CFL_TORUN_ENABLED,
        "fig327_enabled": FIG327_ENABLED and bool(runway_to_ft),
        "vmca_enabled": VMCA_ENABLED,
        "fig331_enabled": FIG331_ENABLED,
        "v2norm_enabled": V2NORM_ENABLED,
        "vref_enabled": VREF_ENABLED,
        "ldgroll_enabled": LDGROLL_ENABLED,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5057, debug=True)
