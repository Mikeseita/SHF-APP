"""
Digitized reading engine for "Minimum Control Speed on the Ground - Short Field
Takeoff" chart. Single panel: Ambient Temperature (X) vs Indicated Airspeed (Y),
family of Airfield Pressure Altitude curves.
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "vmcg_calibration.json"), encoding="utf-8") as f:
    CAL = json.load(f)


def _line_y(curve, x):
    return curve["m"] * x + curve["b"]


def temp_to_x(cal, temp_c):
    ax = cal["temp_axis"]
    return ax["x_at_neg40"] + ax["px_per_degC"] * (temp_c + 40)


def y_to_speed(cal, y):
    ax = cal["speed_axis"]
    return 95 - (y - ax["y_at_95kt"]) / ax["px_per_kt"]


def apply_width_correction(vmcg_kt, runway_width_ft):
    ref = 77
    if runway_width_ft is None or runway_width_ft >= ref:
        return vmcg_kt, 0.0
    if runway_width_ft >= 67:
        delta = (ref - runway_width_ft) / 2.0 * 1.0
        return vmcg_kt + delta, delta
    delta = (ref - 67) / 2.0 * 1.0 + 5.0 + (67 - runway_width_ft) / 2.0 * 3.0
    return vmcg_kt + delta, delta


def compute_vmcg(chart_key, elevation_ft, temp_c, runway_width_ft=None):
    cal = CAL[chart_key]
    x_temp = temp_to_x(cal, temp_c)

    curves = cal["curves"]
    keys = cal["curve_order_kft"]
    curve_labels = cal["curve_labels"]
    labels_by_key = dict(zip(keys, curve_labels))
    elev_kft = elevation_ft / 1000.0

    if elev_kft <= keys[0]:
        k0, k1 = keys[0], keys[1]
    elif elev_kft >= keys[-1]:
        k0, k1 = keys[-2], keys[-1]
    else:
        idx = bisect.bisect_right(keys, elev_kft)
        k0, k1 = keys[idx - 1], keys[idx]

    y0 = _line_y(curves[labels_by_key[k0]], x_temp)
    y1 = _line_y(curves[labels_by_key[k1]], x_temp)
    frac = (elev_kft - k0) / (k1 - k0) if k1 != k0 else 0.0
    y_final = y0 + frac * (y1 - y0)

    vmcg_base = y_to_speed(cal, y_final)
    vmcg_corrected, width_delta = apply_width_correction(vmcg_base, runway_width_ft)

    return {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "runway_width_ft": runway_width_ft,
        "x_temp": x_temp,
        "y_final": y_final,
        "bracket": [k0, k1],
        "frac": frac,
        "vmcg_base_kt": round(vmcg_base, 1),
        "width_correction_kt": round(width_delta, 1),
        "vmcg_kt": round(vmcg_corrected, 1),
    }


def draw_overlay(chart_key, construction, out_path, axis_left=382, axis_bottom=1376):
    cal = CAL[chart_key]
    img_path = os.path.join(IMG_ROOT, cal["source_image"])
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    RED = (230, 0, 0)
    W = 3

    c = construction
    x_temp = c["x_temp"]
    y_final = c["y_final"]

    draw.line([(x_temp, axis_bottom), (x_temp, y_final)], fill=RED, width=W)
    draw.line([(axis_left, y_final), (x_temp, y_final)], fill=RED, width=W)

    for (px, py) in [(x_temp, axis_bottom), (x_temp, y_final), (axis_left, y_final)]:
        draw.ellipse([px - 4, py - 4, px + 4, py + 4], outline=RED, width=2)

    img.save(out_path)
    return out_path
