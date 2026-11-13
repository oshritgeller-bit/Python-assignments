"""Stack Analysis Studio as a web application (Flask).

The browser only shows forms, charts and tables. Everything else runs here:
loading the stack, the analysis (NumPy / SciPy / scikit-image) and rendering
slices as PNG images. Long operations (loading a sample, analysing) run in a
background thread as a *job*; the browser polls ``/api/jobs/<id>`` until the
job is done, so no request has to stay open for the 10+ seconds cells3d takes.

Run:  python web_app.py            → http://127.0.0.1:5000
      python web_app.py --host 0.0.0.0   (reachable from the same network)
"""

from __future__ import annotations

import argparse
import io
import logging
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from flask import Flask, jsonify, render_template, request, send_file

from analysis_service import (
    DEFAULT_SETTINGS,
    REFERENCES,
    SAMPLE_LABELS,
    parse_analysis_settings,
    parse_calibration,
    parse_channel,
    run_analysis,
    safe_name,
)
from reporting import build_analysis_markdown, write_objects_csv
from slice_render import contrast_limits, render_slice
from stack_analysis import SAMPLES, THRESHOLD_METHODS, UNITS, Calibration, StackData, calibration_summary


CATEGORY_LABELS = {
    "overview": "Overview",
    "zprofile": "Z-axis profile",
    "calibration": "Calibration",
    "segmentation": "Segmentation",
    "objects": "Objects",
}

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    state: str = "running"  # running → done | error
    message: str = ""
    result: dict | None = None

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "kind": self.kind, "state": self.state, "message": self.message, "result": self.result}


@dataclass
class StudioState:
    """Server-side state of the one workspace this server holds (single user)."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    stack: StackData | None = None
    sample_key: str = ""
    calibration: Calibration | None = None
    limits: dict[str, tuple[float, float]] = field(default_factory=dict)
    labels: np.ndarray | None = None
    last_analysis: dict[str, object] | None = None
    # Bumped whenever the images would change, so the browser can cache slices per version.
    version: int = 0
    job: Job | None = None

    @property
    def busy(self) -> bool:
        return self.job is not None and self.job.state == "running"

    def stack_payload(self) -> dict[str, object] | None:
        if self.stack is None or self.calibration is None:
            return None
        return {
            "key": self.sample_key,
            "name": self.stack.name,
            "source": self.stack.source,
            "shape": list(self.stack.shape),
            "channels": list(self.stack.channels),
            "defaultChannel": self.stack.default_channel,
            "calibration": calibration_summary(self.stack, self.calibration),
            "originalCalibration": self.stack.calibration.to_dict(),
            "currentSlice": self.stack.shape[0] // 2,
            "hasLabels": self.labels is not None,
            "version": self.version,
        }


def error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def create_app() -> Flask:
    app = Flask(__name__)
    state = StudioState()
    app.config["STUDIO_STATE"] = state

    def body() -> dict:
        values = request.get_json(silent=True)
        return values if isinstance(values, dict) else {}

    def start_job(kind: str, work: Callable[[Job], dict]):
        """Run ``work`` in a thread; only one job at a time. Call with the lock held."""
        if state.busy:
            return error("Another operation is still running. Wait for it to finish.", 409)
        job = Job(id=uuid.uuid4().hex, kind=kind)
        state.job = job

        def run() -> None:
            try:
                result = work(job)
            except Exception:  # never leave a job "running" forever
                log.exception("%s job failed", kind)
                result = {"ok": False, "error": "Unexpected server error. See the server log."}
            with state.lock:
                job.result = result
                job.state = "done" if result.get("ok") else "error"

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return jsonify({"ok": True, "jobId": job.id}), 202

    # ------------------------------------------------------------------ pages

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def get_state():
        with state.lock:
            return jsonify({
                "ok": True,
                "config": {
                    "categoryLabels": CATEGORY_LABELS,
                    "samples": [{"key": key, "label": SAMPLE_LABELS[key]} for key in SAMPLES],
                    "units": list(UNITS),
                    "methods": list(THRESHOLD_METHODS),
                    "defaults": DEFAULT_SETTINGS,
                    "references": REFERENCES,
                },
                "stack": state.stack_payload(),
                "analysis": state.last_analysis,
                "job": state.job.to_dict() if state.busy else None,
            })

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        with state.lock:
            if state.job is None or state.job.id != job_id:
                return error("Unknown job.", 404)
            return jsonify({"ok": True, "job": state.job.to_dict()})

    # ------------------------------------------------------------------ stack

    @app.post("/api/sample")
    def load_sample():
        key = body().get("key")
        loader = SAMPLES.get(key) if isinstance(key, str) else None
        if loader is None:
            return error("Choose one of the listed samples.")

        def work(job: Job) -> dict:
            job.message = "Downloading cells3d…" if key == "cells3d" else "Generating the synthetic stack…"
            try:
                stack = loader()
            except (RuntimeError, OSError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}
            job.message = "Preparing the viewer…"
            limits = contrast_limits(stack)
            with state.lock:
                state.stack = stack
                state.sample_key = key
                state.calibration = stack.calibration
                state.limits = limits
                state.labels = None
                state.last_analysis = None
                state.version += 1
                return {"ok": True, "stack": state.stack_payload()}

        with state.lock:
            return start_job("load", work)

    @app.post("/api/clear")
    def clear_stack():
        with state.lock:
            if state.busy:
                return error("Wait for the running operation to finish.", 409)
            had_stack = state.stack is not None
            state.stack = None
            state.sample_key = ""
            state.calibration = None
            state.limits = {}
            state.labels = None
            state.last_analysis = None
            state.version += 1
            return jsonify({"ok": True, "cleared": had_stack})

    @app.post("/api/calibration")
    def set_calibration():
        with state.lock:
            if state.stack is None:
                return error("Load a sample before changing its calibration.", 409)
            if state.busy:
                return error("Wait for the running operation to finish.", 409)
            try:
                calibration = parse_calibration(body())
            except ValueError as exc:
                return error(str(exc))
            # Physical values (volumes, depths) in an old analysis would be stale.
            cleared = state.last_analysis is not None
            state.calibration = calibration
            state.labels = None
            state.last_analysis = None
            state.version += 1
            return jsonify({"ok": True, "stack": state.stack_payload(), "analysisCleared": cleared})

    @app.get("/api/slice/<int:index>.png")
    def get_slice(index: int):
        with state.lock:
            stack, limits = state.stack, state.limits
            labels = state.labels if request.args.get("labels", "1") != "0" else None
        if stack is None:
            return error("Load a sample first.", 409)
        try:
            png = render_slice(stack, index, limits, labels)
        except IndexError as exc:
            return error(str(exc), 404)
        response = send_file(io.BytesIO(png), mimetype="image/png", download_name=f"slice_{index:03d}.png",
                             as_attachment=request.args.get("download") == "1")
        # The URL carries the state version, so a cached image is never stale.
        response.headers["Cache-Control"] = "private, max-age=600"
        return response

    # --------------------------------------------------------------- analysis

    @app.post("/api/analyze")
    def analyze():
        with state.lock:
            if state.stack is None or state.calibration is None:
                return error("Load a sample before analysing it.", 409)
            try:
                values = body()
                channel = parse_channel(state.stack, values)
                settings = parse_analysis_settings(values)
            except ValueError as exc:
                return error(str(exc))
            stack, sample_key, calibration = state.stack, state.sample_key, state.calibration

            def work(job: Job) -> dict:
                def progress(message: str) -> None:
                    job.message = message

                try:
                    analysis, labels = run_analysis(stack, sample_key, calibration, channel, settings, progress)
                except (ValueError, MemoryError) as exc:
                    return {"ok": False, "error": str(exc)}
                with state.lock:
                    # Stack and calibration cannot change while a job runs (see start_job).
                    state.labels = labels
                    state.last_analysis = analysis
                    state.version += 1
                    return {"ok": True, "analysis": analysis, "stack": state.stack_payload()}

            return start_job("analyze", work)

    # ------------------------------------------------------------------ files

    @app.get("/api/export/report.md")
    def export_report():
        with state.lock:
            analysis = state.last_analysis
        if analysis is None:
            return error("Run an analysis before exporting it.", 409)
        markdown = build_analysis_markdown(analysis).encode("utf-8")
        return send_file(io.BytesIO(markdown), mimetype="text/markdown", as_attachment=True,
                         download_name=f"{safe_name(analysis['name'], 'Stack')}.md")

    @app.get("/api/export/objects.csv")
    def export_objects():
        with state.lock:
            analysis = state.last_analysis
        if analysis is None:
            return error("Run an analysis before exporting objects.", 409)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "objects.csv"
            write_objects_csv(path, analysis)
            data = path.read_bytes()
        return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                         download_name=f"{safe_name(analysis['name'], 'Stack')}_objects.csv")

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stack Analysis Studio web server")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 makes it reachable from other computers")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # debug stays off: Flask's debugger would let anyone on the network run code.
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
