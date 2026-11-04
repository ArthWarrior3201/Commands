"""Running things Commands doesn't do itself: CMD, PowerShell, sh, Odus, programs.

Batch files and PowerShell lines report their final folder and variables
back, so `call setvars.bat`, `$env:X = 1` or `Set-Location` stick, just like
they would inside CMD or PowerShell themselves."""
import os
import re
import sys
import json
import base64
import shutil
import tempfile
import subprocess
import importlib.util

from . import ui

IS_WIN = os.name == "nt"
HOME = os.path.expanduser("~")
_TMP = tempfile.gettempdir()


def wait(proc) -> int:
    """Waits for a program, letting Ctrl+C reach it instead of killing Commands."""
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            continue


def run_process(argv_or_line, stdin_text=None, capture=False, env=None, shell_line=False):
    """Runs a program attached to this console. Returns (rc, output or None)."""
    kw = {"cwd": os.getcwd(), "env": env}
    if stdin_text is not None:
        kw["stdin"] = subprocess.PIPE
    if capture:
        kw["stdout"] = subprocess.PIPE
    try:
        proc = subprocess.Popen(argv_or_line, **kw)
    except FileNotFoundError:
        return 9009, None
    except OSError as e:
        ui.say(ui.err(f"Couldn't start it: {e}"))
        return 1, None
    out = None
    if stdin_text is not None or capture:
        try:
            data = (stdin_text or "").encode(console_encoding(), "replace") if stdin_text is not None else None
            while True:
                try:
                    o, _ = proc.communicate(data)
                    break
                except KeyboardInterrupt:
                    data = None
                    continue
            if capture:
                out = (o or b"").decode(console_encoding(), "replace").replace("\r\n", "\n")
        except (BrokenPipeError, OSError):
            pass
        return proc.returncode, out
    return wait(proc), None


def console_encoding() -> str:
    if IS_WIN:
        try:
            import ctypes
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:
                return "utf-8" if cp == 65001 else f"cp{cp}"
        except Exception:
            pass
        return "mbcs"
    return "utf-8"


# --------------------------------------------------------------- CMD
def cmd_line(line: str, stdin_text=None, capture=False):
    if IS_WIN:
        comspec = os.environ.get("ComSpec") or "cmd.exe"
        return run_process(f'"{comspec}" /d /s /c "{line}"', stdin_text, capture)
    return sh_line(line, stdin_text, capture)


def _apply_state_file(path: str, fmt: str):
    """Reads the folder + variables a batch file / PowerShell line left behind."""
    try:
        if fmt == "cmd":
            with open(path, "rb") as fh:
                raw = fh.read().decode(console_encoding(), "replace")
            lines = raw.splitlines()
            if not lines:
                return
            cwd, env = lines[0].strip(), {}
            for l in lines[1:]:
                if "=" in l[1:]:
                    k, _, v = l.partition("=") if not l.startswith("=") else ("", "", "")
                    if k:
                        env[k] = v
        else:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.loads(fh.read())
            cwd, env = data.get("cwd"), data.get("env") or {}
    except (OSError, ValueError):
        return
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if env:
        for k in list(os.environ):
            if k not in env and k.upper() not in {e.upper() for e in env}:
                if not k.upper().startswith("COMMANDS_"):
                    os.environ.pop(k, None)
        for k, v in env.items():
            if k.upper() in ("PROMPT",) or k.startswith("="):
                continue
            if os.environ.get(k) != v:
                os.environ[k] = v
    if cwd and os.path.isdir(cwd):
        os.chdir(cwd)


def run_batch(path: str, args: list, call=True):
    """Runs a .bat/.cmd and keeps the variables and folder it leaves behind."""
    if not IS_WIN:
        return sh_line(" ".join([path] + args))
    from .lexer import join as qjoin
    state = os.path.join(_TMP, f"commands-state-{os.getpid()}-{id(path)}.txt")
    wrapper = os.path.join(_TMP, f"commands-run-{os.getpid()}.cmd")
    line = f'call {qjoin([path])} {" ".join(args)}'.rstrip()
    with open(wrapper, "w", encoding=console_encoding(), errors="replace", newline="\r\n") as f:
        f.write("@echo off\n" + line + "\n"
                "@set \"COMMANDS_RC=%errorlevel%\"\n"
                f'@(cd & set) > "{state}"\n'
                "@exit /b %COMMANDS_RC%\n")
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    rc, _ = run_process(f'"{comspec}" /d /c "{wrapper}"')
    _apply_state_file(state, "cmd")
    os.environ.pop("COMMANDS_RC", None)
    try:
        os.remove(wrapper)
    except OSError:
        pass
    return rc, None


# -------------------------------------------------------- PowerShell
_ps_exe = None


def powershell_exe():
    global _ps_exe
    if _ps_exe is None:
        _ps_exe = shutil.which("pwsh") or shutil.which("powershell") or ""
    return _ps_exe or None


PS_VERBS = set("""Add Approve Assert Backup Block Build Checkpoint Clear Close Compare Complete Compress
Confirm Connect Convert ConvertFrom ConvertTo Copy Debug Deny Deploy Disable Disconnect Dismount Edit
Enable Enter Exit Expand Export Find Format Get Grant Group Hide Import Initialize Install Invoke Join
Limit Lock Measure Merge Mount Move New Open Optimize Out Ping Pop Protect Publish Push Read Receive Redo
Register Remove Rename Repair Request Reset Resize Resolve Restart Restore Resume Revoke Save Search
Select Send Set Show Skip Split Start Step Stop Submit Suspend Switch Sync Test Trace Unblock Undo
Uninstall Unlock Unprotect Unpublish Unregister Update Use Wait Watch Where Write ForEach Sort Tee""".split())
PS_VERB_SET = {v.capitalize() for v in PS_VERBS}
PS_ALIASES = {"gci", "gcm", "gc", "gps", "gsv", "iwr", "irm", "iex", "sl", "gl", "gm", "ni", "ri", "si",
              "sal", "sv", "gv", "ft", "fl", "measure", "select", "foreach", "%", "?"}
_CMDLET = re.compile(r"^([A-Za-z]+)-[A-Za-z][A-Za-z0-9]*$")


def looks_like_powershell(line: str, first: str) -> bool:
    s = line.lstrip()
    if s.startswith(("$", "[", "@(", "@{", "#")) and not s.startswith("#!"):
        return True
    m = _CMDLET.match(first)
    if m and m.group(1).capitalize() in {v.capitalize() for v in PS_VERBS}:
        return True
    if first.lower() in PS_ALIASES:
        return True
    if re.search(r"\|\s*(Where-Object|Select-Object|ForEach-Object|Sort-Object|Format-\w+|Out-\w+|"
                 r"Measure-Object|Group-Object|ConvertTo-\w+|Select-String)\b", line, re.I):
        return True
    return False


def ps_line(line: str, stdin_text=None, capture=False, file=None, args=()):
    exe = powershell_exe()
    if not exe:
        ui.say(ui.err("PowerShell isn't installed.") + (" Get it with: install powershell" if IS_WIN else
                                                         " (https://aka.ms/powershell)"))
        return 9009, None
    state = os.path.join(_TMP, f"commands-ps-{os.getpid()}.json")
    lit = lambda s: "'" + s.replace("'", "''") + "'"
    if file:
        body = "& " + lit(file) + " " + " ".join(lit(a) for a in args)
    else:
        body = line
    script = (
        "$ErrorActionPreference = 'Continue'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        f"Set-Location -LiteralPath {lit(os.getcwd())}\n"
        "$global:LASTEXITCODE = 0\n"
        "try {\n" + body + "\n} catch { Write-Error $_ }\n"
        "$__ok = $?\n"
        "$__rc = if ($global:LASTEXITCODE) { $global:LASTEXITCODE } elseif ($__ok) { 0 } else { 1 }\n"
        "$__loc = (Get-Location)\n"
        "$__cwd = if ($__loc.Provider.Name -eq 'FileSystem') { $__loc.ProviderPath } else { $null }\n"
        "$__env = @{}; foreach ($__e in [Environment]::GetEnvironmentVariables('Process').GetEnumerator())"
        " { $__env[[string]$__e.Key] = [string]$__e.Value }\n"
        f"[IO.File]::WriteAllText({lit(state)}, (@{{cwd = $__cwd; env = $__env}} | ConvertTo-Json -Compress -Depth 3))\n"
        "exit $__rc\n")
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    argv = [exe, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc] if IS_WIN else \
        [exe, "-NoLogo", "-NoProfile", "-EncodedCommand", enc]
    rc, out = run_process(argv, stdin_text, capture)
    _apply_state_file(state, "ps")
    return rc, out


# -------------------------------------------------------------- sh
def sh_line(line: str, stdin_text=None, capture=False):
    if IS_WIN:
        bash = shutil.which("bash")
        if not bash:
            ui.say(ui.err("No sh/bash found.") + " Install Git for Windows or WSL to run shell scripts.")
            return 9009, None
        return run_process([bash, "-c", line], stdin_text, capture)
    return run_process(["/bin/sh", "-c", line], stdin_text, capture)


# --------------------------------------------------------------- Odus
ODUS_ALWAYS = {"install", "uninstall", "update", "upgrade", "search", "compress", "compress-app",
               "decompress-app", "uncompress-app", "download", "dl", "clone", "yt", "youtube", "managers",
               "purge", "nuke", "yeet", "shrink", "shrink-app", "ytdl", "upgrade-all", "update-all",
               "multi-package", "multipackage", "fast", "fastplus", "fast+", "ufa", "turbo", "ultrafast"}
_odus_path = False
_odus_mod = None


def odus_candidates():
    env = os.environ.get("COMMANDS_ODUS")
    if env:
        yield env
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, "odus", "odus.py")  # copy shipped inside Commands
    yield os.path.join(ui.CONFIG_DIR, "odus", "odus.py")  # downloaded by 'odus get'
    yield os.path.join(HOME, "Odus", "odus.py")
    yield os.path.join(HOME, "Odus", "dist", "odus.py")
    for name in ("odus.py", "odus.cmd", "odus.sh", "odus"):
        p = shutil.which(name)
        if p:
            yield p
            d = os.path.dirname(p)
            yield os.path.join(d, "odus.py")


def find_odus():
    global _odus_path
    if _odus_path is False:
        _odus_path = None
        for p in odus_candidates():
            if p and os.path.isfile(p) and p.lower().endswith((".py", ".cmd", ".sh")) or \
                    (p and os.path.isfile(p) and not IS_WIN):
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        head = fh.read(4000)
                except OSError:
                    continue
                if "Odus" in head or "odus" in head:
                    _odus_path = p
                    break
    return _odus_path


def reset_odus():
    global _odus_path, _odus_mod
    _odus_path, _odus_mod = False, None


def odus_module():
    """Odus loaded as a module, only to ask whether it understands some words."""
    global _odus_mod
    if _odus_mod is None:
        _odus_mod = False
        p = find_odus()
        if p and p.lower().endswith(".py"):
            try:
                spec = importlib.util.spec_from_file_location("odus_for_commands", p)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["odus_for_commands"] = mod  # dataclasses need the module registered
                saved = sys.stdout
                spec.loader.exec_module(mod)
                sys.stdout = saved
                _odus_mod = mod
            except Exception:
                _odus_mod = False
    return _odus_mod or None


def odus_version():
    p = find_odus()
    if not p:
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            m = re.search(r'^VERSION = "([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return None


def odus_understands(argv: list) -> bool:
    m = odus_module()
    if not m or not argv:
        return False
    try:
        req = m.parse(list(argv))
        return req.verb not in ("unknown", "help", "version")
    except Exception:
        return False


def python_exe():
    if getattr(sys, "frozen", False):
        return shutil.which("python") or shutil.which("python3") or "python"
    return sys.executable


def run_odus(argv: list, stdin_text=None, capture=False):
    p = find_odus()
    if not p:
        ui.say(ui.err("Odus isn't on this computer.") + " Run " + ui.b("odus get") +
               " to download it (or set COMMANDS_ODUS to where odus.py is).")
        return 9009, None
    refresh = os.path.join(_TMP, f"commands-odus-{os.getpid()}.ps1")
    env = dict(os.environ)
    env["ODUS_REFRESH_FILE"] = refresh
    env["COMMANDS_HOST"] = "1"
    low = p.lower()
    if low.endswith(".py"):
        cmd = [python_exe(), p] + list(argv)
    elif low.endswith(".cmd"):
        cmd = [python_exe(), "-x", p] + list(argv)  # single-file build: skip the batch line
    else:
        cmd = [python_exe(), p] + list(argv)
    rc, out = run_process(cmd, stdin_text, capture, env=env)
    try:  # Odus tells its launcher which variables changed; we apply them here
        text = open(refresh, encoding="utf-8-sig").read()
        for k, v in re.findall(r"Set-Item -LiteralPath 'Env:(.+?)' -Value '((?:[^']|'')*)'", text):
            os.environ[k] = v.replace("''", "'")
        os.remove(refresh)
    except OSError:
        pass
    return rc, out


ODUS_RELEASE = "https://github.com/ArthWarrior3201/Odus/releases/latest/download/odus.py"


def download_odus() -> str | None:
    import urllib.request
    dest_dir = os.path.join(ui.CONFIG_DIR, "odus")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "odus.py")
    req = urllib.request.Request(ODUS_RELEASE, headers={"User-Agent": "Commands"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if b"Odus" not in data[:20000]:
        raise OSError("the download doesn't look like Odus")
    with open(dest, "wb") as f:
        f.write(data)
    reset_odus()
    return dest
