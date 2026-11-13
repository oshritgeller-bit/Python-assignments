"""Checks for web_app.py through Flask's test client. Run with ``python test_web_app.py`` (or pytest).

No browser or network is needed: the synthetic sample is generated locally.
"""

from __future__ import annotations

import io
import threading
import time

from PIL import Image

from analysis_service import SAMPLE_LABELS, run_analysis
from reporting import ANALYSIS_DATA_START
from stack_analysis import SAMPLES, synthetic_stack
from web_app import create_app


def wait(client, reply, timeout=60.0):
    """Poll a job (as the browser does) and return its result."""
    assert reply.status_code == 202, reply.get_json()
    job_id = reply.get_json()["jobId"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
        if job["state"] != "running":
            return job["result"]
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def loaded_client():
    client = create_app().test_client()
    result = wait(client, client.post("/api/sample", json={"key": "synthetic"}))
    assert result["ok"], result
    return client, result["stack"]


def test_page_and_initial_state():
    client = create_app().test_client()
    page = client.get("/")
    assert page.status_code == 200 and b"/static/app.js" in page.data
    state = client.get("/api/state").get_json()
    assert state["ok"] and state["stack"] is None and state["analysis"] is None and state["job"] is None
    assert {sample["key"] for sample in state["config"]["samples"]} == {"cells3d", "synthetic"}
    assert client.get("/static/app.js").status_code == 200


def test_requests_without_a_stack_are_refused():
    client = create_app().test_client()
    assert client.post("/api/sample", json={"key": "nope"}).status_code == 400
    assert client.post("/api/sample", data="not json").status_code == 400
    for reply in (
        client.post("/api/analyze", json={}),
        client.post("/api/calibration", json={"z": 1, "y": 1, "x": 1, "unit": "µm"}),
        client.get("/api/slice/0.png"),
        client.get("/api/export/report.md"),
        client.get("/api/export/objects.csv"),
    ):
        assert reply.status_code == 409 and reply.get_json()["ok"] is False
    assert client.get("/api/jobs/unknown").status_code == 404


def test_load_sample_and_render_slices():
    client, stack = loaded_client()
    assert stack["shape"] == [48, 160, 160] and stack["channels"] == ["blobs"]
    assert client.get("/api/state").get_json()["stack"]["name"] == stack["name"]
    reply = client.get(f"/api/slice/{stack['currentSlice']}.png?v={stack['version']}")
    assert reply.status_code == 200 and reply.mimetype == "image/png"
    image = Image.open(io.BytesIO(reply.data))
    assert image.size == (160, 160) and image.mode == "RGB"
    assert client.get("/api/slice/48.png").status_code == 404
    download = client.get("/api/slice/0.png?download=1")
    assert "attachment" in download.headers["Content-Disposition"]


def test_calibration_validation():
    client, stack = loaded_client()
    for bad in ({"z": 0, "y": 1, "x": 1, "unit": "µm"}, {"z": "abc", "y": 1, "x": 1, "unit": "µm"},
                {"z": 1, "y": 1, "x": 1, "unit": "inch"}):
        reply = client.post("/api/calibration", json=bad)
        assert reply.status_code == 400 and reply.get_json()["error"], bad
    reply = client.post("/api/calibration", json={"z": "1,0", "y": 0.5, "x": 0.5, "unit": "nm"}).get_json()
    assert reply["ok"] and reply["stack"]["calibration"]["z"] == 1.0
    assert reply["stack"]["calibration"]["unit"] == "nm"
    assert reply["stack"]["version"] > stack["version"]


def test_analysis_job_matches_the_shared_logic():
    client, _stack = loaded_client()
    bad = client.post("/api/analyze", json={"method": "manual"})
    assert bad.status_code == 400 and "manual threshold" in bad.get_json()["error"].lower()
    result = wait(client, client.post("/api/analyze", json={"sigma": "0.5", "minVolume": "5"}))
    assert result["ok"], result
    analysis = result["analysis"]
    assert result["stack"]["hasLabels"] is True
    # Same business logic as the desktop app: call it directly and compare.
    stack = synthetic_stack()
    expected, _labels = run_analysis(stack, "synthetic", stack.calibration, "blobs", analysis["settings"])
    assert analysis["summary"]["count"] == expected["summary"]["count"]
    assert analysis["objects"] == expected["objects"]
    # The state survives a page reload.
    assert client.get("/api/state").get_json()["analysis"]["summary"] == analysis["summary"]
    # Changing calibration clears the analysis (its physical values are stale).
    reply = client.post("/api/calibration", json={"z": 0.5, "y": 0.25, "x": 0.25, "unit": "µm"}).get_json()
    assert reply["analysisCleared"] is True and reply["stack"]["hasLabels"] is False
    assert client.get("/api/state").get_json()["analysis"] is None


def test_exports():
    client, _stack = loaded_client()
    result = wait(client, client.post("/api/analyze", json={}))
    report = client.get("/api/export/report.md")
    assert report.status_code == 200 and "attachment" in report.headers["Content-Disposition"]
    assert ANALYSIS_DATA_START in report.data.decode("utf-8")
    csv = client.get("/api/export/objects.csv")
    lines = csv.data.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("Label,") and len(lines) == result["analysis"]["summary"]["count"] + 1


def test_one_job_at_a_time():
    release = threading.Event()

    def slow_loader():
        release.wait(10)
        return synthetic_stack()

    SAMPLES["slow"] = slow_loader
    SAMPLE_LABELS["slow"] = "Slow test sample"
    try:
        client = create_app().test_client()
        first = client.post("/api/sample", json={"key": "slow"})
        job_id = first.get_json()["jobId"]
        assert client.get(f"/api/jobs/{job_id}").get_json()["job"]["state"] == "running"
        assert client.get("/api/state").get_json()["job"]["id"] == job_id  # a reloaded page can resume it
        assert client.post("/api/sample", json={"key": "synthetic"}).status_code == 409
        assert client.post("/api/clear", json={}).status_code == 409
        release.set()
        assert wait(client, first)["ok"]
        assert client.post("/api/clear", json={}).get_json() == {"ok": True, "cleared": True}
    finally:
        release.set()
        del SAMPLES["slow"], SAMPLE_LABELS["slow"]


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        print(name)
        fn()
    print(f"All {len(tests)} tests passed.")
