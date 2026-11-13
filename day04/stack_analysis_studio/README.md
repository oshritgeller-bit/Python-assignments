# Day 04 – Stack Analysis Studio on the web (Flask)

## Overview

This folder starts as a copy of [Day 03](../../day03/stack_analysis_studio/), the desktop app for analysing 3D image stacks (z-stacks). Day 04 adds a **web application** built with Flask. It uses the same "business logic" and offers it through a browser.

There are two ways to use the same analysis:

| | Desktop (Day 03) | Web (Day 04) |
|:--|:--|:--|
| Start | `python run_app.py` | `python web_app.py`, then open http://127.0.0.1:5000 |
| Interface | PySide6 window: web UI + embedded napari | Any browser: same web UI + a slice viewer |
| UI ⇄ Python | Qt WebChannel (`@Slot` methods) | HTTP + JSON (`fetch` → Flask routes) |
| Where the analysis runs | In the app's process | On the server |

The analyses are the same in both: calibration and scale bar, z-axis profiles, and 3D segmentation with object statistics. For details, see the [Day 03 README](../../day03/stack_analysis_studio/README.md).

## Architecture

```
 BROWSER                                     SERVER  (web_app.py, Flask)
 ┌──────────────────────────┐   HTTP/JSON    ┌───────────────────────────────────────┐
 │ templates/index.html     │ ─────────────▶ │ routes /api/...                       │
 │ static/app.js            │   fetch()      │   │                                   │
 │  forms, charts, tables,  │ ◀───────────── │   ├─ analysis_service.py  ◀── shared ─┼── app.py (desktop)
 │  slider, localStorage    │  JSON / PNG    │   ├─ stack_analysis.py    ◀── shared  │
 │  preferences             │                │   ├─ reporting.py         ◀── shared  │
 └──────────────────────────┘                │   ├─ slice_render.py  (PNG of a slice)│
                                             │   └─ StudioState: stack, labels,      │
                                             │      last analysis, running job       │
                                             └───────────────────────────────────────┘
```

**All the computation stays in Python on the server.** The browser never receives the 3D array. It gets JSON numbers for the charts and tables, and one PNG image per slice.

### Decisions

1. **Shared business logic.** In Day 03, input parsing, validation and building the analysis were inside the Qt class `AnalysisBridge`. They are now in `analysis_service.py`, which has no Qt. The desktop `app.py` and the web `web_app.py` both call it, so both give identical results. `test_web_app.py` checks this by comparing the web result with a direct call.
2. **Slice viewer instead of napari.** napari is a desktop OpenGL program and cannot run in a browser. `slice_render.py` colours the channels, adds the object overlay on top, and returns a PNG. The browser shows it in an `<img>` with a z-slider. The scale bar is drawn in HTML on top of the image, so switching it on or off needs no request. Image URLs carry a state version (`?v=3`), so the browser can cache slices and never shows an out-of-date one.
3. **Long operations are background jobs.** Analysing cells3d takes 10–15 s. `POST /api/analyze` (and loading a sample) starts a thread and immediately returns `202 {jobId}`. The browser polls `GET /api/jobs/<id>` and shows the job's progress message ("Segmenting 3D objects…"). Only one job runs at a time; other changes are refused with `409` until it finishes. Unlike the desktop app, the page doesn't freeze while it waits. If you reload the page during a job, it picks the job up again.
4. **State lives on the server.** The server keeps the loaded stack, calibration, labels and last analysis. A page reload therefore restores the workspace. The server holds **one** workspace, which is right for a personal tool. Supporting several users would need a workspace per session.
5. **Preferences live in the browser.** Theme, font size, visible result tabs and reference links are saved in `localStorage` (QSettings in the desktop app).
6. **Scope.** Kept: load sample, calibration, analysis, all result tabs, global search (Ctrl K), chart/table ⇄ slice linking, Markdown report and CSV download, plus a "Download PNG" button for the current slice. Left out: the 3D view, reset view, maximise/full-screen, and importing a report.

### API

| Method | Route | Purpose |
|:--|:--|:--|
| GET | `/` | The page |
| GET | `/api/state` | Configuration + current stack, analysis and running job |
| POST | `/api/sample` `{key}` | Load a sample → job |
| POST | `/api/clear` | Clear the stack |
| POST | `/api/calibration` `{z, y, x, unit}` | Change voxel size (clears the analysis) |
| POST | `/api/analyze` `{channel, method, sigma, …}` | Run the analysis → job |
| GET | `/api/jobs/<id>` | Job state: `running`, `done` or `error`, message, result |
| GET | `/api/slice/<i>.png?labels=1&download=1` | One slice as PNG |
| GET | `/api/export/report.md` | Markdown report download |
| GET | `/api/export/objects.csv` | Objects table download |

Errors come back as `{"ok": false, "error": "…"}` with status 400 (bad input), 404 or 409 (wrong state or busy).

## Project layout

| File | Purpose |
|:--|:--|
| `web_app.py` | **New.** Flask app: routes, server state, background jobs |
| `analysis_service.py` | **New.** Qt-free parsing, validation and `run_analysis`, shared by both apps |
| `slice_render.py` | **New.** Renders a slice (channels + object overlay) to PNG |
| `templates/index.html`, `static/` | **New.** Web UI, adapted from `web/`: `fetch()` instead of the Qt bridge, slice viewer pane |
| `test_web_app.py` | **New.** Tests the web app through Flask's test client |
| `stack_analysis.py`, `reporting.py` | Business logic, unchanged from Day 03 |
| `app.py` | Desktop app, now importing its logic from `analysis_service.py` |
| `run_app.py`, `main.py`, `napari_panel.py`, `web/` | Desktop app, unchanged |
| `test_stack_analysis.py`, `test_napari_panel.py`, `test_app_e2e.py` | Day 03 tests (all still pass) |

## How to Run

Requires **Python 3.11 or newer**.

**Web version:**

```bash
pip install -r requirements-web.txt      # Flask, Pillow and the analysis libraries (no Qt)
python web_app.py                        # http://127.0.0.1:5000 or http://localhost:5000
```

To make it reachable from other computers **on the same network**:

```bash
python web_app.py --host 0.0.0.0
# or: FLASK_APP=web_app.py flask run --host 0.0.0.0
```

Then open `http://<this computer's IP>:5000` on the other computer. Press Ctrl-C to stop. The server never starts in Flask debug mode, because the debugger would let anyone on the network run code.

**Desktop version** (as in Day 03):

```bash
python run_app.py
```

**Tests:**

```bash
python test_web_app.py          # web: routes, jobs, validation, PNG slices, downloads, same result as desktop logic
python test_stack_analysis.py   # analysis
python test_napari_panel.py     # desktop napari panel
python test_app_e2e.py          # desktop app driven through its UI
```

## Limitations

- One shared workspace per server: two people using the same server at once would see each other's stack.
- Flask's built-in server is for development. A real deployment (e.g. on Linode) would run it behind a production server such as gunicorn.
- The slice viewer is 2D only, with a fixed contrast (0.5–99.8 percentile per channel, as in the desktop app).
- Importing a report is only in the desktop app.
- All Day 03 analysis limitations still apply (classical thresholding, no deconvolution, unreliable sphericity for very small objects).

## AI-Assisted Development

Claude (Opus 5.5) in Visual Studio Code planned the architecture with me. I made the decisions: server-rendered PNG slices, background jobs with polling, the core feature set, and reusing the Day 03 UI. Claude then wrote the code and tested it: unit tests with Flask's test client, a browser run in headless Chrome (load → analyse → tabs → row-to-slice linking → downloads → reload → light theme → phone width), and the Day 03 desktop tests again after the refactor.
