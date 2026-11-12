# Input formats

## Tables

CSV (`.csv`) and Excel (`.xlsx`) files are supported. Excel reading uses `openpyxl`. A simple wide table is recommended:

```text
Time (s),sensor_A,sensor_B
0.00,0.012,-0.004
0.05,0.015,-0.003
0.10,0.011,-0.005
```

Requirements and recommendations:

- Include a header row.
- Make time numeric and strictly increasing; evenly sampled data is preferable.
- Put the time unit in its header: `Time (s)`, `time_sec`, `Time (min)`, `time_minutes`, `Time (ms)`, `time_msec`, `Time (h)`, or `time_hours`. Recognized units are milliseconds (`ms`, `msec`, `millisecond(s)`), seconds (`s`, `sec`, `second(s)`), minutes (`min`, `minute(s)`) and hours (`h`, `hr`, `hour(s)`), each only when no other letter is directly before or after it. These units convert the time column to seconds.
- Use numeric signal columns and meaningful names with units/transform where applicable.
- Avoid merged cells, multiple header rows, formulas dependent on external workbooks, and explanatory rows above the header.
- Inspect missing values, duplicated timestamps, and non-finite values before import.

Automatic time-column choice uses these steps, in this order:

1. The first numeric column whose name contains `time`.
2. The first numeric, strictly increasing column whose name has a seconds or minutes unit (`s`, `sec`, `second(s)`, `min`, `minute(s)`).
3. The first numeric, strictly increasing column whose name has `millisecond(s)`, `msec` or `hour(s)` as a word, or ends with a bracketed `ms`, `h`, `hr` or `hrs`, such as `Elapsed (ms)` or `Elapsed [h]`. A bare short token such as `HR`, `signal_h` or `Ch1 ms` does not make a column the time column.
4. The first numeric, evenly spaced column.

In steps 2 and 3, a column that is not strictly increasing is skipped. After a column is chosen, the short tokens still set its unit: for example `Time ms` and `time_h` are converted from milliseconds and hours.

If no unit is recognized in the time-column name, values are assumed to be seconds. Automatic column detection is heuristic and must be checked by the user. On **Full Data Plot**, use **Time column** to choose another numeric timestamp column and **Time unit** to override the inferred conversion with **Seconds**, **Minutes**, **Milliseconds** or **Hours**.

## Video

The application uses OpenCV. The video file chooser offers MP4, MOV, AVI, and MKV files. Other containers are not offered by the chooser. Supported decoding and encoding still depend on codecs available in the local OpenCV build and operating system.

Use constant-frame-rate video for this release. Variable-frame-rate recordings should be transcoded to constant frame rate before alignment, with the conversion recorded in the analysis notes. Check the transcoded duration and events against the original.

## Publication-safe examples

Run `python examples/generate_demo.py` from the repository root to create `synthetic_video.mp4` and `synthetic_timeseries.csv` in `examples/generated_demo`, or supply a different output directory as the first argument. The application accepts any numeric time-series columns in the same tabular format. The deterministic MP4 is constant-frame-rate, and its event flashes coincide with peaks in the synthetic response signal at an offset of `0 s`. The generator and generated synthetic content are provided under the repository's MIT License.

The local `Example data` directory is deliberately ignored and is not the public demo. Do not force-add or publish those files until ownership and redistribution rights are confirmed and filenames/container metadata have been reviewed for animal, subject, laboratory, investigator, or study identifiers.
