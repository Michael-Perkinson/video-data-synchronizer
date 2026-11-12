# Timing and scientific interpretation

## Alignment model

This version uses:

```text
data_time = video_time + offset
```

The offset is constant throughout the clip. There is no clock-drift correction, time scaling, or multi-anchor warping. Video time is derived from frame position and reported frame rate, which assumes constant frame rate and trustworthy metadata.

Set **Sync Offset** on Full Data Plot, then check the result in Animated Plot. Verify alignment using at least two independently recognizable events separated in time; the preview has Play, Pause, and Reset controls but no seek slider. If the first event aligns but the second does not, stop: changing the single offset cannot fix that mismatch. Investigate source clocks, dropped frames, trimming, or variable-frame-rate encoding outside the application.

## Time units

Headers containing recognizable milliseconds, seconds, minutes or hours terms are converted to seconds. Unlabelled time is assumed to be seconds. This inference is not evidence that acquisition timing is correct.

## Raw and z-scored data

Raw display uses the selected numeric signal column. Baseline z-score is calculated as:

```text
z = (value - baseline_mean) / baseline_sample_standard_deviation
```

The baseline window is user-selected. A zero-variance or empty baseline is invalid. Z-scoring changes interpretation and does not perform domain-specific preprocessing such as filtering or artifact correction. For photometry data specifically, it does not perform motion correction, isosbestic regression, bleaching correction, or ΔF/F calculation.

The **Trace transform** control on Full Data Plot offers two presentation transforms for the synchronized preview and export:

- **Raw values** preserves each selected column's values.
- **Subtract clip-start value** subtracts the first finite value in the selected clip from every displayed sample in that trace. This is the default.

The Full Data Plot itself continues to show the selected column's values without this presentation transform. The same transform is used in the animated preview and export and is saved in the project. Subtraction also applies to a selected z-score column; it does not recompute its baseline. Fixed Y limits from Full Data Plot are entered in the raw units of the selected column, not in transformed units. In the animated preview and export, both limits are moved by the same amount as the plotted data: the lowest raw value in the clip window (across the shown traces) is subtracted from each limit, and that lowest raw value is drawn at 0. For example, if the clip's raw values run from 2.0 to 5.0 and the fixed limits are 1 and 6, the axis runs from -1 to 4 and the trace from 0 to 3. With one trace, this matches the trace for both transforms. With two traces and **Subtract clip-start value**, each trace is moved by its own first value, so the limits do not line up with exact raw values of either trace. When **Limit the plotted signal range** is enabled in Animated Plot's Advanced graph-range settings, that setting overrides the fixed limits. Record the transform and range choices and confirm that exported units and limits remain meaningful.

## Appropriate use

The software creates synchronized visualizations from video and numeric time-series data. It does not validate acquisition hardware, establish causality, or replace a documented preprocessing pipeline. For example, photometry data should be processed according to the user's established photometry workflow before or alongside use of this application. Preserve raw files and report all transformations used in publications.
