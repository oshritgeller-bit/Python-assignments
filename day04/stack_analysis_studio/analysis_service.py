"""Qt-free application logic shared by the desktop app and the web app.

Both interfaces (``app.py`` with PySide6, ``web_app.py`` with Flask) parse user
input and run analyses through these functions, so they give the same results.
"""

from __future__ import annotations

import json
import math
import re
from typing import Callable

import numpy as np

from stack_analysis import (
    THRESHOLD_METHODS,
    Calibration,
    StackData,
    calibration_summary,
    object_stats,
    objects_per_slice,
    segment_3d,
    summarize_objects,
    z_profile,
)


SAMPLE_LABELS = {
    "cells3d": "cells3d · fluorescence cells (download on first use)",
    "synthetic": "Synthetic blobs · offline",
}
DEFAULT_SETTINGS = {
    "method": "otsu",
    "manualThreshold": None,
    "sigma": 0.5,
    "minVolume": 5.0,
    "splitTouching": True,
    "minDistance": 2.0,
}
REFERENCES = {
    "napari": "https://napari.org/stable/",
    "cells3d": "https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.cells3d",
    "laplace": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.laplace.html",
    "otsu": "https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.threshold_otsu",
    "li": "https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.threshold_li",
    "watershed": "https://scikit-image.org/docs/stable/api/skimage.segmentation.html#skimage.segmentation.watershed",
    "marching-cubes": "https://scikit-image.org/docs/stable/api/skimage.measure.html#skimage.measure.marching_cubes",
}


# --------------------------------------------------------------------- input

def parse_object(raw: str | dict, what: str) -> dict:
    """Accept a JSON string (desktop bridge) or an already-decoded dict (web)."""
    if isinstance(raw, dict):
        return raw
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {what}.") from exc
    if not isinstance(values, dict):
        raise ValueError(f"{what.capitalize()} must be an object.")
    return values


def parse_number(value: object, label: str, allow_zero: bool = False) -> float:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        try:
            value = float(value)
        except ValueError:
            raise ValueError(f"{label} must be a number.") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a number.")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{label} must be {'zero or ' if allow_zero else ''}positive.")
    return float(value)


def parse_calibration(values: dict) -> Calibration:
    return Calibration(
        parse_number(values.get("z"), "Voxel size z"),
        parse_number(values.get("y"), "Voxel size y"),
        parse_number(values.get("x"), "Voxel size x"),
        str(values.get("unit", "")),
    )


def parse_analysis_settings(values: dict) -> dict[str, object]:
    method = values.get("method", "otsu")
    if method not in THRESHOLD_METHODS:
        raise ValueError("Choose Otsu, Li or manual thresholding.")
    manual = None
    if method == "manual":
        raw = values.get("manualThreshold")
        if raw in (None, ""):
            raise ValueError("Enter a manual threshold value.")
        if isinstance(raw, str):
            try:
                raw = float(raw.strip())
            except ValueError:
                raise ValueError("Manual threshold must be a number.") from None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise ValueError("Manual threshold must be a number.")
        manual = float(raw)
    split = values.get("splitTouching", True)
    if not isinstance(split, bool):
        raise ValueError("Split touching objects must be true or false.")
    return {
        "method": method,
        "manualThreshold": manual,
        "sigma": parse_number(values.get("sigma", 0.5), "Smoothing sigma", allow_zero=True),
        "minVolume": parse_number(values.get("minVolume", 5), "Minimum volume", allow_zero=True),
        "splitTouching": split,
        "minDistance": parse_number(values.get("minDistance", 2), "Minimum distance"),
    }


def parse_channel(stack: StackData, values: dict) -> str:
    channel = values.get("channel") or stack.default_channel
    if channel not in stack.channels:
        raise ValueError("Choose one of the stack's channels.")
    return channel


def safe_name(name: str, fallback: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name.strip(), flags=re.UNICODE).strip("_") or fallback


# ------------------------------------------------------------------ analysis

def run_analysis(
    stack: StackData,
    sample_key: str,
    cal: Calibration,
    channel: str,
    settings: dict[str, object],
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, object], np.ndarray]:
    """Z-profile + segmentation + object statistics; returns (analysis, labels).

    Raises ``ValueError`` or ``MemoryError`` if the analysis cannot be done.
    ``progress`` receives a short message before each step.
    """
    report = progress or (lambda _message: None)
    volume = stack.channels[channel]
    report("Computing the z-axis profile…")
    profile = z_profile(volume, cal)
    report("Segmenting 3D objects…")
    labels, info = segment_3d(
        volume,
        cal,
        method=settings["method"],
        manual_threshold=settings["manualThreshold"],
        sigma=settings["sigma"],
        min_volume=settings["minVolume"],
        split_touching=settings["splitTouching"],
        min_distance=settings["minDistance"],
    )
    report("Measuring objects…")
    objects = object_stats(labels, volume, cal)
    summary = summarize_objects(objects)
    unit = cal.unit

    def fmt(value, suffix="", digits=3):
        return "—" if value is None else f"{value:.{digits}g}{suffix}"

    analysis = {
        "name": f"{stack.name} · {channel}",
        "sample": sample_key,
        "sampleName": stack.name,
        "source": stack.source,
        "channel": channel,
        "calibration": calibration_summary(stack, cal),
        "settings": settings,
        "zProfile": profile,
        "segmentation": info,
        "objects": objects,
        "summary": summary,
        "objectsPerSlice": objects_per_slice(labels),
        "cards": [
            ["Objects", str(summary["count"])],
            ["Total object volume", fmt(summary["totalVolume"], f" {unit}³", 4)],
            ["Mean equivalent diameter", fmt(summary["meanDiameter"], f" {unit}")],
            ["Brightest slice", f"{profile['brightestSlice']} · z {profile['brightestSlice'] * cal.z:.3g} {unit}"],
            ["Best-focus slice", f"{profile['bestFocusSlice']} · z {profile['bestFocusSlice'] * cal.z:.3g} {unit}"],
            ["Half-intensity depth", fmt(profile["halfIntensityDepth"], f" {unit}") if profile["halfIntensityDepth"] is not None else "Not reached"],
        ],
    }
    return analysis, labels


def import_note(stored_count: object, new_count: int) -> str:
    if stored_count != new_count:
        return f"Re-analysed: {new_count} objects (file: {stored_count})."
    return f"Re-analysed and matched the file: {new_count} objects."
