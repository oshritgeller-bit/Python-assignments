"""Render one z-slice of a stack as a PNG for the web viewer (replaces napari's canvas).

Channels are coloured and added together like napari's additive blending, and
segmented objects can be drawn on top with one colour per label.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from stack_analysis import StackData


CHANNEL_COLORS = {
    "nuclei": (0.0, 1.0, 1.0),     # cyan
    "membranes": (1.0, 0.0, 1.0),  # magenta
    "blobs": (1.0, 1.0, 1.0),      # gray
}
LABEL_OPACITY = 0.55


def contrast_limits(stack: StackData) -> dict[str, tuple[float, float]]:
    """Per-channel display range (0.5–99.8 percentile), as in the desktop viewer."""
    limits = {}
    for name, data in stack.channels.items():
        low, high = np.percentile(data, (0.5, 99.8))
        limits[name] = (float(low), float(max(high, low + 1)))
    return limits


def label_colors(count: int, seed: int = 3) -> np.ndarray:
    """Bright, distinct-looking colours for labels 1..count (row 0 is background)."""
    rng = np.random.default_rng(seed)
    hue = rng.random(count + 1)
    # HSV → RGB with full saturation and value.
    k = (np.array([5.0, 3.0, 1.0])[None, :] + hue[:, None] * 6.0) % 6.0
    rgb = 1.0 - np.clip(np.minimum(k, 4.0 - k), 0.0, 1.0)
    rgb[0] = 0.0
    return rgb.astype(np.float32)


def render_slice(
    stack: StackData,
    index: int,
    limits: dict[str, tuple[float, float]],
    labels: np.ndarray | None = None,
) -> bytes:
    """PNG bytes for slice ``index``; ``labels`` (z, y, x) adds the object overlay."""
    depth = stack.shape[0]
    if not 0 <= index < depth:
        raise IndexError(f"Slice must be between 0 and {depth - 1}.")
    rgb = np.zeros((*stack.shape[1:], 3), dtype=np.float32)
    for name, data in stack.channels.items():
        low, high = limits[name]
        plane = (data[index].astype(np.float32) - low) / (high - low)
        rgb += np.clip(plane, 0.0, 1.0)[..., None] * np.array(CHANNEL_COLORS.get(name, (1.0, 1.0, 1.0)), dtype=np.float32)
    rgb = np.clip(rgb, 0.0, 1.0)
    if labels is not None:
        plane = labels[index]
        inside = plane > 0
        if inside.any():
            colors = label_colors(int(labels.max()))
            rgb[inside] = (1 - LABEL_OPACITY) * rgb[inside] + LABEL_OPACITY * colors[plane[inside]]
    image = Image.fromarray((rgb * 255).round().astype(np.uint8), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()
