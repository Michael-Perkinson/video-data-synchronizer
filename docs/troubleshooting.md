# Troubleshooting

## The video will not open

Confirm that the file plays elsewhere and try a short path without unusual characters. Codec support varies by OpenCV build. Transcoding a copy to constant-frame-rate H.264 MP4 can improve compatibility; retain the original and document conversion settings.

## Excel loading fails

Install from `pyproject.toml`, which includes `openpyxl`: `python -m pip install -e .`. Ensure the workbook is `.xlsx`, not a renamed legacy `.xls` file.

## The wrong time or signal column is selected

Use explicit headers such as `Time (s)` and numeric signal names. Remove title rows or merged cells. Check that time values are finite and strictly increasing, and that signal values were parsed as numbers. On Full Data Plot, select the intended **Time column** and **Time unit** if automatic detection or unit inference chose incorrectly.

## Data appear shifted by 60×

The time unit is probably missing or ambiguous. Rename the column to state milliseconds, seconds, minutes or hours explicitly (for example `Time (ms)` or `Time (h)`) and reload it, or choose the unit in **Time unit**. A factor of 1000 or 3600 points to the same problem with milliseconds or hours.

## A comma will not type into a number field

Numeric fields (offset, start/end times, y-limits, baseline times) use "." as the decimal separator on all systems, including systems set to a comma-decimal locale; type `1.5`, not `1,5`.

## Events align at the start but not the end

This indicates possible clock drift, dropped frames, trimming, or variable frame rate. This release intentionally has no drift correction. Do not choose a compromise offset; investigate or correct the sources externally.

## Export fails or will not play

Choose a writable destination with free disk space, shorten the clip for a test, and avoid overwriting an open file. Encoder availability varies. Delete invalid partial output only after confirming it is not a source file, then try a standard MP4 destination.

## Playback is slow

Preview performance depends on decoding, resolution, plotting, and storage speed. Close other intensive applications, use local storage, or test with a shorter/lower-resolution copy. Export timing should still be checked in the completed file.

## Reporting a problem

Include application version, OS, sanitized media metadata, relevant column names, and exact steps. Never post confidential recordings or identifiable paths to a public issue.
