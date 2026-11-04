"""The first-start question: put Odus on PATH?

Yes: its folder is added to the user PATH straight in the registry (never setx,
which cuts PATH at 1024 characters), with a backup first. On macOS/Linux a
link goes in ~/.local/bin. Then `odus` works in Commands, CMD and everywhere.

No: the answer is remembered. Commands won't run Odus for you, and Odus is only
reachable by running its file (odus.cmd / odus.sh) directly.
"""
import os
import sys

from . import ui, envtools, backends

IS_WIN = os.name == "nt"
KEY = "odus_on_path"  # in config.json: None (not asked yet), "yes", "no"

ODUS_CMD = """@echo off
rem Odus launcher (made by Commands). Not using setlocal on purpose, so Live Refresh can update this window.
set "ODUS_REFRESH_FILE=%TEMP%\\odus-refresh-%RANDOM%%RANDOM%.cmd"
where py >nul 2>nul && goto :use_py
python "%~dp0odus.py" %*
goto :after
:use_py
py -3 "%~dp0odus.py" %*
:after
set "ODUS_RC=%errorlevel%"
if exist "%ODUS_REFRESH_FILE%" call "%ODUS_REFRESH_FILE%"
if exist "%ODUS_REFRESH_FILE%" del "%ODUS_REFRESH_FILE%" >nul 2>nul
set "ODUS_REFRESH_FILE="
set "ODUS_RC=" & exit /b %ODUS_RC%
"""


def odus_file():
    return backends.find_odus()


def odus_dir():
    p = odus_file()
    return os.path.dirname(os.path.abspath(p)) if p else None


def launcher_name():
    d = odus_dir()
    if not d:
        return "odus.cmd" if IS_WIN else "odus.sh"
    for n in (("odus.cmd",) if IS_WIN else ("odus.sh", "odus.py")):
        if os.path.exists(os.path.join(d, n)):
            return os.path.join(d, n)
    return os.path.join(d, "odus.cmd" if IS_WIN else "odus.py")


def on_path() -> bool:
    """Is Odus on the saved PATH (what new windows get), not just this window's?"""
    d = odus_dir()
    if not d:
        return False
    if IS_WIN:
        try:
            saved = envtools.get_path("user") + envtools.get_path("machine")
        except OSError:
            saved = []
        return envtools.find_entry(saved, d) >= 0
    link = os.path.expanduser("~/.local/bin/odus")
    if os.path.lexists(link):
        return True
    return envtools.find_entry(envtools.get_path("user"), d) >= 0


def add() -> str:
    d = odus_dir()
    if not d:
        raise envtools.EnvError("Odus isn't on this computer (odus get downloads it).")
    if IS_WIN:
        cmd = os.path.join(d, "odus.cmd")
        if not os.path.exists(cmd) and os.path.exists(os.path.join(d, "odus.py")):
            with open(cmd, "w", encoding="ascii", newline="\r\n") as f:
                f.write(ODUS_CMD)
        res = envtools.add_path("user", d)  # registry, not setx; backs up first
        return res.get("backup") or ""
    src = launcher_name()
    os.chmod(src, os.stat(src).st_mode | 0o111)
    bindir = os.path.expanduser("~/.local/bin")
    os.makedirs(bindir, exist_ok=True)
    link = os.path.join(bindir, "odus")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(src, link)
    if envtools.find_entry(envtools.get_path("session"), bindir) < 0:
        envtools.add_path("user", bindir)
    return ""


def remove():
    d = odus_dir()
    if not d:
        return
    if IS_WIN:
        for scope in ("user", "machine"):
            if envtools.find_entry(envtools.get_path(scope), d) >= 0:
                envtools.remove_path(scope, d)
        return
    link = os.path.expanduser("~/.local/bin/odus")
    if os.path.islink(link):
        os.remove(link)


def declined(sh) -> bool:
    return sh.config.get(KEY) == "no"


def declined_message() -> str:
    return (ui.warn("Odus isn't on your PATH") + ui.dim(" (you chose that), so Commands doesn't run it.\n") +
            ui.dim("Run it by its file: ") + ui.b(launcher_name()) +
            ui.dim("   ·   Changed your mind? ") + ui.b("odus-path add"))


def first_run(sh):
    """Asks once, the first time Commands starts with Odus off PATH."""
    if not odus_file():
        return
    if on_path():
        if sh.config.get(KEY) != "yes":
            sh.config[KEY] = "yes"
            sh.save_config()
        return
    if sh.config.get(KEY) == "no" or not ui.can_interact():
        return
    ui.say(ui.b("Odus isn't on your PATH yet.") + ui.dim(f"  ({odus_dir()})"))
    ui.say(ui.dim("Adding it lets you type 'odus' (and install, update, compress...) in Commands, CMD and anywhere.\n"
                  "It goes straight into the registry - never setx, which cuts PATH at 1024 characters - "
                  "and your PATH is backed up first." if IS_WIN else
                  "A link goes in ~/.local/bin and your shell settings get a small, backed-up block."))
    if ui.confirm("Add Odus to your PATH?", default=True):
        try:
            bk = add()
            sh.config[KEY] = "yes"
            ui.say(ui.ok("Odus is on your PATH.") + (ui.dim(f"  old PATH saved: {bk}") if bk else ""))
        except (OSError, envtools.EnvError) as e:
            ui.say(ui.err(f"Couldn't add it: {e}"))
            return
    else:
        sh.config[KEY] = "no"
        ui.say(ui.dim("OK, nothing changed. Odus won't run from Commands or CMD; use the file itself: ") +
               ui.b(launcher_name()) + ui.dim("\nChange your mind any time with ") + ui.b("odus-path add"))
    sh.save_config()
    ui.say()
