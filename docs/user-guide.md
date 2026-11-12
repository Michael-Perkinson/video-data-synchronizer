# User guide

## Before starting

Work from copies of source files. Use a constant-frame-rate video and a table with a numeric, strictly increasing time column. The application aligns the streams with one fixed offset; it does not compensate for clocks that diverge during a recording.

## Import and prepare video

On **Video Cropping**, open a video and confirm its full duration and frame rate. Choose the clip boundaries, draw a crop only if needed, and use the **Rotate 90°** button to cycle through 0°, 90°, 180°, and 270° clockwise; the current angle appears beside the button and an existing crop rotates with the image. Click **Apply Video Settings** to send the selected clip, crop, orientation, and colour adjustments to the animated preview. Click it even when no crop is needed. Rotation is saved with the project. The selected-clip duration updates as the start and end fields change. After changing video settings, apply them and inspect the animated preview before export; export also applies the current Video Cropping settings. The file chooser offers MP4, AVI, MOV, and MKV, but codec support depends on the installed OpenCV build, so a file extension alone does not guarantee compatibility.

## Import data

On **Full Data Plot**, load a CSV or XLSX file containing numeric time-series data, such as photometry, sensor, or behavioral measurements. Confirm the detected time column and its units rather than relying on inference. Select one or two numeric signal columns. If the inferred duration is implausible by a factor of 60, check whether the column name clearly says seconds or minutes.

Use **Time column** to select a different clock and the **Time unit** list beside it for milliseconds, seconds, minutes, or hours. At the default window size the settings columns of **Video Cropping** and **Full Data Plot** show all their controls without scrolling; in a smaller window, scroll the column (keyboard focus also brings a control into view). All choices require finite, strictly increasing timestamps. If automatic detection fails during a manual file import, an error appears first and the load flow then offers a separate time-column chooser; that fallback is not offered when opening a saved project. An invalid or empty import leaves the current recording intact. Changing the clock or its units clears derived z-score columns; calculate them again using the intended baseline in seconds.

## Transform and display traces

The full-data plot preserves the selected column's scale. Z-scoring expresses values relative to the selected baseline mean and standard deviation. Choose a baseline appropriate for the measurement and scientific comparison, and verify that it contains enough finite, varying samples.

The **Trace transform** options are **Raw values** and **Subtract clip-start value**; subtracting the clip-start value is the default. The latter rebases each synchronized trace to its first finite value in the clip. These transforms affect the animated preview and export, not the Full Data Plot, which preserves the selected column's values. Preview and export use the same project-saved choice. Subtraction also applies when the selected column already contains z-scores; it is not domain-specific preprocessing, baseline correction, or a ΔF/F calculation. Fixed Y limits are applied after this transform, unless **Animated Plot → Advanced → Graph range → Limit the plotted signal range** is enabled; that graph-range setting overrides the Fixed Y limits from Full Data Plot. Record the choices with the analysis.

In **Data Column**, enter an optional name in **Legend** or **2nd legend** for either selected signal; names appear in legends when multiple signals are shown and are saved with projects and appearance presets. For a single trace, the full-data plot uses the custom name as its y-axis label only when no recognized unit is inferred; the animated preview and export suppress single-trace legends.

In **Animated Plot → Advanced → Graph range**, automatic scaling is the default. To limit the graph, turn on **Limit the plotted signal range** and choose how much of the full range to show (default: 70%). The graph starts at the lowest signal value in the current clip; the percentage controls how far upward it shows, and higher values are clipped. For example, if the signal runs from −4 to 6, choosing 50% shows −4 to 1. The percentage is recalculated when the signal range changes and is saved with the project and appearance preset. When enabled, this range setting overrides Fixed Y limits on Full Data Plot. Scale bars remain pinned to the selected plot-pane corner. In automatic mode, traces may be translated relative to the shared scale-bar origin and adjusted further if needed for clearance. Fixed-range mode reserves an empty band outside the data interval so the signal minimum is preserved without shifting trace values. Scale-bar labels start at the shared origin rather than being centered along their bars.

## Align

Set **Sync Offset** on **Full Data Plot**, then return to **Animated Plot** and play the video to check alignment. The offset control is not on the preview tab. Find an event visible in both recordings, adjust the offset, and verify another event near the end. The preview has no seek slider: use **Reset** to return to the clip start and play through to inspect later events. In this release, a mismatch that grows over time cannot be corrected inside the application; check acquisition clocks or prepare corrected source data instead.

The convention is `selected data time = video time + offset`. A positive offset pairs a video frame with a later point in the data file.

## Preview and export

Review the clip in the side-by-side preview. The video panel defaults to at most 68% of the width so the plot remains usable; drag the splitter to adjust the balance. Saved project and preset proportions are restored up to that limit, while older video-heavy splits return to the aspect-aware default. A drag is kept for the current session but is not a global preference. The preview starts at the clip beginning; use **Play**, **Pause**, and **Reset** to play through and replay it. The single-export button is labelled **Export Video (<speed>)** and exports at the selected Preview speed. **Export Speeds…** has its own speed selection and shows the number of videos and speed-suffixed filenames before export. It does not change the Preview speed. Variants receive suffixes such as `_0.5x` and `_2x`; they are never rendered in parallel. Failed or cancelled in-progress outputs are discarded without replacing an existing destination. Reopen every completed output and check duration, dimensions, synchronization, labels, crop, and trace scale.

The application remembers the most recently used trace colours, available channel names, line width, transform, preview speed, de-flicker choice, composition options, and file folders. Preview panel proportions are saved with individual projects and appearance presets, not as a global preference. A saved project retains its own scientific state without replacing those personal defaults. Video colour correction (brightness, contrast, gamma, and RGB gains) is not a remembered preference: it applies only to the current video, every newly opened video starts with the default colours, and the values are saved only in projects (and in the unsaved-work recovery file). Use **Reset** in Colour Correction to restore identity settings for the current video. A remembered channel name is used only when it exists in the newly loaded table; otherwise the normal channel-detection fallback applies. If a remembered line width is damaged or not a finite number, the Data tab uses the 1.5 default.

If the legacy folder preference is damaged or unreadable, file selection starts in your home folder. Choosing a new folder repairs damaged legacy preferences while retaining other valid settings; an unreadable settings file is not overwritten.

The **Presets** menu saves or loads these recording-independent settings as versioned JSON. Colour mappings follow matching column names. Presets intentionally exclude source paths, clip bounds, crop, offset, z-score baselines, and fixed y-limits. In Advanced composition settings, optional custom time and signal units replace the automatic unit text, while the measured numeric scale length remains visible. See [Appearance presets](appearance-presets.md) for two ready-to-import examples and guidance on adapting column mappings safely.

Choose **Help → Open Demo Project** for a fully synthetic six-second example with matching video flashes and trace peaks. Demo generation runs in the background and its files are cached locally.

While a project has unsaved changes, the application writes a debounced recovery snapshot separate from the project file. After an abnormal exit it offers to recover that snapshot. Successfully saving or explicitly discarding clears it. **File → Open Recent** lists up to eight projects and allows the list to be cleared; missing entries are removed when selected.

Export is intentionally conservative: only one export queue can run, OpenCV compute uses one thread, and repeated export clicks are ignored while work is active. A codec supplied by the operating system or OpenCV may still manage an internal worker thread.

## Reproducibility checklist

Every export attempt now creates a permanent local record automatically. Successful exports also save a settings file beside the video, named `<output>.<run-id>.run.json`. Re-exporting the same filename preserves earlier records, although the video itself is replaced after confirmation. Failed and cancelled attempts remain in history; earlier completed batch variants remain available.

Choose **File → Export History** and search by source video, data filename, project name, output name, speed (for example `2x`), or run ID. Select a result to read its settings, **Open output folder**, or **Restore settings** into an unsaved project. The **Open history folder** button locates the records without needing to know the operating system's application-data path. Refresh the list to see a currently running export's latest status.

Records contain start/finish times, status/errors, source SHA-256 fingerprints, application/OpenCV versions, clip bounds, crop, offset, selected time/signal columns, baseline recipes and statistics, trace transform/colours, video colour adjustments (including the actual lookup table), de-flicker, layout, speed, and actual output dimensions/FPS/codec and automatic scales. An output fingerprint distinguishes different exports that used the same filename. Each speed variant has its own record. Identifying large source files can take time before frame progress begins; Cancel remains available during source hashing.

Restore uses the original files or the existing relink dialog if they have moved. Changed file size or modification time prompts before restoration; the full fingerprints remain in the record for exact identification. Restoration cannot recreate missing source media. Data changed on disk after import must be reloaded before exporting. Keep source files and run records together with the analysis record.

History is stored locally and is not uploaded. Records include paths and column names: inspect them before sharing. If the application cannot write its initial history record, export stops with an error instead of producing an unlogged output. An abrupt process exit may leave a record marked `running` without a finish time. If only the adjacent settings copy fails after a video has been saved, the error reports that the video exists and gives the permanent history record location.
