import numpy as np
import pandas as pd

from video_data_sync.gui.animated_tab import _sorted_trace_arrays


def test_sorted_trace_arrays_drops_nonfinite_and_sorts_time():
    frame = pd.DataFrame({"Time (s)": [2.0, np.nan, 1.0], "signal": [20.0, 99.0, 10.0]})
    times, values = _sorted_trace_arrays(frame, "Time (s)", "signal")
    assert times.tolist() == [1.0, 2.0]
    assert values.tolist() == [10.0, 20.0]
