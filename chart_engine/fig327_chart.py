"""
Digitized reading engine for the C-295M "Refusal Speed and Critical Engine
Failure Speed - Short Field Takeoff" chart (Figure 3-27).

Panel 1: Airfield Ambient Temperature (X) vs Airfield Pressure Altitude curves
         (family) -> horizontal transfer (shared height with panel 2)
Panel 2: Runway Length Available (or CFL, same units/curves) -> vertical
         transfer (shared x with panel 3)
Panel 3: Takeoff Weight curves (fan from a common origin) -> Uncorrected
         Indicated Airspeed (right axis)
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "fig327_calibration.json"), encoding="utf-8") as f:
    CAL = json.load(f)

_PANEL1_POINTS_CACHE = {}


def _panel1_points(cal):
    fname = cal["panel1_points_file"]
    if fname not in _PANEL1_POINTS_CACHE:
        with open(os.path.join(DATA_DIR, fname), encoding="utf-8") as f:
            _PANEL1_POINTS_CACHE[fname] = json.load(f)
    return _PANEL1_POINTS_CACHE[fname]


def _y_for_x_on_piecewise(points, x_target):
    """points: list of [x,y] sorted ascending by x (dense, ~1px steps)."""
    n = len(points)
    if x_target <= points[0][0]:
        return points[0][1]
    if x_target >= points[-1][0]:
        return points[-1][1]
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= x_target:
            lo = mid
        else:
            hi = mid
    x0, y0 = points[lo]
    x1, y1 = points[hi]
    frac = (x_target - x0) / (x1 - x0) if x1 != x0 else 0.0
    return y0 + frac * (y1 - y0)


def temp_to_x(cal, temp_c):
    ax = cal["panel1_temp_axis"]
    return ax["x_at_neg20"] + ax["px_per_degC"] * (temp_c + 20)


def _panel1_height(cal, elevation_ft, temp_c):
    """Panel 1: given temperature and elevation, return the shared height y_p1."""
    x_temp = temp_to_x(cal, temp_c)
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
    y0 = _y_for_x_on_piecewise(p1points[labels_by_key[k0]], x_temp)
    y1 = _y_for_x_on_piecewise(p1points[labels_by_key[k1]], x_temp)
    frac = (elev_kft - k0) / (k1 - k0) if k1 != k0 else 0.0
    y_p1 = y0 + frac * (y1 - y0)
    return x_temp, y_p1, (k0, k1), frac


def _panel2_x(cal, y_p1, length_kft):
    """Panel 2: given the shared height and a runway-length/CFL value (thousands
    of feet), return the shared x for panel 3, plus whether the point is
    off-chart (curve does not reach that high -> speed off-scale)."""
    curves = cal["panel2_runway_curves"]
    keys = sorted(float(k) for k in curves.keys())
    if length_kft <= keys[0]:
        k0, k1 = keys[0], keys[1]
    elif length_kft >= keys[-1]:
        k0, k1 = keys[-2], keys[-1]
    else:
        idx = bisect.bisect_right(keys, length_kft)
        k0, k1 = keys[idx - 1], keys[idx]

    def key_str(k):
        return str(k) if k != int(k) else "%.1f" % k

    c0, c1 = curves[key_str(k0)], curves[key_str(k1)]
    frac = (length_kft - k0) / (k1 - k0) if k1 != k0 else 0.0
    x0 = (y_p1 - c0["b"]) / c0["m"]
    x1 = (y_p1 - c1["b"]) / c1["m"]
    x_p2 = x0 + frac * (x1 - x0)

    tips = cal["panel2_curve_tip_y"]
    tip_y0 = tips[key_str(k0)]
    tip_y1 = tips[key_str(k1)]
    tip_y = tip_y0 + frac * (tip_y1 - tip_y0)
    off_chart = y_p1 < tip_y

    xb = cal["panel3_x_bounds"]
    if x_p2 < xb["x_min"] or x_p2 > xb["x_max"]:
        off_chart = True

    return x_p2, off_chart, (k0, k1), frac


def _panel3_speed(cal, x_p2, weight_kg):
    """Panel 3: given the shared x and takeoff weight, return knots (uncorrected IAS)."""
    curves = cal["panel3_weight_curves"]
    keys = cal["panel3_weight_curve_order"]
    w_target = weight_kg / 1000.0
    if w_target <= keys[0]:
        k0, k1 = keys[0], keys[1]
    elif w_target >= keys[-1]:
        k0, k1 = keys[-2], keys[-1]
    else:
        idx = bisect.bisect_right(keys, w_target)
        k0, k1 = keys[idx - 1], keys[idx]

    def key_str(k):
        return str(k) if k != int(k) else str(int(k))

    c0, c1 = curves[key_str(k0)], curves[key_str(k1)]
    frac = (w_target - k0) / (k1 - k0) if k1 != k0 else 0.0
    y0 = c0["m"] * x_p2 + c0["b"]
    y1 = c1["m"] * x_p2 + c1["b"]
    y_p3 = y0 + frac * (y1 - y0)

    ax = cal["panel3_speed_axis"]
    speed_kt = 120 - (y_p3 - ax["y_at_120kt"]) / ax["px_per_kt"]
    return speed_kt, y_p3, (k0, k1), frac


def compute_speed(chart_key, elevation_ft, temp_c, weight_kg, length_kft, off_chart_label=">110"):
    """Shared computation for both Vre (length_kft = runway length available)
    and Vcef (length_kft = CFL for this weight/temp). Returns a dict with the
    construction points and either a numeric speed_kt or the off-chart label."""
    cal = CAL[chart_key]

    x_temp, y_p1, p1_bracket, p1_frac = _panel1_height(cal, elevation_ft, temp_c)
    x_p2, off_chart, p2_bracket, p2_frac = _panel2_x(cal, y_p1, length_kft)

    construction = {
        "temp_c": temp_c,
        "elevation_ft": elevation_ft,
        "weight_kg": weight_kg,
        "length_kft": length_kft,
        "x_temp": x_temp,
        "y_p1": y_p1,
        "p1_bracket": p1_bracket,
        "p1_frac": p1_frac,
        "x_p2": x_p2,
        "p2_bracket": p2_bracket,
        "p2_frac": p2_frac,
        "off_chart": off_chart,
    }

    if off_chart:
        construction["speed_kt"] = None
        construction["speed_label"] = off_chart_label
        construction["y_p3"] = None
        return construction

    speed_kt, y_p3, p3_bracket, p3_frac = _panel3_speed(cal, x_p2, weight_kg)
    construction["y_p3"] = y_p3
    construction["p3_bracket"] = p3_bracket
    construction["p3_frac"] = p3_frac
    construction["speed_kt"] = round(speed_kt, 1)
    construction["speed_label"] = "%.1f" % round(speed_kt, 1)
    return construction


def draw_overlay(chart_key, construction, out_path, panel1_left=308, panel3_right=1470):
    cal = CAL[chart_key]
    img_path = os.path.join(IMG_ROOT, cal["source_image"])
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    RED = (230, 0, 0)
    W = 4

    c = construction
    x_temp = c["x_temp"]
    y_p1 = c["y_p1"]
    x_p2 = c["x_p2"]

    draw.line([(panel1_left, y_p1), (x_p2, y_p1)], fill=RED, width=W)
    draw.line([(x_temp, y_p1), (x_temp, 903)], fill=RED, width=2)

    points = [(x_temp, y_p1), (x_p2, y_p1)]

    if not c["off_chart"]:
        y_p3 = c["y_p3"]
        draw.line([(x_p2, y_p1), (x_p2, y_p3)], fill=RED, width=W)
        draw.line([(x_p2, y_p3), (panel3_right, y_p3)], fill=RED, width=W)
        points.append((x_p2, y_p3))
        points.append((panel3_right, y_p3))

    for (px, py) in points:
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], outline=RED, width=3)

    img.save(out_path)
    return out_path
