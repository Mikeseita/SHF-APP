"""
Digitized reading engine for the C-295M "Landing Speeds - Short Field
Landing" chart (Figure 8-7), Flaps 23.

No temp/altitude dependency -- landing weight only, two curves:
  VTH = 1.13 VSR (threshold speed) -- used as Vref_shf.
  VTD = 1.10 VSR (touchdown speed).
"""
import json
import os
import bisect
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_ROOT = os.path.join(BASE_DIR, "SHF TABLES")

with open(os.path.join(DATA_DIR, "vref_calibration.json"), encoding="utf-8") as f:
    CAL = json.load(f)


def _y_for_weight(points, weight_kg):
    ws = [p[0] for p in points]
    if weight_kg <= ws[0]:
        w0, w1 = points[0], points[1]
    elif weight_kg >= ws[-1]:
        w0, w1 = points[-2], points[-1]
    else:
        idx = bisect.bisect_right(ws, weight_kg)
        w0, w1 = points[idx - 1], points[idx]
    frac = (weight_kg - w0[0]) / (w1[0] - w0[0]) if w1[0] != w0[0] else 0.0
    return w0[1] + frac * (w1[1] - w0[1])


def compute_vref(chart_key, weight_kg):
    cal = CAL[chart_key]
    ax_w = cal["weight_axis"]
    x = ax_w["x_at_12000kg"] + ax_w["px_per_kg"] * (weight_kg - 12000)

    y_vth = _y_for_weight(cal["vth_curve_points"], weight_kg)
    y_vtd = _y_for_weight(cal["vtd_curve_points"], weight_kg)

    ax_s = cal["speed_axis"]
    vth_kt = 115 - (y_vth - ax_s["y_at_115kt"]) / ax_s["px_per_kt"]
    vtd_kt = 115 - (y_vtd - ax_s["y_at_115kt"]) / ax_s["px_per_kt"]

    return {
        "weight_kg": weight_kg,
        "x": x,
        "y_vth": y_vth,
        "y_vtd": y_vtd,
        "vth_kt": round(vth_kt, 1),
        "vtd_kt": round(vtd_kt, 1),
        "vref_shf_kt": round(vth_kt, 1),
    }


def draw_overlay(chart_key, construction, out_path, speed_axis_left=380, weight_axis_bottom=1322.5):
    cal = CAL[chart_key]
    img_path = os.path.join(IMG_ROOT, cal["source_image"])
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    BLUE = (0, 90, 230)
    W = 4

    c = construction
    x = c["x"]

    # Only VTH matters for Vref_shf -- VTD is not used, so it's left undrawn.
    # Enter with weight (bottom axis), go up to VTH, then across to the speed axis.
    draw.line([(x, weight_axis_bottom), (x, c["y_vth"])], fill=BLUE, width=W)
    draw.line([(speed_axis_left, c["y_vth"]), (x, c["y_vth"])], fill=BLUE, width=W)
    draw.ellipse([x - 5, c["y_vth"] - 5, x + 5, c["y_vth"] + 5], outline=BLUE, width=3)

    img.save(out_path)
    return out_path
