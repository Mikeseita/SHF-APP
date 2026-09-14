"""
Generic reading engine for the family of 2-panel charts that share the same
layout: Panel 1 = Ambient Temperature vs Pressure Altitude curves (vertical
transfer), Panel 2 = a family of curves indexed by Takeoff Weight (thousands
kg), output read on a right-hand axis (feet or knots depending on chart).
Used for: Critical Field Length (CFL), Takeoff Ground Run (TO RUN).
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")


def load_cal(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def _line_x(curve, y):
    return curve["m"] * y + curve["b"]


def temp_to_y(cal, temp_c):
    ax = cal["panel1_temp_axis"]
    return ax["y_at_60C"] + ax["px_per_degC"] * (60 - temp_c)


def _interp_x(curves_dict, keys_sorted, labels_by_key, key_value, y):
    keys = keys_sorted
    if key_value <= keys[0]:
        k0, k1 = keys[0], keys[1]
    elif key_value >= keys[-1]:
        k0, k1 = keys[-2], keys[-1]
    else:
        idx = bisect.bisect_right(keys, key_value)
        k0, k1 = keys[idx - 1], keys[idx]
    x0 = _line_x(curves_dict[labels_by_key[k0]], y)
    x1 = _line_x(curves_dict[labels_by_key[k1]], y)
    frac = (key_value - k0) / (k1 - k0) if k1 != k0 else 0.0
    x = x0 + frac * (x1 - x0)
    return x, k0, k1, frac


def _y_for_x_on_piecewise(points, x_target):
    """points: list of [y,x] sorted ascending by y; x is monotonic (decreasing
    as y increases) along a single weight curve. Returns interpolated y for x_target,
    extrapolating linearly using the nearest edge segment if out of range."""
    n = len(points)
    if n < 2:
        return points[0][0]
    # points are sorted by y asc; x should be decreasing as y increases
    for i in range(n - 1):
        y_a, x_a = points[i]
        y_b, x_b = points[i + 1]
        lo, hi = (x_b, x_a) if x_a >= x_b else (x_a, x_b)
        if lo <= x_target <= hi:
            if x_b == x_a:
                return (y_a + y_b) / 2.0
            frac = (x_target - x_a) / (x_b - x_a)
            return y_a + frac * (y_b - y_a)
    # Out of range: this weight curve does not reach x_target within its
    # digitized extent. Clamp to the nearest endpoint (safe, bounded) --
    # the caller checks _x_gap_on_piecewise and marks the result unreliable
    # if the gap is too large to trust.
    x_first = points[0][1]
    if x_target > x_first:
        return points[0][0]
    return points[-1][0]


def _x_gap_on_piecewise(points, x_target):
    """How far x_target lies outside this curve's digitized x-range (0 if inside)."""
    xs = [p[1] for p in points]
    lo, hi = min(xs), max(xs)
    if lo <= x_target <= hi:
        return 0.0
    return min(abs(x_target - lo), abs(x_target - hi))


def compute_value(cal, elevation_ft, temp_c, weight_kg, panel2_points=None, output_axis_key="output_axis", y0_key="y_at_6kft", scale_key="px_per_kft", y0_value=6.0):
    y_temp = temp_to_y(cal, temp_c)

    p1curves = cal["panel1_curves"]
    p1keys = cal["panel1_curve_order_kft"]
    p1labels = cal["panel1_curve_labels"]
    labels_by_key = dict(zip(p1keys, p1labels))
    elev_kft = elevation_ft / 1000.0
    x_p1, k0, k1, frac1 = _interp_x(p1curves, p1keys, labels_by_key, elev_kft, y_temp)

    w_keys = cal["panel2_weight_order"]
    w_target = weight_kg / 1000.0
    if w_target <= w_keys[0]:
        idx0 = 0
    elif w_target >= w_keys[-1]:
        idx0 = len(w_keys) - 2
    else:
        idx0 = bisect.bisect_right(w_keys, w_target) - 1
    wk0, wk1 = w_keys[idx0], w_keys[idx0 + 1]
    wfrac = (w_target - wk0) / (wk1 - wk0) if wk1 != wk0 else 0.0

    reliable = True
    if panel2_points is not None:
        key0 = str(wk0) if wk0 != int(wk0) else str(int(wk0))
        key1 = str(wk1) if wk1 != int(wk1) else str(int(wk1))
        pts0 = panel2_points[key0]
        pts1 = panel2_points[key1]
        y0 = _y_for_x_on_piecewise(pts0, x_p1)
        y1 = _y_for_x_on_piecewise(pts1, x_p1)
        gap = max(_x_gap_on_piecewise(pts0, x_p1), _x_gap_on_piecewise(pts1, x_p1))
        reliable = gap <= 20
    else:
        p2curves = cal["panel2_weight_curves"]
        w_key_strs = sorted(p2curves.keys(), key=lambda s: float(s))
        c0, c1 = p2curves[w_key_strs[idx0]], p2curves[w_key_strs[idx0 + 1]]
        y0 = (x_p1 - c0["b"]) / c0["m"]
        y1 = (x_p1 - c1["b"]) / c1["m"]
    y_p2 = y0 + wfrac * (y1 - y0)

    ax = cal[output_axis_key]
    value = y0_value - (y_p2 - ax[y0_key]) / ax[scale_key]

    return {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "weight_kg": weight_kg,
        "y_temp": y_temp,
        "x_p1": x_p1,
        "p1_bracket": [k0, k1],
        "p2_bracket": [wk0, wk1],
        "y_p2": y_p2,
        "value": round(value, 2),
        "reliable": reliable,
    }


def draw_overlay(cal, construction, source_image, out_path, panel1_left=None, panel2_right=None):
    img_path = os.path.join(IMG_ROOT, source_image)
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    RED = (230, 0, 0)
    W = 4

    if panel1_left is None:
        panel1_left = cal.get("panel1_left", 290)
    if panel2_right is None:
        panel2_right = cal.get("panel2_right", cal["output_axis"].get("axis_x", 880))

    c = construction
    y_temp = c["y_temp"]
    x_p1 = c["x_p1"]
    y_p2 = c["y_p2"]

    draw.line([(panel1_left, y_temp), (x_p1, y_temp)], fill=RED, width=W)
    draw.line([(x_p1, y_temp), (x_p1, y_p2)], fill=RED, width=W)
    draw.line([(x_p1, y_p2), (panel2_right, y_p2)], fill=RED, width=W)

    for (px, py) in [(x_p1, y_temp), (x_p1, y_p2), (panel2_right, y_p2)]:
        draw.ellipse([px - 4, py - 4, px + 4, py + 4], outline=RED, width=2)

    img.save(out_path)
    return out_path
