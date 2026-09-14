"""
Digitized reading engine for the C-295M "Air Minimum Control Speed - Short
Field Takeoff" chart (Figure 3-29).

Panel 1: Ambient Temperature (Y) vs Airfield Pressure Altitude curves (family)
         -> vertical transfer (shared X with panel 2), same layout as MTOW/CFL.
Panel 2: X_shared enters at the BASELINE height (fixed reference weight,
         18000 kg). For any other weight, move along the local slope of the
         weight-curve family (interpolated between the printed 12-24 curves)
         from (X_shared, baseline) to the height matching the target weight.
         The resulting X reads directly on the CALIBRATED AIRSPEED axis
         (valid at any height via the chart's vertical gridlines).
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "vmca_calibration.json"), encoding="utf-8") as f:
    CAL = json.load(f)

_PANEL1_POINTS_CACHE = {}
_PANEL2_POINTS_CACHE = {}


def _panel1_points(cal):
    fname = cal["panel1_points_file"]
    if fname not in _PANEL1_POINTS_CACHE:
        with open(os.path.join(DATA_DIR, fname), encoding="utf-8") as f:
            _PANEL1_POINTS_CACHE[fname] = json.load(f)
    return _PANEL1_POINTS_CACHE[fname]


def _panel2_points(cal):
    fname = cal["panel2_points_file"]
    if fname not in _PANEL2_POINTS_CACHE:
        with open(os.path.join(DATA_DIR, fname), encoding="utf-8") as f:
            _PANEL2_POINTS_CACHE[fname] = json.load(f)
    return _PANEL2_POINTS_CACHE[fname]


def _x_for_y_generic(points, y_target):
    """points: list of [y,x] sorted ascending by y; clamps outside its range."""
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


def _x_for_y_on_piecewise(points, y_target):
    """points: list of [y,x] sorted ascending by y."""
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
    ax = cal["panel2_weight_axis"]
    return ax["y_at_18000"] - (weight_kg - 18000) / 1000.0 * ax["px_per_1000kg"]


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
    return y_temp, x_p1, (k0, k1), frac


def _weight_shift(cal, weight_kg, baseline_y, y_target):
    """How much X shifts, following the printed weight-curve family, between
    baseline height and y_target -- interpolated between the two bracketing
    printed weight curves (12-24 kg) using their own real (curved) paths."""
    points = _panel2_points(cal)
    keys = cal["panel2_diagonal_order_kg"]
    w = weight_kg / 1000.0
    if w <= keys[0]:
        k0, k1 = keys[0], keys[1]
    elif w >= keys[-1]:
        k0, k1 = keys[-2], keys[-1]
    else:
        idx = bisect.bisect_right(keys, w)
        k0, k1 = keys[idx - 1], keys[idx]
    pts0 = points[str(k0)]
    pts1 = points[str(k1)]
    shift0 = _x_for_y_generic(pts0, y_target) - _x_for_y_generic(pts0, baseline_y)
    shift1 = _x_for_y_generic(pts1, y_target) - _x_for_y_generic(pts1, baseline_y)
    frac = (w - k0) / (k1 - k0) if k1 != k0 else 0.0
    return shift0 + frac * (shift1 - shift0)


def _weight_shift_table_px(cal, weight_kg):
    """Authoritative shift (px) at the full target height, read directly off
    the printed grid squares (quadriculas) by the pilot for this weight --
    only valid within the table's weight range (validated for SL/1000ft)."""
    table = cal.get("panel2_shift_table_kg_quad")
    if not table:
        return None
    quad_px = cal["panel2_quadricula_px"]
    kgs = sorted(int(k) for k in table)
    if weight_kg < kgs[0] or weight_kg > kgs[-1]:
        return None
    if weight_kg in kgs:
        return table[str(weight_kg)] * quad_px
    idx = bisect.bisect_right(kgs, weight_kg)
    k0, k1 = kgs[idx - 1], kgs[idx]
    frac = (weight_kg - k0) / (k1 - k0)
    q0, q1 = table[str(k0)], table[str(k1)]
    return (q0 + frac * (q1 - q0)) * quad_px


def _weight_shift_final(cal, weight_kg, baseline_y, y_target):
    """Final shift to use for the landing point: prefer the empirical
    grid-square table (exact, pilot-verified) and fall back to the
    curve-interpolation model outside its validated weight range."""
    table_shift = _weight_shift_table_px(cal, weight_kg)
    if table_shift is not None:
        return table_shift
    return _weight_shift(cal, weight_kg, baseline_y, y_target)


def _weight_path(cal, weight_kg, x_p1, baseline_y, y_target, final_shift, n=40):
    """Points tracing the interpolated curve shape (not a straight chord) from
    (x_p1, baseline_y) to (x_final, y_target). The shape follows the real
    traced curve family; it is rescaled so it lands exactly on final_shift
    (the authoritative, pilot-verified endpoint) rather than the raw
    curve-trace estimate, which is not pixel-perfect near the baseline."""
    raw_final = _weight_shift(cal, weight_kg, baseline_y, y_target)
    scale = final_shift / raw_final if raw_final else 1.0
    pts = []
    for i in range(n + 1):
        y_i = baseline_y + (y_target - baseline_y) * i / n
        shift_i = _weight_shift(cal, weight_kg, baseline_y, y_i) * scale
        pts.append((x_p1 + shift_i, y_i))
    return pts


def compute_vmca(chart_key, elevation_ft, temp_c, weight_kg):
    cal = CAL[chart_key]

    y_temp, x_p1, p1_bracket, p1_frac = _panel1_x(cal, elevation_ft, temp_c)

    baseline_y = cal["panel2_baseline_y"]
    y_target = weight_to_y(cal, weight_kg)
    shift = _weight_shift_final(cal, weight_kg, baseline_y, y_target)
    x_final = x_p1 + shift

    ax = cal["airspeed_axis"]
    speed_kt = ax["m"] * x_final + ax["b"]

    construction = {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "weight_kg": weight_kg,
        "y_temp": y_temp,
        "x_p1": x_p1,
        "p1_bracket": p1_bracket,
        "p1_frac": p1_frac,
        "baseline_y": baseline_y,
        "y_target": y_target,
        "shift": shift,
        "x_final": x_final,
        "vmca_kt": round(speed_kt, 1),
    }
    return construction


def draw_overlay(chart_key, construction, out_path, panel1_left=1299, airspeed_axis_y=580):
    cal = CAL[chart_key]
    img_path = os.path.join(IMG_ROOT, cal["source_image"])
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    RED = (230, 0, 0)
    W = 4

    c = construction
    y_temp = c["y_temp"]
    x_p1 = c["x_p1"]
    baseline_y = c["baseline_y"]
    y_target = c["y_target"]
    x_final = c["x_final"]

    draw.line([(panel1_left, y_temp), (x_p1, y_temp)], fill=RED, width=W)
    draw.line([(x_p1, y_temp), (x_p1, baseline_y)], fill=RED, width=W)
    path = _weight_path(cal, c["weight_kg"], x_p1, baseline_y, y_target, c["shift"])
    draw.line(path, fill=RED, width=W)
    draw.line([(x_final, y_target), (x_final, airspeed_axis_y)], fill=RED, width=W)

    for (px, py) in [(x_p1, y_temp), (x_p1, baseline_y), (x_final, y_target), (x_final, airspeed_axis_y)]:
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], outline=RED, width=3)

    img.save(out_path)
    return out_path
