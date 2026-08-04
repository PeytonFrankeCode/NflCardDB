"""Run the collector on a schedule, via Windows Task Scheduler.

The collector cannot run in the cloud -- eBay bot-checks datacentre addresses,
and sold listings need a signed-in session that lives in a Chrome profile on
this PC. So "automatic" means this PC doing it on a timer, not a server.

Task Scheduler is driven here through an XML definition rather than `schtasks`
flags, because three of the settings that decide whether this actually works
unattended have no flag:

* StartWhenAvailable -- run after a missed start, so a PC that was off at 07:00
  still collects when it comes back rather than skipping the day.
* DisallowStartIfOnBatteries -- off by default, which silently skips every run
  on an unplugged laptop.
* MultipleInstancesPolicy -- a slow run must not have the next day's run start
  on top of it.
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

TASK_NAME = "NflCardDB Daily Collection"

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ScheduleError(RuntimeError):
    """The task could not be created, read or removed."""


@dataclass
class ScheduleState:
    installed: bool
    detail: str = ""


def is_windows() -> bool:
    return platform.system() == "Windows"


def parse_time(value: str) -> tuple[int, int]:
    """Validate a 24-hour HH:MM. A bad time here becomes a task that never fires."""
    m = TIME_RE.match((value or "").strip())
    if not m:
        raise ScheduleError(
            f"{value!r} is not a time. Use 24-hour HH:MM, e.g. 07:00 or 21:30."
        )
    return (int(m.group(1)), int(m.group(2)))


def build_task_xml(
    command: Path,
    hour: int,
    minute: int,
    user: Optional[str] = None,
    task_dir: Optional[Path] = None,
) -> str:
    """A Task Scheduler 1.2 definition for a daily unattended run."""
    user = user or f"{os.environ.get('USERDOMAIN', '.')}\\{getpass.getuser()}"
    working = str(task_dir or command.parent)
    # The date only anchors the daily repeat; any past date starts it immediately.
    start = f"{date.today().isoformat()}T{hour:02d}:{minute:02d}:00"

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Collects yesterday's eBay football card sales and uploads them.</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <Priority>7</Priority>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <WorkingDirectory>{working}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(args: list[str]) -> subprocess.CompletedProcess:
    if not is_windows():
        raise ScheduleError(
            "Scheduling uses Windows Task Scheduler, and this is not Windows.\n"
            "On macOS or Linux, run `nflcarddb scrape` from cron instead."
        )
    try:
        return subprocess.run(
            ["schtasks", *args], capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError as exc:  # pragma: no cover - Windows always has it
        raise ScheduleError("schtasks.exe not found on this system.") from exc


def install(command: Path, when: str, task_name: str = TASK_NAME) -> str:
    """Create or replace the daily task. Returns the time it will run."""
    hour, minute = parse_time(when)
    command = Path(command).resolve()
    if not command.exists():
        raise ScheduleError(f"{command} does not exist.")

    xml = build_task_xml(command, hour, minute)
    # Task Scheduler requires UTF-16 to match the declaration in the XML itself.
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".xml", delete=False, encoding="utf-16"
    )
    try:
        handle.write(xml)
        handle.close()
        done = _schtasks(["/Create", "/TN", task_name, "/XML", handle.name, "/F"])
        if done.returncode != 0:
            raise ScheduleError(
                (done.stderr or done.stdout or "schtasks refused the task").strip()
            )
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    return f"{hour:02d}:{minute:02d}"


def remove(task_name: str = TASK_NAME) -> bool:
    """Delete the task. False when there was nothing to delete."""
    done = _schtasks(["/Delete", "/TN", task_name, "/F"])
    if done.returncode == 0:
        return False if "cannot find" in (done.stdout or "").lower() else True
    if "cannot find" in (done.stderr + done.stdout).lower():
        return False
    raise ScheduleError((done.stderr or done.stdout).strip())


def status(task_name: str = TASK_NAME) -> ScheduleState:
    """Whether the task exists, and what Task Scheduler says about it."""
    done = _schtasks(["/Query", "/TN", task_name, "/FO", "LIST"])
    if done.returncode != 0:
        return ScheduleState(installed=False, detail="not scheduled")

    wanted = ("Next Run Time", "Last Run Time", "Last Result", "Status")
    lines = [
        line.strip()
        for line in done.stdout.splitlines()
        if any(line.strip().startswith(k) for k in wanted)
    ]
    return ScheduleState(installed=True, detail="\n".join(lines))


def run_now(task_name: str = TASK_NAME) -> None:
    """Start the task immediately, to prove it works without waiting a day."""
    done = _schtasks(["/Run", "/TN", task_name])
    if done.returncode != 0:
        raise ScheduleError((done.stderr or done.stdout).strip())
