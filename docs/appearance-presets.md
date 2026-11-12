# Appearance presets

Appearance presets are small JSON files for sharing reusable display and export preferences.
Load one from **Presets → Load Appearance Preset…**. Saving from the same menu captures the
current reusable settings.

Two ready-to-import examples are included:

- [`generic-clean.vdsp-preset.json`](../examples/presets/generic-clean.vdsp-preset.json)
  provides a neutral side-by-side layout, automatic scale lengths, identity video colour, and
  normal-speed export. It has no preferred channel names, so it does not select a recording's
  columns.
- [`fiber-photometry.vdsp-preset.json`](../examples/presets/fiber-photometry.vdsp-preset.json)
  is a domain-specific template for files with `dFoF_470` and `Control_405` columns. It overlays
  the traces with a blue/orange palette, preserves their existing dF/F values, and remembers 0.5×,
  1×, and 2× export choices.

Column colours and custom legend names are matched by the exact column heading. To adapt the
photometry example to a lab's files, save a preset after selecting the real columns in the
application; this avoids hand-editing names or accidentally choosing the wrong signal.

## What presets do and do not contain

Presets contain trace colours, optional per-column legend names, preferred channel names, line
width, display transform, overlay choice, preview panel proportions, scale-bar presentation, an
optional maximum graph range, export speeds, de-flicker preference, and video colour defaults. The current preview is always side by side; legacy stacked-layout values do not change its
orientation. Saved panel proportions are restored up to a 68% video-width limit; older video-heavy
splits use the aspect-aware default so the plot remains visible.
Automatic scaling remains the default. The optional range limit is a percentage (default 70%) of
the current signal span, so it adjusts automatically as that span changes.

They intentionally exclude source paths, clip boundaries, crop rectangles, synchronization
offsets, z-score baseline intervals, and absolute fixed y-axis limits. Those values describe a
particular recording and remain in its project file. Loading a preset does not calculate dF/F or
z-scores; the photometry example's `dF/F` label is appropriate only when the selected source values
already use that scale.

Preset files use a strict, versioned schema. The application rejects unknown fields, invalid
colours, out-of-range values, malformed JSON, and unsupported schema versions rather than partly
applying a questionable file.
