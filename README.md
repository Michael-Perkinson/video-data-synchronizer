# Video Data Synchronizer

[![CI](https://github.com/Michael-Perkinson/video-data-synchronizer/actions/workflows/ci.yml/badge.svg)](https://github.com/Michael-Perkinson/video-data-synchronizer/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Turn a recording and a time-series signal into a synchronized visual story for talks, posters, and publications. Video Data Synchronizer helps behavioural researchers preview the alignment, tailor the video and traces, and export them together — all on their own computer, without uploading source data. Load a video and a CSV or XLSX file, set the offset, and see the result before export.

**This is a visualization tool, not an analysis tool.** Exports are intended to illustrate behaviour alongside a signal, not to serve as quantitative evidence. One fixed offset is applied to the whole clip; there is no clock-drift correction. See [Timing and scientific interpretation](docs/timing-and-transforms.md) for what this means for your data.

## Features

- Load common video files supported by OpenCV and CSV or XLSX data
- Crop video and adjust colour
- Select and overlay one or two numeric traces
- Convert time columns labelled in milliseconds, seconds, minutes or hours
- Apply baseline z-scoring and choose either raw trace values or a first-value subtraction for synchronized display
- Adjust video/data offset and preview synchronized playback
- Export a combined video and trace view
- Export several playback speeds sequentially without running concurrent jobs
- Remember reusable trace colours, signal channels, preview speed, and export settings (video colour correction applies to the current video and is saved only in projects);
  save panel proportions with projects and appearance presets
- Save and load recording-independent appearance presets as versioned JSON
- Recover unsaved work, reopen recent projects, and launch a synthetic demo from Help
- Work locally without an account or network connection
- Automatically record every export attempt and find its settings in File → Export History

## Installation

Python 3.11 or newer is required. From a fresh clone:

```powershell
git clone https://github.com/Michael-Perkinson/video-data-synchronizer.git
cd video-data-synchronizer
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
video-data-sync
```

On macOS or Linux, activate with `source .venv/bin/activate`. Developers should install with `python -m pip install -e ".[dev]"` instead of the plain `pip install .` above — the `-e` (editable) flag makes `src/` changes take effect immediately without reinstalling, and a stale non-editable install will make `pytest` exercise old code. Then enable local checks with `pre-commit install`.

To install a GitHub release, download the matching wheel asset. From the directory containing it, run `python -m pip install ./video_data_synchronizer-1.0.0-py3-none-any.whl` (replace the version with the one in the downloaded filename). This wheel is platform-independent, but it is not a standalone executable: Python 3.11 or newer is still required, and pip installs the runtime dependencies. The project does not yet publish a packaged macOS app.

The authoritative dependency and package definition is `pyproject.toml`.

## Quick start

Start the application with `video-data-sync`, then choose **Help → Open Demo Project** to try a synthetic recording and signals. This works with either a source installation or an installed wheel; the application generates or reuses the demo in its local cache.

To generate separate demo input files from a source checkout, run the following from the repository root (or pass a different output directory). The `examples/` script is not included in the installed wheel:

```powershell
python examples/generate_demo.py
```

This creates a six-second constant-frame-rate MP4 and a CSV with two fully synthetic example signals in `examples/generated_demo`. Both event flashes in the video coincide with peaks in the response signal, so the correct starting offset is `0 s`.

1. Open the application and choose a video. The first tab shows the full duration and the selected-clip duration.
2. Set the clip range and crop if needed, then click **Apply Video Settings**. Click it even when you do not need a crop; this applies the selected clip, orientation, crop, and colour adjustments and unlocks the animated preview once data is loaded.
3. On **Full Data Plot**, load a CSV or XLSX workbook and check the detected time and signal columns. Use **Time column** and **Time unit** to correct the detection when needed.
4. Choose the signal or signals, baseline/z-score options, trace transform, and plot appearance. The UI options are **Raw values** and **Subtract clip-start value**; the latter is the default and subtracts each trace's first finite value in the synchronized clip. These transforms affect the animated preview and export; the Full Data Plot continues to show the selected column's values.
5. Set **Sync Offset** on **Full Data Plot**. The convention is `data time = video time + offset`, so a positive offset selects later data for a given video time. Return to **Animated Plot** and play the preview to check the alignment; use **Reset** to replay from the clip start.
6. Review the side-by-side composition, then use **Export Video (1x)** (the label follows the selected Preview speed) for one video or **Export Speeds…** for selected variants. Batch variants are named with speed suffixes and run one after another. Advanced composition settings can replace the automatic time and signal scale units while retaining the measured numeric scale length.

At the default window size the Video and Full Data Plot settings columns show all their controls; only in a smaller window do you need to scroll them, and keyboard focus also brings a control into view. The Animated Plot playback and export controls wrap onto two rows when needed.

The preview splitter is user-resizable and defaults to at most 68% video width so the plot stays usable.
Saved project or preset proportions are restored up to that limit; older video-heavy splits return to
the aspect-aware default. These proportions are not a global preference.

Use the **Presets** menu to exchange reusable appearance settings without including source paths, clip bounds, crop, offset, baselines, or fixed y-limits. Two importable examples and the preset safety boundaries are described in [Appearance presets](docs/appearance-presets.md).

Never overwrite irreplaceable source media. Inspect the beginning, middle, and end of every exported video before using it.

More detail is available in the [user guide](docs/user-guide.md), [input format reference](docs/input-formats.md), [timing and transform notes](docs/timing-and-transforms.md), and [troubleshooting guide](docs/troubleshooting.md).

## Supported inputs

- Video: uses the OpenCV Python package; no separate FFmpeg installation is required. The file chooser offers MP4, AVI, MOV, and MKV, but formats and codecs readable or writable depend on the OpenCV build on your system
- Tables: CSV and XLSX
- Time: a numeric, strictly increasing time column anywhere in the file. Detection prioritizes names containing `time`, then seconds/minutes units, then millisecond/hour units written as words or as a bracketed suffix such as `(ms)` or `[h]`, then evenly-spaced numeric columns. Use the **Time column** selector to correct the choice and **Time unit** to override conversion. Naming the column `Time (s)` or `Time (min)` is recommended. Sampling may be sparse or high-rate; timestamps must be strictly increasing, but do not need a uniform interval.
- Signals: any other numeric columns can be selected as data regardless of their names; text columns are ignored. Missing values may limit plotting or baseline calculations.

For reproducible work, use constant-frame-rate video and include units in every column name. See [input formats](docs/input-formats.md).

## Privacy and example data

The program performs file reading, plotting, and export locally. It has no telemetry or upload feature. File paths are stored in local preferences and export records. Each successful export has an adjacent `.run.json` settings record; these records include source paths and column names, so review them before sharing. File → Export History searches all local runs and opens their folders.

The generator and its synthetic outputs are provided under this repository's MIT License. Generated files are ignored so repeated tutorials do not dirty the repository.

## Development

```powershell
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy src
pyright
python -m build
```

Release and packaging guidance is in [docs/releasing.md](docs/releasing.md).

## Known limitations

- No clock-drift correction; one offset applies to the whole clip.
- Constant frame rate is assumed. Variable-frame-rate sources should be transcoded to constant frame rate before use.
- Codec availability and output compatibility depend on the local OpenCV/operating-system build.
- OpenCV processing is limited to one compute thread and exports are serialized; a platform codec may still use its own small internal worker thread.
- Time-column inference is a convenience, not validation of experimental timing.
- The application has not yet been independently validated as a regulated or clinical analysis tool.

## Citation and license

Citation metadata is provided in [CITATION.cff](CITATION.cff). This project is released under the [MIT License](LICENSE). Third-party dependencies have their own licenses; review their license terms and any required notices when redistributing the software or its dependencies. The dependency list is in `pyproject.toml`.

Experimental data, footage, images, and other media may have separate rights and are not automatically covered by the software license. Before publishing or redistributing media, document its source, creator, license or permission, and any applicable consent or approval constraints; retain attribution and notices where required. Do not assume rights from possession alone.
