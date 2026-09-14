"""
Digitized reading engine for the C-295M "MTOW Limited by Single Engine
Climb Performance - Short Field Takeoff" chart family.

Panel 1: Airfield Ambient Temperature (Y) vs Airfield Pressure Altitude curves (family) -> vertical transfer
Panel 2: Climb Gradient % curves -> horizontal transfer
Panel 3: Drag Index diagonals -> Takeoff Weight (right axis)
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "chart_calibration.json"), encoding="utf-8") as f:
    CAL = json.load(f)

_PANEL1_POINTS_CACHE = {}


def _panel1_points(cal):
    fname = cal["panel1_points_file"]
    if fname not in _PANEL1_POINTS_CACHE:
        with open(os.path.join(DATA_DIR, fname), encoding="utf-8") as f:
            _PANEL1_POINTS_CACHE[fname] = json.load(f)
    return _PANEL1_POINTS_CACHE[fname]


def _x_for_y_on_piecewise(points, y_target):
    """points: list of [y,x] sorted ascending by y (dense, ~1px steps).
    Linearly interpolates x at y_target; clamps to nearest endpoint outside range."""
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
    return ax["y_at_60C"] + ax["px_per_degC"] * (60 - temp_c)


def weight_to_y(cal, weight_kg):
    ax = cal["panel3"]["weight_axis"]
    return ax["y_at_24000"] + ax["px_per_1000kg"] * (24 - weight_kg / 1000.0)


def y_to_weight(cal, y):
    ax = cal["panel3"]["weight_axis"]
    return (24 - (y - ax["y_at_24000"]) / ax["px_per_1000kg"]) * 1000.0


def di_to_x(cal, di):
    ax = cal["panel3"]["drag_index_axis"]
    return ax["x_at_0"] + (ax["x_at_200"] - ax["x_at_0"]) * (di / 200.0)


def _panel2_slope_at_y(diagonals, y):
    """diagonals: sorted list of [y_at_x806, slope]. Interpolate slope at height y."""
    ys = [d[0] for d in diagonals]
    if y <= ys[0]:
        return diagonals[0][1]
    if y >= ys[-1]:
        return diagonals[-1][1]
    idx = bisect.bisect_right(ys, y)
    y0, s0 = diagonals[idx - 1]
    y1, s1 = diagonals[idx]
    frac = (y - y0) / (y1 - y0) if y1 != y0 else 0.0
    return s0 + frac * (s1 - s0)


def compute_mtow_1eng(chart_key, elevation_ft, temp_c, gradient_pct, drag_index):
    """Returns dict with computed weight_kg and the list of construction points
    for drawing the red overlay lines."""
    cal = CAL[chart_key]

    y_temp = temp_to_y(cal, temp_c)

    # Panel 1: interpolate pressure-altitude curves by elevation (in kft).
    # Curves are digitized as dense pixel-traced piecewise point lists (not a
    # single linear fit), since they are genuinely curved, not straight lines.
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
    frac1 = (elev_kft - k0) / (k1 - k0) if k1 != k0 else 0.0
    x_p1 = x0 + frac1 * (x1 - x0)

    # Panel 2: interpolate gradient curves at x_p1 -> find y where gradient curve crosses x_p1.
    # Each curve's y is solved independently before interpolating (interpolating
    # m/b directly can blow up when the two bracketing slopes differ a lot).
    p2curves = cal["panel2_gradient_curves"]
    g_keys = sorted(int(k) for k in p2curves.keys())
    if gradient_pct <= g_keys[0]:
        g0, g1 = g_keys[0], g_keys[1]
    elif gradient_pct >= g_keys[-1]:
        g0, g1 = g_keys[-2], g_keys[-1]
    else:
        idx = bisect.bisect_right(g_keys, gradient_pct)
        g0, g1 = g_keys[idx - 1], g_keys[idx]
    c0, c1 = p2curves[str(g0)], p2curves[str(g1)]
    gfrac = (gradient_pct - g0) / (g1 - g0) if g1 != g0 else 0.0
    y0_p2 = (x_p1 - c0["b"]) / c0["m"]
    y1_p2 = (x_p1 - c1["b"]) / c1["m"]
    y_p2 = y0_p2 + gfrac * (y1_p2 - y0_p2)

    # Panel 3: baseline (DI=0) at x=baseline_x, height y_p2; follow diagonal to DI
    p3 = cal["panel3"]
    x_baseline = p3["baseline_x"]
    x_di = di_to_x(cal, drag_index)
    slope = _panel2_slope_at_y(p3["diagonals"], y_p2)
    y_final = y_p2 + slope * (x_di - x_baseline)

    weight_kg = y_to_weight(cal, y_final)

    construction = {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "gradient_pct": gradient_pct,
        "drag_index": drag_index,
        "y_temp": y_temp,
        "x_p1": x_p1,
        "p1_bracket": [k0, k1],
        "p1_frac": frac1,
        "y_p2": y_p2,
        "p2_bracket": [g0, g1],
        "p2_frac": gfrac,
        "x_baseline": x_baseline,
        "x_di": x_di,
        "y_final": y_final,
        "weight_kg": round(weight_kg),
    }
    return construction


def draw_overlay(chart_key, construction, out_path, panel1_left=457):
    cal = CAL[chart_key]
    img_path = os.path.join(IMG_ROOT, cal["source_image"])
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    RED = (230, 0, 0)
    W = 4

    c = construction
    y_temp = c["y_temp"]
    x_p1 = c["x_p1"]
    y_p2 = c["y_p2"]
    x_baseline = c["x_baseline"]
    x_di = c["x_di"]
    y_final = c["y_final"]
    right_axis_x = cal["panel3"].get("right_axis_x", 1429.5)

    draw.line([(panel1_left, y_temp), (x_p1, y_temp)], fill=RED, width=W)
    draw.line([(x_p1, y_temp), (x_p1, y_p2)], fill=RED, width=W)
    draw.line([(x_p1, y_p2), (x_baseline, y_p2)], fill=RED, width=W)
    draw.line([(x_baseline, y_p2), (x_di, y_final)], fill=RED, width=W)
    draw.line([(x_di, y_final), (right_axis_x, y_final)], fill=RED, width=W)

    for (px, py) in [(x_p1, y_temp), (x_p1, y_p2), (x_baseline, y_p2), (x_di, y_final), (right_axis_x, y_final)]:
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], outline=RED, width=3)

    img.save(out_path)
    return out_path
