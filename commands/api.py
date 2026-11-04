"""The Commands API.

Every Commands window runs a small server on 127.0.0.1 with a random token.
Programs started from that window find it through two variables:

    COMMANDS_API        127.0.0.1:<port>
    COMMANDS_API_TOKEN  <secret>

Protocol: one JSON object per line, over TCP. Any language can use it.

    -> {"token": "...", "id": 1, "method": "session.cd", "params": {"path": "C:\\\\Tools"}}
    <- {"id": 1, "result": {"cwd": "C:\\\\Tools"}}
    <- {"id": 1, "error": {"code": "bad_params", "message": "..."}}

Things this makes possible that a .cmd or .ps1 file can't do on its own:
change the folder and variables of the window that started it, add
aliases, show arrow-key menus / password prompts / progress bars in the
user's window, change PATH for good without the 1024 limit, register new
commands, queue commands to run after the program exits, and more.
Without a session (`commands api ...` from any terminal) the methods that
don't need a window still work.
"""
import os
import sys
import json
import time
import socket
import secrets
import threading

from . import VERSION, ui, term, envtools, backends

API_VERSION = 1
METHODS = {}


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def method(name, summary, params="", session=False, ui_=False):
    def deco(fn):
        METHODS[name] = {"fn": fn, "summary": summary, "params": params, "session": session, "ui": ui_}
        return fn
    return deco


def _need(p, *names):
    for n in names:
        if n not in p or p[n] is None:
            raise ApiError("bad_params", f"'{n}' is required")
    return [p[n] for n in names]


def _scope(p, default="user"):
    s = (p.get("scope") or default).lower()
    if s not in ("user", "machine", "session"):
        raise ApiError("bad_params", "scope must be user, machine or session")
    return s


# ------------------------------------------------------------------ api
@method("api.version", "Versions of the API, Commands and Odus.")
def _version(sh, p):
    return {"api": API_VERSION, "commands": VERSION, "odus": backends.odus_version(),
            "os": "windows" if os.name == "nt" else sys.platform, "session": bool(sh and sh.interactive)}


@method("api.methods", "Every method with its parameters.")
def _methods(sh, p):
    return [{"method": k, "summary": v["summary"], "params": v["params"], "needs_session": v["session"]}
            for k, v in sorted(METHODS.items())]


# -------------------------------------------------------------- session
@method("session.info", "Folder, variables count, admin, last exit code, theme of the window.", session=True)
def _info(sh, p):
    return {"cwd": os.getcwd(), "pid": os.getpid(), "admin": envtools.is_admin(), "last_exit_code": sh.last_rc,
            "theme": ui.theme_name(), "history": len(sh.history), "aliases": sh.config["aliases"],
            "commands": VERSION, "jobs": [j.line for j in sh.jobs if j.proc.poll() is None]}


@method("session.cd", "Change the folder of the Commands window (not just your program's).", "path", session=True)
def _cd(sh, p):
    (path,) = _need(p, "path")
    path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    if not os.path.isdir(path):
        raise ApiError("not_found", f"{path} isn't a folder")
    sh.chdir(path)
    return {"cwd": os.getcwd()}


@method("session.getenv", "Read a variable of the Commands window.", "name", session=True)
def _getenv(sh, p):
    (name,) = _need(p, "name")
    return os.environ.get(name)


@method("session.setenv", "Set (or remove with value=null) a variable in the Commands window.", "name, value",
        session=True)
def _setenv(sh, p):
    (name,) = _need(p, "name")
    v = p.get("value")
    if v is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = str(v)
    return True


@method("session.alias", "Add (or remove with command=null) an alias, saved for good.", "name, command", session=True)
def _alias(sh, p):
    (name,) = _need(p, "name")
    cmd = p.get("command")
    if cmd:
        sh.config["aliases"][name.lower()] = cmd
    else:
        sh.config["aliases"].pop(name.lower(), None)
    sh.save_config()
    return True


@method("session.title", "Set the window title (null resets).", "title", session=True)
def _title(sh, p):
    sh.title = p.get("title") or None
    sh.set_title()
    return True


@method("session.run", "Queue a command line to run in the window after your program exits.", "line", session=True)
def _queue(sh, p):
    (line,) = _need(p, "line")
    sh.pending.append(str(line))
    return {"queued": len(sh.pending)}


@method("session.history", "The last commands typed in the window.", "limit", session=True)
def _hist(sh, p):
    n = int(p.get("limit") or 50)
    return sh.history[-n:]


@method("shell.run", "Run a command line through Commands (built-ins, Odus, CMD, PowerShell). capture=true returns "
                     "the output.", "line, capture")
def _run(sh, p):
    (line,) = _need(p, "line")
    if p.get("capture"):
        rc, out = sh.run_stage(line, capture=True) if "&" not in line else (sh.run_line(line), None)
        return {"exit_code": rc, "output": ui.strip_ansi(out or "")}
    return {"exit_code": sh.run_line(line)}


# ------------------------------------------------------------------- ui
_ui_lock = threading.Lock()


@method("ui.print", "Print in the user's window. markup: [ok]..[/] [err] [warn] [dim] [b] [accent] [path].",
        "text, markup", ui_=True)
def _print(sh, p):
    text = str(p.get("text", ""))
    term.write((ui.markup(text, sys.__stdout__) if p.get("markup", True) else text) + "\n")
    return True


@method("ui.confirm", "Ask yes/no in the user's window.", "question, default", ui_=True)
def _confirm(sh, p):
    return ui.confirm(str(p.get("question", "Continue?")), bool(p.get("default", False)))


@method("ui.ask", "Ask for a line of text (secret=true hides it, for passwords). null if cancelled.",
        "question, default, secret", ui_=True)
def _ask(sh, p):
    return ui.ask(str(p.get("question", "")), str(p.get("default") or ""), bool(p.get("secret")))


@method("ui.choose", "Arrow-key menu with type-to-filter. Returns the item (a list with multi=true), null if cancelled.",
        "items, title, multi", ui_=True)
def _choose(sh, p):
    (items,) = _need(p, "items")
    if not isinstance(items, list) or not items:
        raise ApiError("bad_params", "items must be a non-empty list")
    labels = [str(i) for i in items]
    pick = ui.choose(labels, ui.b(str(p.get("title") or "")), multi=bool(p.get("multi")))
    if pick is None:
        return None
    if isinstance(pick, list):
        return [items[labels.index(x)] for x in pick]
    return items[labels.index(pick)]


@method("ui.progress", "Draw a progress bar on one line; send done=true at the end.", "label, value, total, done", ui_=True)
def _progress(sh, p):
    label = str(p.get("label", ""))
    if p.get("done"):
        term.write("\r\x1b[K" + (ui.ok("✓ ") + label if label else "") + "\n")
        return True
    term.write("\r\x1b[K" + ui.progress_line(label, float(p.get("value") or 0), p.get("total")))
    return True


@method("ui.table", "Print rows as aligned columns.", "rows, header", ui_=True)
def _table(sh, p):
    (rows,) = _need(p, "rows")
    ui.table([[str(c) for c in r] for r in rows], header=p.get("header"), emit=lambda s: term.write(s + "\n"))
    return True


@method("ui.notify", "Desktop notification.", "title, message")
def _notify(sh, p):
    from .builtins_sys import desktop_notify
    return desktop_notify(str(p.get("title") or "Commands"), str(p.get("message") or ""), quiet=True)


# --------------------------------------------------------- PATH and env
@method("path.get", "PATH entries of a scope (user, machine, session).", "scope")
def _path_get(sh, p):
    return envtools.get_path(_scope(p))


@method("path.add", "Add a folder to PATH for good: no 1024 limit, backup first, this window updated too.",
        "folder, scope, front")
def _path_add(sh, p):
    (folder,) = _need(p, "folder")
    try:
        return envtools.add_path(_scope(p), folder, front=bool(p.get("front")))
    except envtools.NeedsAdmin as e:
        raise ApiError("needs_admin", str(e))


@method("path.remove", "Remove a folder (or 1-based number) from PATH.", "folder, scope")
def _path_remove(sh, p):
    (folder,) = _need(p, "folder")
    try:
        return envtools.remove_path(_scope(p), str(folder))
    except envtools.NeedsAdmin as e:
        raise ApiError("needs_admin", str(e))


@method("path.clean", "Remove missing and duplicate folders (dry=true only reports).", "scope, dry")
def _path_clean(sh, p):
    try:
        return envtools.clean_path(_scope(p), dry=bool(p.get("dry")))
    except envtools.NeedsAdmin as e:
        raise ApiError("needs_admin", str(e))


@method("path.which", "Every copy of a program on PATH, first one wins.", "name")
def _which(sh, p):
    (name,) = _need(p, "name")
    return envtools.which_all(name)


@method("env.get", "Read a variable from a scope.", "name, scope")
def _env_get(sh, p):
    (name,) = _need(p, "name")
    return envtools.get_var(_scope(p, "session"), name)


@method("env.set", "Save a variable for good (value=null removes it). Backup first, no length limit.",
        "name, value, scope")
def _env_set(sh, p):
    (name,) = _need(p, "name")
    try:
        bk = envtools.set_var(_scope(p), name, p.get("value"))
    except envtools.NeedsAdmin as e:
        raise ApiError("needs_admin", str(e))
    except envtools.EnvError as e:
        raise ApiError("refused", str(e))
    return {"backup": bk}


@method("env.refresh", "Load variables changed by installers into the window (Windows).", session=True)
def _env_refresh(sh, p):
    envtools.live_refresh(initial=False)
    return True


# -------------------------------------------------------------- system
@method("clipboard.get", "Text on the clipboard.")
def _clip_get(sh, p):
    from .builtins_sys import clipboard_get
    return clipboard_get()


@method("clipboard.set", "Put text on the clipboard (Unicode-safe).", "text")
def _clip_set(sh, p):
    from .builtins_sys import clipboard_set
    clipboard_set(str(p.get("text", "")))
    return True


@method("fs.trash", "Move files/folders to the Recycle Bin / Trash.", "paths")
def _trash(sh, p):
    from .builtins_files import send_to_trash
    (paths,) = _need(p, "paths")
    done, failed = [], []
    for x in ([paths] if isinstance(paths, str) else paths):
        try:
            send_to_trash(x)
            done.append(x)
        except OSError as e:
            failed.append({"path": x, "error": str(e)})
    return {"trashed": done, "failed": failed}


@method("sys.info", "Operating system, CPU, memory, uptime, disks.")
def _sysinfo(sh, p):
    from . import builtins_sys as s
    total, used = s.memory()
    return {"os": s.os_name(), "cpu": s.cpu_name(), "threads": os.cpu_count(), "memory_total": total,
            "memory_used": used, "uptime_seconds": s.uptime_seconds(), "battery": s.battery(),
            "disks": [{"path": d, "total": t, "used": u} for d, t, u in s.drives()], "admin": envtools.is_admin()}


@method("sys.processes", "Running processes [{pid, name, memory}].", "filter")
def _procs(sh, p):
    from .builtins_sys import process_list
    q = (p.get("filter") or "").lower()
    return [{"pid": a, "name": b, "memory": c} for a, b, c in process_list() if q in b.lower()]


@method("sys.ports", "Listening ports [{proto, port, address, pid, program}].")
def _ports(sh, p):
    from .builtins_sys import listening_ports
    return [{"proto": a, "port": b, "address": c, "pid": d, "program": e} for a, b, c, d, e in listening_ports()]


@method("sys.kill", "Stop a process (and its children).", "pid")
def _kill(sh, p):
    from .builtins_sys import kill_pid
    (pid,) = _need(p, "pid")
    ok, msg = kill_pid(int(pid))
    if not ok:
        raise ApiError("failed", msg or "couldn't stop it")
    return True


@method("odus.run", "Run Odus with these words in the user's window; returns the exit code.", "args", ui_=True)
def _odus(sh, p):
    args = p.get("args") or []
    if isinstance(args, str):
        from .lexer import words
        args = words(args)
    if sh is not None and sh.config.get("odus_on_path") == "no":
        raise ApiError("refused", "the user chose not to put Odus on PATH, so Commands doesn't run it")
    rc, _ = backends.run_odus([str(a) for a in args])
    return {"exit_code": rc}


@method("odus.status", "Is Odus here and on PATH?")
def _odus_status(sh, p):
    from . import odus_setup as osu
    return {"file": osu.odus_file(), "version": backends.odus_version(), "on_path": osu.on_path(),
            "answer": (sh.config.get(osu.KEY) if sh else None)}


# ------------------------------------------------------- more system
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


@method("sys.open", "Open a file, folder or link with its usual app.", "target")
def _open(sh, p):
    import subprocess
    (target,) = _need(p, "target")
    target = str(target)
    if IS_WIN:
        os.startfile(target if "://" in target else os.path.abspath(target))
    else:
        subprocess.Popen(["open" if IS_MAC else "xdg-open", target], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return True


@method("sys.elevate", "Run a command line as administrator in a new admin Commands window (asks via UAC).",
        "line, pause")
def _elevate(sh, p):
    (line,) = _need(p, "line")
    if not IS_WIN:
        raise ApiError("unsupported", "on macOS/Linux run it with sudo")
    if envtools.is_admin():
        return {"started": True, "already_admin": True, "exit_code": sh.run_line(str(line)) if sh else None}
    import ctypes
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = (f'"{os.path.join(root, "run.py")}" --cd "{os.getcwd()}" ' + ("--pause " if p.get("pause", True) else "")
            + "-c " + subprocess.list2cmdline([str(line)]))
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", backends.python_exe(), args, root, 1)
    if r <= 32:
        raise ApiError("refused", "administrator rights were not given")
    return {"started": True}


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_dir():
    return (os.path.expanduser("~/Library/LaunchAgents") if IS_MAC else
            os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart"))


@method("startup.list", "Programs that start when you sign in (your account's list).")
def _startup_list(sh, p):
    out = {}
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            i = 0
            while True:
                try:
                    n, v, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[n] = v
                i += 1
        return out
    d = _startup_dir()
    for f in (os.listdir(d) if os.path.isdir(d) else []):
        out[f] = os.path.join(d, f)
    return out


@method("startup.add", "Start a command every time you sign in.", "name, command")
def _startup_add(sh, p):
    name, command = _need(p, "name", "command")
    name = str(name)
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(command))
        return True
    d = _startup_dir()
    os.makedirs(d, exist_ok=True)
    if IS_MAC:
        import shlex
        import plistlib
        with open(os.path.join(d, f"commands.{name}.plist"), "wb") as f:
            plistlib.dump({"Label": f"commands.{name}", "ProgramArguments": shlex.split(str(command)),
                           "RunAtLoad": True}, f)
    else:
        with open(os.path.join(d, f"commands-{name}.desktop"), "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName={name}\nExec={command}\n")
    return True


@method("startup.remove", "Stop a command from starting when you sign in.", "name")
def _startup_remove(sh, p):
    (name,) = _need(p, "name")
    name = str(name)
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, name)
                return True
            except FileNotFoundError:
                return False
    for f in (f"commands.{name}.plist", f"commands-{name}.desktop"):
        path = os.path.join(_startup_dir(), f)
        if os.path.exists(path):
            os.remove(path)
            return True
    return False


@method("shortcut.create", "Make a shortcut (.lnk on Windows, .desktop on Linux, a link on macOS).",
        "path, target, args, icon, workdir")
def _shortcut(sh, p):
    import subprocess
    path, target = _need(p, "path", "target")
    path = os.path.abspath(os.path.expanduser(str(path)))
    if IS_WIN:
        if not path.lower().endswith(".lnk"):
            path += ".lnk"
        q = lambda s: str(s).replace("'", "''")
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(path)}');$s.TargetPath='{q(target)}';"
              f"$s.Arguments='{q(p.get('args') or '')}';$s.WorkingDirectory='{q(p.get('workdir') or '')}';")
        if p.get("icon"):
            ps += f"$s.IconLocation='{q(p['icon'])}';"
        ps += "$s.Save()"
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           creationflags=0x08000000)
        if r.returncode or not os.path.exists(path):
            raise ApiError("failed", r.stderr.decode(errors="replace").strip() or "couldn't make the shortcut")
    elif IS_MAC:
        os.symlink(str(target), path)
    else:
        if not path.endswith(".desktop"):
            path += ".desktop"
        with open(path, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName={os.path.basename(path)[:-8]}\n"
                    f"Exec={target} {p.get('args') or ''}\nIcon={p.get('icon') or ''}\n")
        os.chmod(path, 0o755)
    return {"path": path}


@method("net.download", "Download a file, with a progress bar in the user's window.", "url, dest, progress")
def _download(sh, p):
    import urllib.request
    url, dest = _need(p, "url", "dest")
    dest = os.path.abspath(os.path.expanduser(str(dest)))
    if os.path.isdir(dest):
        dest = os.path.join(dest, os.path.basename(str(url).split("?")[0].rstrip("/")) or "download")
    show = p.get("progress", True) and ui.can_interact()
    req = urllib.request.Request(str(url), headers={"User-Agent": f"Commands/{VERSION}"})
    tmp = dest + ".part"
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) or None
            label = os.path.basename(dest)
            last = 0.0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if show and time.time() - last > 0.1:
                    term.write("\r\x1b[K" + ui.progress_line(label, done, total))
                    last = time.time()
        os.replace(tmp, dest)
    except (OSError, ValueError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ApiError("failed", f"download failed: {e}")
    if show:
        term.write("\r\x1b[K" + ui.ok("✓ ") + f"{os.path.basename(dest)} ({ui.human(done)})\n")
    return {"path": dest, "bytes": done}


@method("net.free_port", "A free TCP port on this computer (for dev servers).", "start")
def _free_port(sh, p):
    start = int(p.get("start") or 0)
    if start:
        for port in range(start, min(start + 1000, 65536)):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        raise ApiError("failed", "no free port found")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@method("fs.hash", "Checksum of a file (sha256, or md5 / sha1 / sha512).", "path, algo")
def _fs_hash(sh, p):
    from .builtins_files import file_hash
    (path,) = _need(p, "path")
    algo = str(p.get("algo") or "sha256").lower()
    if algo not in ("md5", "sha1", "sha256", "sha512"):
        raise ApiError("bad_params", "algo must be md5, sha1, sha256 or sha512")
    try:
        return file_hash(str(path), algo)
    except OSError as e:
        raise ApiError("not_found", str(e))


@method("fs.size", "Size of a file or a whole folder.", "path")
def _fs_size(sh, p):
    from .builtins_files import folder_size
    (path,) = _need(p, "path")
    path = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.isfile(path):
        n = os.path.getsize(path)
        return {"bytes": n, "files": 1, "human": ui.human(n)}
    if not os.path.isdir(path):
        raise ApiError("not_found", f"{path} doesn't exist")
    b, n = folder_size(path)
    return {"bytes": b, "files": n, "human": ui.human(b)}


@method("fs.watch", "Wait until a file or folder changes (or timeout seconds pass); says what changed.",
        "path, timeout")
def _fs_watch(sh, p):
    (path,) = _need(p, "path")
    path = os.path.abspath(os.path.expanduser(str(path)))
    timeout = float(p.get("timeout") or 60)
    if not os.path.exists(path):
        raise ApiError("not_found", f"{path} doesn't exist")

    def snap():
        out = {}
        items = [(path, [], [os.path.basename(path)])] if os.path.isfile(path) else os.walk(path)
        for root, _, files in items:
            for f in files:
                fp = os.path.join(os.path.dirname(path), f) if root == path and os.path.isfile(path) else \
                    os.path.join(root, f)
                try:
                    st = os.stat(fp)
                    out[fp] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
            if len(out) > 20000:
                break
        return out
    before = snap()
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.25)
        now = snap()
        if now != before:
            return {"changed": True, "added": sorted(set(now) - set(before)),
                    "removed": sorted(set(before) - set(now)),
                    "modified": sorted(k for k in now if k in before and now[k] != before[k])}
    return {"changed": False}


@method("jobs.start", "Run a command line in the background of the window ('jobs' shows it).", "line",
        session=True)
def _jobs_start(sh, p):
    import io
    import contextlib
    from .registry import Ctx
    from .builtins_sys import cmd_bg
    (line,) = _need(p, "line")
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_bg(Ctx(sh, "bg", [str(line)]))
    j = sh.jobs[-1]
    return {"job": j.n, "pid": j.proc.pid, "log": j.log}


@method("jobs.list", "Background jobs of the window.", session=True)
def _jobs_list(sh, p):
    return [{"job": j.n, "line": j.line, "pid": j.proc.pid, "running": j.proc.poll() is None,
             "exit_code": j.proc.poll(), "log": j.log} for j in sh.jobs]


# ---------------------------------------------------------- commands
@method("commands.register", "Add a new command to Commands for good. run is the command line it starts.",
        "name, run, help")
def _register(sh, p):
    name, run = _need(p, "name", "run")
    apps = ui.load_json("apps.json", {})
    apps[str(name).lower()] = {"run": str(run), "help": str(p.get("help") or "")}
    ui.save_json("apps.json", apps)
    return True


@method("commands.unregister", "Remove a command added with commands.register.", "name")
def _unregister(sh, p):
    (name,) = _need(p, "name")
    apps = ui.load_json("apps.json", {})
    existed = apps.pop(str(name).lower(), None) is not None
    ui.save_json("apps.json", apps)
    return existed


@method("store.get", "Read a value your app saved (per app name).", "app, key")
def _store_get(sh, p):
    app, key = _need(p, "app", "key")
    return ui.load_json("store.json", {}).get(str(app), {}).get(str(key))


@method("store.set", "Save a small JSON value for your app (survives restarts).", "app, key, value")
def _store_set(sh, p):
    app, key = _need(p, "app", "key")
    data = ui.load_json("store.json", {})
    data.setdefault(str(app), {})[str(key)] = p.get("value")
    ui.save_json("store.json", data)
    return True


# ------------------------------------------------------------- calling
def call(sh, name, params, from_session=True):
    m = METHODS.get(name)
    if not m:
        raise ApiError("unknown_method", f"No method '{name}'. Try api.methods.")
    if m["session"] and (sh is None or not sh.interactive or not from_session):
        raise ApiError("needs_session", f"{name} only works for programs started inside a Commands window.")
    if not isinstance(params, dict):
        raise ApiError("bad_params", "params must be an object")
    if m["ui"]:
        with _ui_lock:
            return m["fn"](sh, params)
    return m["fn"](sh, params)


class Server:
    def __init__(self, sh):
        self.sh = sh
        self.token = secrets.token_hex(24)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.global_env = True
        threading.Thread(target=self._accept, daemon=True).start()

    def child_env(self):
        return {"COMMANDS_API": f"127.0.0.1:{self.port}", "COMMANDS_API_TOKEN": self.token,
                "COMMANDS_API_VERSION": str(API_VERSION)}

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        f = conn.makefile("rwb")
        try:
            for raw in f:
                if not raw.strip():
                    continue
                rid = None
                try:
                    msg = json.loads(raw.decode("utf-8"))
                    rid = msg.get("id")
                    if not secrets.compare_digest(str(msg.get("token", "")), self.token):
                        raise ApiError("bad_token", "wrong or missing token")
                    res = {"id": rid, "result": call(self.sh, str(msg.get("method", "")), msg.get("params") or {})}
                except ApiError as e:
                    res = {"id": rid, "error": {"code": e.code, "message": str(e)}}
                except (ValueError, AttributeError):
                    res = {"id": rid, "error": {"code": "bad_request", "message": "send one JSON object per line"}}
                except Exception as e:  # keep the window alive whatever an app sends
                    res = {"id": rid, "error": {"code": "failed", "message": f"{type(e).__name__}: {e}"}}
                f.write((json.dumps(res, default=str) + "\n").encode("utf-8"))
                f.flush()
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def start_server(sh):
    try:
        srv = Server(sh)
    except OSError:
        return None
    os.environ.update(srv.child_env())
    return srv


# --------------------------------------------------- client (CLI side)
def client_call(method_name, params, timeout=None):
    """Calls the session that started us, if there is one. Returns (ok, result_or_error)."""
    addr = os.environ.get("COMMANDS_API")
    token = os.environ.get("COMMANDS_API_TOKEN")
    if not addr or not token:
        return None
    host, _, port = addr.rpartition(":")
    try:
        s = socket.create_connection((host, int(port)), timeout=5)
    except OSError:
        return None
    s.settimeout(timeout)
    with s:
        s.sendall((json.dumps({"token": token, "id": 1, "method": method_name, "params": params}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8") or "{}")


def parse_cli_params(args):
    """key=value pairs (values parsed as JSON when they can be), or one JSON object."""
    if len(args) == 1 and args[0].strip().startswith("{"):
        return json.loads(args[0])
    out = {}
    for a in args:
        if "=" not in a:
            raise ApiError("bad_params", f"'{a}': use key=value")
        k, _, v = a.partition("=")
        try:
            out[k] = json.loads(v)
        except ValueError:
            try:  # Windows paths: ["C:\Tools"] has a lone backslash, which JSON doesn't allow
                out[k] = json.loads(v.replace("\\", "\\\\")) if v[:1] in "[{\"" else v
            except ValueError:
                out[k] = v
    return out


def cli(args, sh=None) -> int:
    """commands api <method> [key=value ...] [--raw]"""
    raw = "--raw" in args
    args = [a for a in args if a != "--raw"]
    if not args or args[0] in ("-h", "--help", "help", "list"):
        rows = [[ui.paint(k, "builtin", bold=True), v["params"], v["summary"] + (ui.dim(" (window)") if v["session"] else "")]
                for k, v in sorted(METHODS.items())]
        ui.say(ui.b("Commands API") + ui.dim(f" v{API_VERSION} · commands api <method> key=value ... [--raw]"))
        ui.table(rows, header=["method", "params", ""])
        ui.say(ui.dim("\nDocs: docs/API.md · Python: sdk/python/commands_api.py · Node: sdk/node/commands-api.js"))
        return 0
    name = args[0]
    try:
        params = parse_cli_params(args[1:])
    except (ApiError, ValueError) as e:
        ui.say(ui.err(str(e)))
        return 2
    resp = None
    if sh is None or not sh.interactive:
        resp = client_call(name, params)
    if resp is None:
        try:
            result = call(sh, name, params, from_session=sh is not None and sh.interactive)
            resp = {"result": result}
        except ApiError as e:
            resp = {"error": {"code": e.code, "message": str(e)}}
        except Exception as e:
            resp = {"error": {"code": "failed", "message": str(e)}}
    if "error" in resp:
        sys.stderr.write(json.dumps(resp["error"]) + "\n")
        return 1
    result = resp.get("result")
    if raw and isinstance(result, str):
        print(result)
    elif raw and isinstance(result, list) and all(isinstance(x, str) for x in result):
        print("\n".join(result))
    else:
        print(json.dumps(result, indent=None if raw else 2, ensure_ascii=False, default=str))
    return 0
