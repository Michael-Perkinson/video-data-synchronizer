"""Guard the filesystem boundary used by the GUI acceptance selection."""

from typing import cast

from video_data_sync.gui.main_window import MainWindow


def test_recovery_path_is_scoped_to_the_current_test(tmp_path):
    getter = cast(property, vars(MainWindow)["_recovery_path"]).fget
    assert getter is not None
    assert getter(None) == tmp_path / "recovery.vdsp.json"
