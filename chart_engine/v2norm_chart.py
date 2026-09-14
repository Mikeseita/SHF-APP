"""
Digitized reading engine for the C-295M "Takeoff Speeds - Normal Takeoff"
chart (Figure 3-13): only the V2 and MIN V2 readings (V2norm column).
Vr for normal takeoff is intentionally not digitized here (deferred).

Per the manual's own "Use of Graphs (Figure 3-13)" text (PDM page 3-23):
Panel 1: Ambient Temperature (Y) vs Airfield Pressure Altitude curves
         (family -1/SL/2, same layout as the other charts) -> shared X.
Min V2 : shared X + weight curve family (12/14/16/18, heavily overlapping
         in the practical reading window) -> "the minimum value of V2".
V2     : weight only (single diagonal "BASELINE" line, no temp/altitude
         dependency) -> V2 at the nominal V2/VSR = 1.13 ratio. The printed
         chart also has a guideline fan to correct for other V2/VSR ratios
         (1.13-1.23); this app has no ratio input, so it always reads the
         BASELINE (1.13, "nominal V2 speed" per the manual).

Final V2norm = max(V2 baseline value, MIN V2 value).
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "v2norm_calibration.json"), encoding="utf-8") as f:
    CAL = json.load(f)

_PANEL1_POINTS_CACHE = {}


def _panel1_points(cal):
    fname = cal["panel1_points_file"]
    if fname not in _PANEL1_POINTS_CACHE:
        with open(os.path.join(DATA_DIR, fname), encoding="utf-8") as f:
            _PANEL1_POINTS_CACHE[fname] = json.load(f)
    return _PANEL1_POINTS_CACHE[fname]


def _x_for_y_on_piecewise(points, y_target):
    n = len(points)
    if y_target <= points[0][0]:
        return points[0][1]
    if y_target >= points[-1][0]:
        return points[-1][1]
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= y_target:
            lo = mid
        else:
            hi = mid
    y0, x0 = points[lo]
    y1, x1 = points[hi]
    frac = (y_target - y0) / (y1 - y0) if y1 != y0 else 0.0
    return x0 + frac * (x1 - x0)


def temp_to_y(cal, temp_c):
    ax = cal["panel1_temp_axis"]
    return ax["y_at_m40C"] + ax["px_per_degC"] * (temp_c + 40)


def _panel1_x(cal, elevation_ft, temp_c):
    y_temp = temp_to_y(cal, temp_c)
    p1points = _panel1_points(cal)
    p1keys = cal["panel1_curve_order_kft"]
    p1labels = cal["panel1_curve_labels"]
    labels_by_key = dict(zip(p1keys, p1labels))
    elev_kft = elevation_ft / 1000.0
    if elev_kft <= p1keys[0]:
        k0, k1 = p1keys[0], p1keys[1]
    elif elev_kft >= p1keys[-1]:
        k0, k1 = p1keys[-2], p1keys[-1]
    else:
        idx = bisect.bisect_right(p1keys, elev_kft)
        k0, k1 = p1keys[idx - 1], p1keys[idx]
    x0 = _x_for_y_on_piecewise(p1points[labels_by_key[k0]], y_temp)
    x1 = _x_for_y_on_piecewise(p1points[labels_by_key[k1]], y_temp)
    frac = (elev_kft - k0) / (k1 - k0) if k1 != k0 else 0.0
    x_p1 = x0 + frac * (x1 - x0)
    return y_temp, x_p1


def _y_on_2col_curve(curve, x_p1):
    m = (curve["y2"] - curve["y1"]) / (curve["x2"] - curve["x1"])
    return curve["y1"] + m * (x_p1 - curve["x1"])


def _y_for_weight(cal, x_p1, weight_kg):
    curves = cal["min_v2_weight_curves_kg"]
    order = cal["min_v2_weight_order"]
    w = weight_kg
    if w <= order[0]:
        k0, k1 = order[0], order[1]
    elif w >= order[-1]:
        k0, k1 = order[-2], order[-1]
    else:
        idx = bisect.bisect_right(order, w)
        k0, k1 = order[idx - 1], order[idx]
    y0 = _y_on_2col_curve(curves[str(k0)], x_p1)
    y1 = _y_on_2col_curve(curves[str(k1)], x_p1)
    frac = (w - k0) / (k1 - k0) if k1 != k0 else 0.0
    frac = max(0.0, min(1.0, frac))
    return y0 + frac * (y1 - y0)


def _v2_baseline_y_for_weight(cal, weight_kg):
    pts = cal["v2_baseline_curve_points"]
    ws = [p[0] for p in pts]
    if weight_kg <= ws[0]:
        w0, w1 = pts[0], pts[1]
    elif weight_kg >= ws[-1]:
        w0, w1 = pts[-2], pts[-1]
    else:
        idx = bisect.bisect_right(ws, weight_kg)
        w0, w1 = pts[idx - 1], pts[idx]
    frac = (weight_kg - w0[0]) / (w1[0] - w0[0]) if w1[0] != w0[0] else 0.0
    return w0[1] + frac * (w1[1] - w0[1])


def compute_v2norm(chart_key, elevation_ft, temp_c, weight_kg):
    cal = CAL[chart_key]

    y_temp, x_p1 = _panel1_x(cal, elevation_ft, temp_c)

    # Shared-X + weight curve family (12/14/16/18) -> MINIMUM V2 (manual, p.3-23).
    y_minv2 = _y_for_weight(cal, x_p1, weight_kg)
    axv2 = cal["v2_axis"]
    min_v2_kt = 80 + (y_minv2 - axv2["y_at_80kt"]) / axv2["px_per_kt"]

    # Weight-only BASELINE diagonal (V2/VSR = 1.13, nominal) -> main V2.
    ax_w = cal["v2_baseline_weight_axis"]
    x_v2 = ax_w["x_at_0kg"] + ax_w["px_per_kg"] * weight_kg
    y_v2 = _v2_baseline_y_for_weight(cal, weight_kg)
    v2_kt = 80 + (y_v2 - axv2["y_at_80kt"]) / axv2["px_per_kt"]

    v2norm_final = max(v2_kt, min_v2_kt)

    construction = {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "weight_kg": weight_kg,
        "y_temp": y_temp,
        "x_p1": x_p1,
        "y_minv2": y_minv2,
        "min_v2_kt": round(min_v2_kt, 1),
        "x_v2": x_v2,
        "y_v2": y_v2,
        "v2_kt": round(v2_kt, 1),
        "v2norm_final_kt": round(v2norm_final, 1),
    }
    return construction


def draw_overlay(chart_key, construction, out_path, panel1_left=940, v2_axis_left=940):
    cal = CAL[chart_key]
    img_path = os.path.join(IMG_ROOT, cal["source_image"])
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    RED = (230, 0, 0)
    BLUE = (0, 90, 230)
    GREEN = (0, 160, 60)
    W = 4

    c = construction
    y_temp = c["y_temp"]
    x_p1 = c["x_p1"]

    # Panel 1: temp -> x_p1
    draw.line([(panel1_left, y_temp), (x_p1, y_temp)], fill=RED, width=W)
    draw.ellipse([x_p1 - 5, y_temp - 5, x_p1 + 5, y_temp + 5], outline=RED, width=3)

    # Shared vertical line down into the MIN V2 panel (curve family)
    draw.line([(x_p1, y_temp), (x_p1, c["y_minv2"])], fill=RED, width=W)

    # MIN V2 readout (shared-X + weight panel)
    draw.line([(v2_axis_left, c["y_minv2"]), (x_p1, c["y_minv2"])], fill=RED, width=W)
    draw.ellipse([x_p1 - 5, c["y_minv2"] - 5, x_p1 + 5, c["y_minv2"] + 5], outline=RED, width=3)

    # V2 readout (main, weight-only BASELINE diagonal), entered from the
    # bottom with weight on its own axis.
    x_v2 = c["x_v2"]
    v2_panel_bottom = cal["v2_axis"]["y_at_80kt"] + 50 / cal["v2_axis"]["px_per_kt"]
    draw.line([(x_v2, v2_panel_bottom), (x_v2, c["y_v2"])], fill=BLUE, width=W)
    draw.line([(v2_axis_left, c["y_v2"]), (x_v2, c["y_v2"])], fill=BLUE, width=W)
    draw.ellipse([x_v2 - 5, c["y_v2"] - 5, x_v2 + 5, c["y_v2"] + 5], outline=BLUE, width=3)

    # Mark the winning (final) value with a green square.
    y_final = c["y_v2"] if c["v2_kt"] >= c["min_v2_kt"] else c["y_minv2"]
    draw.rectangle([v2_axis_left - 14, y_final - 7, v2_axis_left - 2, y_final + 7], outline=GREEN, width=3)

    img.save(out_path)
    return out_path
