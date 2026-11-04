# Day 03

For this assignment I chose to build an application: Stack Analysis Studio, a desktop app for analysing 3D image stacks (z-stacks) with an embedded napari viewer. It does scale bar and calibration, z-axis profiles, and 3D segmentation with object statistics.

- App, how to run it and the development process: [stack_analysis_studio/README.md](stack_analysis_studio/README.md)
- My prompt: [STACK_ANALYSIS_STUDIO_PROMPT.md](STACK_ANALYSIS_STUDIO_PROMPT.md)

I used Claude (Opus 5.5) in VS Code to generate the app from that written prompt. The structure and style follow a reference project, [Protein Analysis Studio](https://github.com/ron075/course_assignments/tree/main/day03).

## Data

The app uses sample data, not files stored in this folder: `cells3d` from scikit-image (downloaded on first use) and a synthetic stack that the app generates.
