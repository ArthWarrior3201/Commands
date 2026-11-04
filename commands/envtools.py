"""Permanent PATH and environment variables, done safely.

Windows: writes the registry directly (HKCU / HKLM), keeps REG_EXPAND_SZ,
never uses setx (which cuts values at 1024 characters), backs up first,
refuses to write an empty or shrunken PATH by accident, and tells running
programs that the environment changed.

macOS / Linux: keeps a managed block in your shell start-up files
(~/.profile, ~/.bashrc, ~/.zshrc, fish conf.d) for the user scope, and
/etc/paths.d (macOS) or /etc/profile.d (Linux) for the machine scope.
"""
import os
import re
import sys
import json
import time
import shlex

from . import ui

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
SEP = os.pathsep
BACKUP_DIR = os.path.join(ui.CONFIG_DIR, "backups")
WIN_MAX_VAR = 32767  # the real per-variable limit on Windows (setx stops at 1024)


class EnvError(Exception):
    pass


class NeedsAdmin(EnvError):
    pass


def is_admin() -> bool:
    if IS_WIN:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def norm(p: str) -> str:
    p = os.path.expandvars(p.strip().strip('"'))
    if not p:
        return ""
    p = os.path.normpath(p)
    return os.path.normcase(p).rstrip("\\/") or p


def split(value: str) -> list:
    return [p for p in (value or "").split(SEP) if p.strip()]


def join(parts) -> str:
    return SEP.join(parts)


def scopes() -> list:
    return ["user", "machine"]


# ------------------------------------------------------------ Windows
_WIN_KEYS = {
    "user": ("HKEY_CURRENT_USER", r"Environment"),
    "machine": ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
}


def _win_open(scope, write=False):
    import winreg
    hive_name, sub = _WIN_KEYS[scope]
    hive = getattr(winreg, hive_name)
    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
    try:
        return winreg.OpenKey(hive, sub, 0, access)
    except PermissionError:
        raise NeedsAdmin(f"Changing {scope} variables needs an administrator terminal.")


def win_get(scope: str, name: str):
    """(value, kind) or (None, None)."""
    import winreg
    with _win_open(scope) as k:
        try:
            v, kind = winreg.QueryValueEx(k, name)
            return v, kind
        except FileNotFoundError:
            return None, None


def win_all(scope: str) -> dict:
    import winreg
    out = {}
    with _win_open(scope) as k:
        i = 0
        while True:
            try:
                name, val, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            out[name] = val
            i += 1
    return out


def _broadcast():
    try:
        import ctypes
        res = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0x0002, 3000, ctypes.byref(res))
    except Exception:
        pass


def win_set(scope: str, name: str, value, kind=None):
    import winreg
    if value is not None and len(value) > WIN_MAX_VAR:
        raise EnvError(f"{name} would be {len(value)} characters; Windows allows {WIN_MAX_VAR} per variable.")
    old, old_kind = win_get(scope, name)
    with _win_open(scope, write=True) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        else:
            if kind is None:
                kind = old_kind if old_kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else None
                if "%" in value:
                    kind = winreg.REG_EXPAND_SZ
                if kind is None:
                    kind = winreg.REG_EXPAND_SZ if name.lower() == "path" else winreg.REG_SZ
            winreg.SetValueEx(k, name, 0, kind, value)
    _broadcast()
    return old


# --------------------------------------------------------- macOS / Linux
BLOCK_START = "# >>> commands env >>>"
BLOCK_END = "# <<< commands env <<<"
STATE_FILE = "persist.json"  # what Commands manages for the user scope on macOS / Linux


def _posix_state() -> dict:
    s = ui.load_json(STATE_FILE, {})
    s.setdefault("path_front", [])
    s.setdefault("path_end", [])
    s.setdefault("vars", {})
    return s


def _rc_files() -> list:
    home = os.path.expanduser("~")
    files = [os.path.join(home, ".profile")]
    for name in (".bashrc", ".zshrc", ".bash_profile", ".zprofile"):
        p = os.path.join(home, name)
        if os.path.exists(p):
            files.append(p)
    shell = os.path.basename(os.environ.get("SHELL", ""))
    if shell == "zsh" and os.path.join(home, ".zshrc") not in files:
        files.append(os.path.join(home, ".zshrc"))
    if shell == "bash" and os.path.join(home, ".bashrc") not in files:
        files.append(os.path.join(home, ".bashrc"))
    return files


def _sh_block(state: dict) -> str:
    lines = [BLOCK_START, "# Managed by Commands (commands path / commands env). Edits here are overwritten."]
    for p in reversed(state["path_front"]):
        lines.append(f'case ":$PATH:" in *":{p}:"*) ;; *) export PATH={shlex.quote(p)}":$PATH" ;; esac')
    for p in state["path_end"]:
        lines.append(f'case ":$PATH:" in *":{p}:"*) ;; *) export PATH="$PATH:"{shlex.quote(p)} ;; esac')
    for k, v in state["vars"].items():
        lines.append(f"export {k}={shlex.quote(v)}")
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def _fish_block(state: dict) -> str:
    lines = ["# Managed by Commands. Edits here are overwritten."]
    for p in reversed(state["path_front"]):
        lines.append(f"contains -- {shlex.quote(p)} $PATH; or set -gx PATH {shlex.quote(p)} $PATH")
    for p in state["path_end"]:
        lines.append(f"contains -- {shlex.quote(p)} $PATH; or set -gx PATH $PATH {shlex.quote(p)}")
    for k, v in state["vars"].items():
        lines.append(f"set -gx {k} {shlex.quote(v)}")
    return "\n".join(lines) + "\n"


def _write_block(path: str, block: str):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        text = ""
    pat = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.S)
    if pat.search(text):
        new = pat.sub(lambda _m: block, text)
    else:
        new = text + ("" if not text or text.endswith("\n") else "\n") + "\n" + block
    if new != text:
        backup(os.path.basename(path), text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)


def _posix_apply(state: dict):
    ui.save_json(STATE_FILE, state)
    block = _sh_block(state)
    for rc in _rc_files():
        _write_block(rc, block)
    fish = os.path.expanduser("~/.config/fish")
    if os.path.isdir(fish):
        os.makedirs(os.path.join(fish, "conf.d"), exist_ok=True)
        with open(os.path.join(fish, "conf.d", "commands.fish"), "w", encoding="utf-8") as f:
            f.write(_fish_block(state))


def _posix_machine_file():
    return "/etc/paths.d/commands" if IS_MAC else "/etc/profile.d/commands.sh"


def _posix_machine_paths() -> list:
    f = _posix_machine_file()
    try:
        text = open(f, encoding="utf-8").read()
    except OSError:
        return []
    if IS_MAC:
        return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    return re.findall(r'export PATH="\$PATH:(.+?)"', text)


def _posix_write_machine(paths: list):
    if not is_admin():
        raise NeedsAdmin("Changing the machine PATH needs root (run: sudo commands ...).")
    f = _posix_machine_file()
    if IS_MAC:
        text = "".join(p + "\n" for p in paths)
    else:
        text = "# Managed by Commands\n" + "".join(f'export PATH="$PATH:{p}"\n' for p in paths)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(text)


# ------------------------------------------------------------- backups
def backup(label: str, content: str) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]", "_", label)
    path = os.path.join(BACKUP_DIR, f"{stamp}-{safe}.txt")
    n = 1
    while os.path.exists(path):
        path = os.path.join(BACKUP_DIR, f"{stamp}-{safe}-{n}.txt")
        n += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def list_backups(kind: str = "") -> list:
    try:
        names = sorted(os.listdir(BACKUP_DIR), reverse=True)
    except OSError:
        return []
    return [os.path.join(BACKUP_DIR, n) for n in names if kind.lower() in n.lower()]


# ------------------------------------------------------------ PATH API
def get_path(scope: str) -> list:
    """Entries of the PATH in a scope: 'session', 'user' or 'machine'."""
    if scope == "session":
        return split(os.environ.get("PATH", ""))
    if IS_WIN:
        v, _ = win_get(scope, "Path")
        return split(v or "")
    if scope == "user":
        s = _posix_state()
        return s["path_front"] + s["path_end"]
    return _posix_machine_paths()


def set_path(scope: str, entries: list, allow_shrink=0, reason="") -> str | None:
    """Writes the whole PATH of a scope. Refuses to wipe it. Returns the backup file."""
    entries = [e for e in entries if e.strip()]
    old = get_path(scope)
    if not entries and old:
        raise EnvError("Refusing to write an empty PATH. (Use 'path restore' to go back to a backup.)")
    if len(old) - len(entries) > allow_shrink:
        raise EnvError(f"Refusing to remove {len(old) - len(entries)} PATH entries at once when "
                       f"{allow_shrink} were expected. Nothing was changed.")
    if scope == "session":
        os.environ["PATH"] = join(entries)
        return None
    bk = backup(f"{scope}-PATH", join(old) + "\n")
    if IS_WIN:
        win_set(scope, "Path", join(entries))
    elif scope == "user":
        s = _posix_state()
        # keep each entry where it was (front or end)
        front = [e for e in entries if e in s["path_front"]]
        s["path_front"], s["path_end"] = front, [e for e in entries if e not in front]
        _posix_apply(s)
    else:
        _posix_write_machine(entries)
    log_change(scope, "PATH", reason or "set", bk)
    return bk


def find_entry(entries: list, what: str) -> int:
    if what.isdigit():
        i = int(what) - 1
        return i if 0 <= i < len(entries) else -1
    key = norm(what)
    for i, e in enumerate(entries):
        if norm(e) == key:
            return i
    return -1


def add_path(scope: str, folder: str, front=False, before: str | None = None, session_too=True) -> dict:
    folder = folder.strip().strip('"')
    if scope != "session" and not IS_WIN and folder.startswith("~"):
        folder = os.path.expanduser(folder)
    entries = get_path(scope)
    result = {"scope": scope, "entry": folder, "changed": False, "backup": None}
    i = find_entry(entries, folder)
    if i >= 0 and not front and before is None:
        result["already"] = True
    else:
        if i >= 0:
            entries.pop(i)
        if before is not None:
            j = find_entry(entries, before)
            entries.insert(j if j >= 0 else 0, folder)
        elif front:
            entries.insert(0, folder)
        else:
            entries.append(folder)
        if scope != "session" and not IS_WIN and front:
            s = _posix_state()
            if folder not in s["path_front"]:
                s["path_front"].insert(0, folder)
                s["path_end"] = [p for p in s["path_end"] if p != folder]
                ui.save_json(STATE_FILE, s)
        result["backup"] = set_path(scope, entries, reason=f"add {folder}")
        result["changed"] = True
    if session_too and scope != "session":
        sess = get_path("session")
        if find_entry(sess, folder) < 0 or front:
            k = find_entry(sess, folder)
            if k >= 0:
                sess.pop(k)
            exp = os.path.expandvars(folder)
            sess.insert(0, exp) if front else sess.append(exp)
            os.environ["PATH"] = join(sess)
    return result


def remove_path(scope: str, what: str, session_too=True) -> dict:
    entries = get_path(scope)
    i = find_entry(entries, what)
    if i < 0:
        return {"scope": scope, "entry": what, "changed": False, "missing": True}
    removed = entries.pop(i)
    bk = set_path(scope, entries, allow_shrink=1, reason=f"remove {removed}")
    if session_too and scope != "session":
        sess = get_path("session")
        k = find_entry(sess, removed)
        if k >= 0:
            sess.pop(k)
            os.environ["PATH"] = join(sess)
    return {"scope": scope, "entry": removed, "changed": True, "backup": bk}


def problems(entries: list) -> dict:
    """missing folders and duplicates (index lists)."""
    seen, dups, missing = {}, [], []
    for i, e in enumerate(entries):
        k = norm(e)
        if k in seen:
            dups.append(i)
        else:
            seen[k] = i
        if not os.path.isdir(os.path.expandvars(e.strip('"'))):
            missing.append(i)
    return {"missing": missing, "duplicates": dups}


def clean_path(scope: str, dry=False) -> dict:
    entries = get_path(scope)
    p = problems(entries)
    drop = sorted(set(p["missing"]) | set(p["duplicates"]))
    keep = [e for i, e in enumerate(entries) if i not in drop]
    res = {"scope": scope, "removed": [entries[i] for i in drop], "backup": None}
    if drop and not dry:
        res["backup"] = set_path(scope, keep, allow_shrink=len(drop), reason=f"clean ({len(drop)})")
    return res


def move_path(scope: str, what: str, to: int) -> dict:
    entries = get_path(scope)
    i = find_entry(entries, what)
    if i < 0:
        raise EnvError(f"'{what}' isn't in the {scope} PATH.")
    e = entries.pop(i)
    to = max(0, min(len(entries), to))
    entries.insert(to, e)
    return {"backup": set_path(scope, entries, reason=f"move {e} to {to + 1}"), "entry": e, "position": to + 1}


def restore_path(scope: str, backup_file: str) -> str:
    with open(backup_file, encoding="utf-8") as fh:
        text = fh.read().strip()
    entries = split(text)
    if not entries:
        raise EnvError("That backup is empty.")
    return set_path(scope, entries, allow_shrink=10 ** 6, reason=f"restore {os.path.basename(backup_file)}")


def which_all(name: str, entries: list | None = None) -> list:
    """Every matching program in PATH order (the first one wins)."""
    entries = entries if entries is not None else get_path("session")
    exts = [""] + (os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";") if IS_WIN else [])
    if IS_WIN and os.path.splitext(name)[1]:
        exts = [""]
    found, seen = [], set()
    for d in entries:
        if norm(d) in seen:
            continue
        seen.add(norm(d))
        d = os.path.expandvars(d.strip('"'))
        for ext in exts:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p) and (IS_WIN or os.access(p, os.X_OK)):
                found.append(p)
                if IS_WIN:
                    break
    return found


# ------------------------------------------------------- other variables
def get_var(scope: str, name: str):
    if scope == "session":
        return os.environ.get(name)
    if IS_WIN:
        return win_get(scope, name)[0]
    if scope == "user":
        return _posix_state()["vars"].get(name)
    return None


def set_var(scope: str, name: str, value, session_too=True) -> str | None:
    """Permanent variable. value=None removes it. Returns the backup file."""
    if not re.fullmatch(r"[A-Za-z_][\w().-]*", name):
        raise EnvError(f"'{name}' isn't a valid variable name.")
    if name.lower() == "path" and scope != "session":
        if value is None or not value.strip():
            raise EnvError("Refusing to delete or empty PATH. Use 'path remove' for single entries.")
        return set_path(scope, split(value), allow_shrink=10 ** 6, reason="env set PATH")
    bk = None
    if scope != "session":
        old = get_var(scope, name)
        bk = backup(f"{scope}-{name}", "" if old is None else old)
        if IS_WIN:
            win_set(scope, name, value)
        elif scope == "user":
            s = _posix_state()
            if value is None:
                s["vars"].pop(name, None)
            else:
                s["vars"][name] = value
            _posix_apply(s)
        else:
            raise EnvError("Machine-wide variables on macOS/Linux: edit /etc/environment as root.")
        log_change(scope, name, "remove" if value is None else "set", bk)
    if session_too or scope == "session":
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = os.path.expandvars(value) if IS_WIN else value
    return bk


def list_vars(scope: str) -> dict:
    if scope == "session":
        return dict(os.environ)
    if IS_WIN:
        return win_all(scope)
    if scope == "user":
        s = _posix_state()
        v = dict(s["vars"])
        if s["path_front"] or s["path_end"]:
            v["PATH (added)"] = join(s["path_front"] + s["path_end"])
        return v
    return {}


def log_change(scope, name, what, bk):
    hist = ui.load_json("env-changes.json", [])
    hist.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "scope": scope, "name": name,
                 "what": what, "backup": bk})
    ui.save_json("env-changes.json", hist[-500:])


# ------------------------------------------------- live refresh (Windows)
def fresh_from_system() -> dict:
    """What a new terminal would get: machine + user variables, PATH merged."""
    if not IS_WIN:
        return {}
    try:
        m, u = win_all("machine"), win_all("user")
    except Exception:
        return {}
    out = {}
    for src in (m, u):
        for k, v in src.items():
            if k.lower() == "path":
                continue
            out[k] = os.path.expandvars(v) if isinstance(v, str) else str(v)
    mp = os.path.expandvars(m.get("Path", "") or "")
    up = os.path.expandvars(u.get("Path", "") or "")
    out["Path"] = mp.rstrip(";") + (";" + up if up else "")
    return out


_last_system: dict = {}


def live_refresh(initial=False) -> list:
    """Brings this session up to date with variables changed elsewhere
    (an installer, another terminal). Only applies things that changed in the
    system since we last looked, so session-only changes stay. Returns notes."""
    global _last_system
    if not IS_WIN:
        return []
    now = fresh_from_system()
    if not now:
        return []
    notes = []
    if initial or not _last_system:
        _last_system = now
        return []
    for k, v in now.items():
        old = _last_system.get(k)
        if old == v:
            continue
        if k.lower() == "path":
            sess = split(os.environ.get("PATH", ""))
            have = {norm(p) for p in sess}
            gone = {norm(p) for p in split(old or "")} - {norm(p) for p in split(v)}
            added = [p for p in split(v) if norm(p) not in have]
            sess = [p for p in sess if norm(p) not in gone] + added
            os.environ["PATH"] = join(sess)
            if added:
                notes.append("PATH + " + ", ".join(added))
            if gone:
                notes.append(f"PATH - {len(gone)} folder(s)")
        else:
            os.environ[k] = v
            notes.append(f"{k} updated")
    for k in set(_last_system) - set(now):
        if k.lower() != "path" and k in os.environ:
            os.environ.pop(k, None)
            notes.append(f"{k} removed")
    _last_system = now
    return notes
