"""The daily Windows scheduled task.

Most of what decides whether an unattended run actually happens is in the XML
settings, not in the code around them -- a task that skips every run on battery
looks identical to one that works until you unplug the laptop. So the settings
are asserted explicitly.
"""

from pathlib import Path, PurePath

import pytest

from nflcarddb.scheduling import (
    ScheduleError,
    build_task_xml,
    install,
    parse_time,
    remove,
    status,
)


def test_parse_time_accepts_24_hour_times():
    assert parse_time("07:00") == (7, 0)
    assert parse_time("21:30") == (21, 30)
    assert parse_time("00:00") == (0, 0)
    assert parse_time(" 23:59 ") == (23, 59)


def test_parse_time_rejects_what_would_become_a_task_that_never_fires():
    for bad in ("7pm", "25:00", "07:60", "0700", "", "noon", None):
        with pytest.raises(ScheduleError):
            parse_time(bad)


def _xml(hour=7, minute=0):
    return build_task_xml(Path("C:/NflCardDB/daily.bat"), hour, minute, user="PC\\me")


def test_a_missed_start_still_runs():
    """A PC that was off at 07:00 must collect when it comes back, not skip."""
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in _xml()


def test_battery_power_does_not_silently_skip_the_run():
    """Both default to true, which quietly disables the task on a laptop."""
    xml = _xml()
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml


def test_a_slow_run_is_not_stacked_on_by_the_next_one():
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in _xml()


def test_the_task_runs_in_the_signed_in_session():
    """The collector drives a real Chrome holding the eBay session."""
    assert "<LogonType>InteractiveToken</LogonType>" in _xml()


def test_the_run_time_lands_in_the_trigger():
    assert "T21:30:00</StartBoundary>" in _xml(21, 30)
    assert "T07:00:00</StartBoundary>" in _xml(7, 0)


def test_the_command_and_its_folder_are_both_set():
    """Without WorkingDirectory the task runs in system32 and finds no venv."""
    import re

    xml = _xml()
    command = re.search(r"<Command>(.*?)</Command>", xml).group(1)
    working = re.search(r"<WorkingDirectory>(.*?)</WorkingDirectory>", xml).group(1)

    assert command.endswith("daily.bat")
    assert PurePath(working) == PurePath(command).parent


def test_installing_a_missing_command_fails_before_touching_the_scheduler(tmp_path):
    with pytest.raises(ScheduleError, match="does not exist"):
        install(tmp_path / "nope.bat", "07:00")


def test_a_bad_time_fails_before_touching_the_scheduler(tmp_path):
    script = tmp_path / "daily.bat"
    script.write_text("@echo off")
    with pytest.raises(ScheduleError, match="not a time"):
        install(script, "seven")


def test_non_windows_says_so_rather_than_failing_obscurely(monkeypatch, tmp_path):
    monkeypatch.setattr("nflcarddb.scheduling.is_windows", lambda: False)
    script = tmp_path / "daily.bat"
    script.write_text("@echo off")

    with pytest.raises(ScheduleError, match="not Windows"):
        install(script, "07:00")


def test_install_hands_schtasks_a_utf16_xml_file(monkeypatch, tmp_path):
    """Task Scheduler rejects the file if the encoding and declaration disagree."""
    script = tmp_path / "daily.bat"
    script.write_text("@echo off")
    seen = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake(args):
        path = args[args.index("/XML") + 1]
        seen["text"] = Path(path).read_text(encoding="utf-16")
        seen["args"] = args
        return Done()

    monkeypatch.setattr("nflcarddb.scheduling._schtasks", fake)
    assert install(script, "6:05") == "06:05"
    assert "encoding=\"UTF-16\"" in seen["text"]
    assert "/F" in seen["args"]          # replace an existing task, not fail on it


def test_install_leaves_no_temp_file_behind(monkeypatch, tmp_path):
    script = tmp_path / "daily.bat"
    script.write_text("@echo off")
    seen = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake(args):
        seen["path"] = Path(args[args.index("/XML") + 1])
        return Done()

    monkeypatch.setattr("nflcarddb.scheduling._schtasks", fake)
    install(script, "07:00")
    assert not seen["path"].exists()


def test_a_refused_task_reports_what_schtasks_said(monkeypatch, tmp_path):
    script = tmp_path / "daily.bat"
    script.write_text("@echo off")

    class Refused:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr("nflcarddb.scheduling._schtasks", lambda args: Refused())
    with pytest.raises(ScheduleError, match="Access is denied"):
        install(script, "07:00")


def test_status_reports_not_installed_without_raising(monkeypatch):
    class Missing:
        returncode = 1
        stdout = ""
        stderr = "ERROR: The system cannot find the file specified."

    monkeypatch.setattr("nflcarddb.scheduling._schtasks", lambda args: Missing())
    state = status()
    assert state.installed is False


def test_status_surfaces_the_next_and_last_run(monkeypatch):
    class Found:
        returncode = 0
        stderr = ""
        stdout = (
            "Folder: \\\n"
            "HostName:      PC\n"
            "TaskName:      \\NflCardDB Daily Collection\n"
            "Next Run Time: 05/08/2026 07:00:00\n"
            "Status:        Ready\n"
            "Last Run Time: 04/08/2026 07:00:00\n"
            "Last Result:   0\n"
        )

    monkeypatch.setattr("nflcarddb.scheduling._schtasks", lambda args: Found())
    state = status()
    assert state.installed is True
    assert "Next Run Time" in state.detail
    assert "Last Result" in state.detail
    assert "HostName" not in state.detail      # noise, not status


def test_removing_a_task_that_was_never_there_is_not_an_error(monkeypatch):
    class Missing:
        returncode = 1
        stdout = ""
        stderr = "ERROR: The system cannot find the file specified."

    monkeypatch.setattr("nflcarddb.scheduling._schtasks", lambda args: Missing())
    assert remove() is False
