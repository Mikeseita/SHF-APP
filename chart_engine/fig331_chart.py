"""
Digitized reading engine for the C-295M "Takeoff Speeds - Short Field
Takeoff, Flaps TO" chart (Figure 3-31): Vr, V2, and their MIN floors.

Panel 1: Ambient Temperature (Y) vs Airfield Pressure Altitude curves
         (family -1/SL/2, same layout as the other charts) -> shared X.
Panel 2: shared X + weight curve (family, some overlapping) -> MIN VR.
Panel 3: shared X + weight curve (family, plateau+knee per curve) -> VR.
Min V2 : weight only (single curve, no temp/altitude dependency) -> MIN V2.
Panel 5: shared X + weight curve (family) -> V2.

Final Vr = max(VR panel value, MIN VR panel value).
Final V2 = max(V2 panel value, MIN V2 value).

Panels 2/3/5 are modeled with a local 2-column linear fit (x=1296px and
x=1308px) per weight curve, rather than a full dense trace, because the
shared X actually produced by panel 1 for this aircraft's practical
operating range (temps 10/20/30 C, elevations near sea level) always
falls inside that narrow window. Accuracy degrades outside it.
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "fig331_calibration.json"), encoding="utf-8") as f:
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
    """curve: {"x1":..,"y1":..,"x2":..,"y2":..} local linear model."""
    m = (curve["y2"] - curve["y1"]) / (curve["x2"] - curve["x1"])
    return curve["y1"] + m * (x_p1 - curve["x1"])


def _y_for_weight(cal, curves_key, order_key, x_p1, weight_kg):
    """Interpolate across the weight-curve family at shared X. Weights
    outside the digitized range clamp to the nearest end curve rather than
    extrapolating past it (there is no printed curve beyond it to follow)."""
    curves = cal[curves_key]
    order = cal[order_key]
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


def _minv2_y_for_weight(cal, weight_kg):
    pts = cal["minv2_curve_points"]
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


def compute_vr_v2(chart_key, elevation_ft, temp_c, weight_kg):
    cal = CAL[chart_key]

    y_temp, x_p1 = _panel1_x(cal, elevation_ft, temp_c)

    y_minvr = _y_for_weight(cal, "panel2_weight_curves_kg", "panel2_weight_order", x_p1, weight_kg)
    ax2 = cal["panel2_minvr_axis"]
    min_vr_kt = 70 + (y_minvr - ax2["y_at_70kt"]) / ax2["px_per_kt"]

    y_vr = _y_for_weight(cal, "panel3_weight_curves_kg", "panel3_weight_order", x_p1, weight_kg)
    ax3 = cal["panel3_vr_axis"]
    vr_kt = 70 + (y_vr - ax3["y_at_70kt"]) / ax3["px_per_kt"]

    vr_final = max(vr_kt, min_vr_kt)

    # The simple weight-only diagonal panel is the MAIN V2 (not a floor);
    # the shared-X + weight panel (12/14/16/18, same layout as MIN VR) is V2 MIN.
    ax_w = cal["minv2_weight_axis"]
    x_v2main = ax_w["x_at_0kg"] + ax_w["px_per_kg"] * weight_kg
    y_v2main = _minv2_y_for_weight(cal, weight_kg)
    axv2 = cal["v2_axis"]
    v2_kt = 80 + (y_v2main - axv2["y_at_80kt"]) / axv2["px_per_kt"]

    y_minv2 = _y_for_weight(cal, "panel5_weight_curves_kg", "panel5_weight_order", x_p1, weight_kg)
    min_v2_kt = 80 + (y_minv2 - axv2["y_at_80kt"]) / axv2["px_per_kt"]

    v2_final = max(v2_kt, min_v2_kt)

    construction = {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "weight_kg": weight_kg,
        "y_temp": y_temp,
        "x_p1": x_p1,
        "y_minvr": y_minvr,
        "min_vr_kt": round(min_vr_kt, 1),
        "y_vr": y_vr,
        "vr_kt": round(vr_kt, 1),
        "vr_final_kt": round(vr_final, 1),
        "x_v2main": x_v2main,
        "y_v2main": y_v2main,
        "v2_kt": round(v2_kt, 1),
        "y_minv2": y_minv2,
        "min_v2_kt": round(min_v2_kt, 1),
        "v2_final_kt": round(v2_final, 1),
    }
    return construction


def draw_overlay(chart_key, construction, out_path, panel1_left=940, vr_axis_left=940, v2_axis_left=940):
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
    draw.line([(panel1_left, y_temp), (x_p1, y_temp)], fill=BLUE, width=W)
    draw.ellipse([x_p1 - 5, y_temp - 5, x_p1 + 5, y_temp + 5], outline=BLUE, width=3)

    # Shared vertical line straight down through panels 2 and 3
    draw.line([(x_p1, y_temp), (x_p1, c["y_vr"])], fill=BLUE, width=W)

    # VR readout (panel 3)
    draw.line([(vr_axis_left, c["y_vr"]), (x_p1, c["y_vr"])], fill=BLUE, width=W)
    draw.ellipse([x_p1 - 5, c["y_vr"] - 5, x_p1 + 5, c["y_vr"] + 5], outline=BLUE, width=3)

    # MIN VR readout (panel 2) in red for comparison
    draw.line([(vr_axis_left, c["y_minvr"]), (x_p1, c["y_minvr"])], fill=RED, width=W)
    draw.ellipse([x_p1 - 5, c["y_minvr"] - 5, x_p1 + 5, c["y_minvr"] + 5], outline=RED, width=3)

    # Shared vertical line through the MIN V2 panel (shares layout with MIN VR)
    draw.line([(x_p1, c["y_vr"]), (x_p1, c["y_minv2"])], fill=RED, width=W)
    draw.line([(v2_axis_left, c["y_minv2"]), (x_p1, c["y_minv2"])], fill=RED, width=W)
    draw.ellipse([x_p1 - 5, c["y_minv2"] - 5, x_p1 + 5, c["y_minv2"] + 5], outline=RED, width=3)

    # MAIN V2 readout: enter with WEIGHT (top of its own small panel), straight
    # down to the curve, then right to the same shared V2 axis.
    x_v2main = c["x_v2main"]
    v2main_panel_top = cal["v2_axis"]["y_at_80kt"]
    draw.line([(x_v2main, v2main_panel_top), (x_v2main, c["y_v2main"])], fill=BLUE, width=W)
    draw.line([(v2_axis_left, c["y_v2main"]), (x_v2main, c["y_v2main"])], fill=BLUE, width=W)
    draw.ellipse([x_v2main - 5, c["y_v2main"] - 5, x_v2main + 5, c["y_v2main"] + 5], outline=BLUE, width=3)

    # Mark the WINNING (final) value on the shared axis with a green square,
    # since Vr_final = max(VR, MIN VR) and V2_final = max(V2, MIN V2).
    y_vr_final = c["y_vr"] if c["vr_kt"] >= c["min_vr_kt"] else c["y_minvr"]
    draw.rectangle([vr_axis_left - 14, y_vr_final - 7, vr_axis_left - 2, y_vr_final + 7], outline=GREEN, width=3)
    y_v2_final = c["y_v2main"] if c["v2_kt"] >= c["min_v2_kt"] else c["y_minv2"]
    draw.rectangle([v2_axis_left - 14, y_v2_final - 7, v2_axis_left - 2, y_v2_final + 7], outline=GREEN, width=3)

    img.save(out_path)
    return out_path
