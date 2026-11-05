"""Commands apps, the api command, and setup (PATH, Start menu, Windows Terminal, Explorer)."""
import os
import sys
import json
import struct
import zlib
import shutil
import subprocess

from . import VERSION, ui, envtools, backends, lexer
from .registry import builtin, Usage

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@builtin("api", cat="Apps & API", complete=lambda sh, a, w: __import__("commands.api", fromlist=["METHODS"]).METHODS
         if not a else [], summary="Call the Commands API from the prompt or from any script.",
         usage="api                          list every method\napi sys.info\n"
               "api path.add folder=C:\\Tools scope=user\napi ui.choose items='[\"a\",\"b\"]'")
def cmd_api(ctx):
    from . import api
    return api.cli(ctx.args, ctx.sh)


@builtin("commands", cat="Apps & API", hidden=True, summary="commands api ... / commands <line> inside a window.")
def cmd_commands(ctx):
    from . import api
    if ctx.args and ctx.args[0] == "api":
        return api.cli(ctx.args[1:], ctx.sh)
    if not ctx.args:
        ui.say(f"Commands {VERSION}")
        return 0
    return ctx.sh.run_line(lexer.join(ctx.args))


APP_TEMPLATE_PY = '''"""{name} - a Commands app.

Run it by typing:  {name}
It talks to the Commands window through the Commands API (see docs/API.md).
"""
import sys
sys.path.insert(0, {sdk!r})
from commands_api import Commands

cx = Commands()  # finds the window that started us

if not cx.available:
    print("Start me from Commands to get menus, progress bars and the rest.")
    sys.exit(1)

cx.print("[accent]Hello from {name}![/]  You are in [path]" + cx.info()["cwd"] + "[/]")
color = cx.choose(["red", "green", "blue"], title="Pick a color")
if color is None:
    cx.print("[dim]Cancelled.[/]")
    sys.exit(1)
cx.print(f"You picked [b]{{color}}[/].")
if cx.confirm("Go to your home folder when I'm done?"):
    cx.cd("~")
'''


@builtin("app", "apps-dev", cat="Apps & API", complete=lambda sh, a, w: ["list", "new", "remove", "folder", "info"] if not a else [],
         summary="Make and manage Commands apps (new commands that get the Commands API).",
         usage="app list                 your apps\napp new <name>           make one from a template (Python)\n"
               "app new <name> --js      Node.js template\napp remove <name>\napp folder               open the apps folder\n"
               "Any .py .js .ps1 .cmd .exe in the apps folder becomes a command.")
def cmd_app(ctx):
    from .shell import APPS_DIR
    o, rest = ctx.opts({"js": bool, "y|yes": bool})
    sub = rest[0].lower() if rest else "list"
    os.makedirs(APPS_DIR, exist_ok=True)
    if sub == "list":
        apps = ctx.sh.apps()
        if not apps:
            ui.say(ui.dim(f"No apps yet. Make one: app new hello   (folder: {APPS_DIR})"))
            return 0
        rows = []
        for n, e in sorted(apps.items()):
            rows.append([ui.paint(n, "odus", bold=True), e["run"] if isinstance(e, dict) else e,
                         ui.dim(e.get("help", "")) if isinstance(e, dict) else ""])
        ui.table(rows)
        return 0
    if sub == "folder":
        ui.say(APPS_DIR)
        if IS_WIN:
            os.startfile(APPS_DIR)
        return 0
    if len(rest) < 2:
        raise Usage()
    name = rest[1].lower()
    if sub == "new":
        sdk_py = os.path.join(ROOT, "sdk", "python")
        if o["js"]:
            path = os.path.join(APPS_DIR, name + ".js")
            sdk_js = os.path.join(ROOT, "sdk", "node", "commands-api.js").replace("\\", "\\\\")
            body = (f"// {name} - a Commands app\nconst {{ Commands }} = require(\"{sdk_js}\");\n\n(async () => {{\n"
                    "  const cx = new Commands();\n  if (!cx.available) { console.log('Start me from Commands.'); process.exit(1); }\n"
                    f"  await cx.print('[accent]Hello from {name}![/]');\n"
                    "  const pick = await cx.choose(['one', 'two', 'three'], { title: 'Pick one' });\n"
                    "  await cx.print(`You picked [b]${pick}[/]`);\n  cx.close();\n})();\n")
        else:
            path = os.path.join(APPS_DIR, name + ".py")
            body = APP_TEMPLATE_PY.format(name=name, sdk=sdk_py)
        if os.path.exists(path) and not o["yes"]:
            ui.say(ui.err(f"{path} already exists."))
            return 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        ui.say(ui.ok(f"Made {path}") + ui.dim(f"\nType '{name}' to run it. Edit it with: open \"{path}\""))
        return 0
    if sub == "remove":
        apps = ctx.sh.apps()
        e = apps.get(name)
        if e is None:
            ui.say(ui.err(f"No app called {name}."))
            return 1
        if isinstance(e, dict):
            reg = ui.load_json("apps.json", {})
            reg.pop(name, None)
            ui.save_json("apps.json", reg)
        else:
            from .builtins_files import send_to_trash
            send_to_trash(e)
        ui.say(ui.ok(f"Removed {name}."))
        return 0
    if sub == "info":
        e = ctx.sh.apps().get(name)
        ui.say(json.dumps(e, indent=2) if isinstance(e, dict) else str(e))
        return 0
    raise Usage()


# ------------------------------------------------------------- setup
def _png(size, pixels):
    raw = b"".join(b"\x00" + bytes(pixels[y * size * 4:(y + 1) * size * 4]) for y in range(size))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icon(path):
    """The Commands icon: a dark rounded tile with an amber › and a cursor bar."""
    images = []
    for size in (16, 32, 48, 256):
        px = bytearray(size * size * 4)
        r = size * 0.2
        def put(x, y, c, a=255):
            if 0 <= x < size and 0 <= y < size:
                i = (y * size + x) * 4
                px[i:i + 4] = bytes((c[0], c[1], c[2], a))
        for y in range(size):
            for x in range(size):
                cx = min(max(x + 0.5, r), size - r)
                cy = min(max(y + 0.5, r), size - r)
                d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
                if d <= r:
                    shade = 18 + int(10 * y / size)
                    put(x, y, (shade, shade, shade + 4), 255 if d < r - 1 else int(255 * max(0, r - d)))
        t = max(1, round(size / 11))
        amber = (240, 178, 62)
        x0, y0, x1, y1 = size * 0.22, size * 0.3, size * 0.48, size * 0.5
        steps = size * 4
        for i in range(steps + 1):
            f = i / steps
            for (ax, ay, bx, by) in ((x0, y0, x1, y1), (x1, y1, x0, size * 0.7)):
                px_ = ax + (bx - ax) * f
                py_ = ay + (by - ay) * f
                for dx in range(t):
                    for dy in range(t):
                        put(int(px_) + dx - t // 2, int(py_) + dy - t // 2, amber)
        for x in range(int(size * 0.54), int(size * 0.8)):
            for y in range(int(size * 0.66), int(size * 0.66) + t):
                put(x, y, (230, 230, 235))
        images.append((size, _png(size, px)))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    with open(path, "wb") as f:
        f.write(head + entries + data)
    return path


WT_SCHEME = {
    "name": "Commands", "background": "#0C0C0C", "foreground": "#D6D6D6", "cursorColor": "#F0B23E",
    "selectionBackground": "#283C5A", "black": "#0C0C0C", "red": "#FF6262", "green": "#6ED680",
    "yellow": "#F6C85A", "blue": "#62A0FF", "purple": "#D68CFF", "cyan": "#56C8EC", "white": "#D6D6D6",
    "brightBlack": "#6E6E76", "brightRed": "#FF8A8A", "brightGreen": "#96EBA6", "brightYellow": "#FFDC8A",
    "brightBlue": "#8CBCFF", "brightPurple": "#E6B4FF", "brightCyan": "#8CDCF5", "brightWhite": "#FFFFFF",
}


def single_file():
    """When started from a downloaded commands.bat / .command / .sh, a copy of it is kept in
    the settings folder so PATH and shortcuts don't depend on the Downloads folder."""
    src = os.environ.get("COMMANDS_LAUNCHER")
    if not src or not os.path.isfile(src):
        return None
    ext = os.path.splitext(src)[1].lower() or ".sh"
    bindir = os.path.join(ui.CONFIG_DIR, "bin")
    os.makedirs(bindir, exist_ok=True)
    dest = os.path.join(bindir, "commands" + (ext if ext in (".bat", ".cmd", ".command", ".py", ".sh") else ".sh"))
    if not (os.path.exists(dest) and os.path.samefile(src, dest)):
        shutil.copyfile(src, dest)
    if not IS_WIN:
        os.chmod(dest, 0o755)
    return dest


def launcher():
    from .__main__ import write_launcher
    return single_file() or write_launcher()


def start_command(extra=""):
    """(program, arguments) that open a Commands window."""
    lf = single_file()
    if lf and lf.lower().endswith((".bat", ".cmd")):
        comspec = os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe"
        return comspec, f'/d /s /c ""{lf}"{(" " + extra) if extra else ""}"'
    if lf and lf.lower().endswith((".sh", ".command")):
        return lf, extra
    script = lf or os.path.join(ROOT, "run.py")
    return backends.python_exe(), f'"{script}"{(" " + extra) if extra else ""}'


def setup_path():
    folder = os.path.dirname(launcher())
    res = envtools.add_path("user", folder)
    return "already on PATH" if res.get("already") else f"added {folder} to your PATH"


def setup_shortcut(icon):
    start = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Commands.lnk")
    exe, args = start_command()
    q = lambda s: s.replace("'", "''")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(start)}');"
          f"$s.TargetPath='{q(exe)}';$s.Arguments='{q(args)}';"
          f"$s.WorkingDirectory='{q(os.path.expanduser('~'))}';$s.IconLocation='{q(icon)},0';"
          "$s.Description='Commands';$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    if r.returncode:
        raise OSError(r.stderr.decode(errors="replace").strip() or "couldn't make the shortcut")
    return f"Start menu: {start}"


def setup_terminal(icon):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows Terminal", "Fragments", "Commands")
    os.makedirs(base, exist_ok=True)
    frag = {"profiles": [{
        "name": "Commands", "commandline": '"{}" {}'.format(*start_command()),
        "startingDirectory": "%USERPROFILE%", "icon": icon, "colorScheme": "Commands",
        "font": {"face": "Cascadia Mono", "size": 11}, "cursorShape": "bar", "padding": "10",
        "tabTitle": "Commands", "suppressApplicationTitle": False,
        "guid": "{c0fdfa11-c0de-4c0d-b0b0-0000c0ffee01}"}], "schemes": [WT_SCHEME]}
    path = os.path.join(base, "commands.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(frag, f, indent=2)
    return "Windows Terminal profile 'Commands' (with its own color scheme)"


def setup_explorer(icon):
    import winreg
    cmd = '"{}" {}'.format(*start_command('--cd "%V"'))
    for key in (r"Software\Classes\Directory\Background\shell\Commands", r"Software\Classes\Directory\shell\Commands"):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Open Commands here")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd.replace("%V", "%V" if "Background" in key else "%1"))
    return "Explorer right-click: 'Open Commands here'"


def setup_posix():
    bindir = os.path.expanduser("~/.local/bin")
    os.makedirs(bindir, exist_ok=True)
    link = os.path.join(bindir, "commands")
    src = launcher()
    os.chmod(src, os.stat(src).st_mode | 0o111)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(src, link)
    msgs = [f"linked {link}"]
    if envtools.find_entry(envtools.get_path("session"), bindir) < 0:
        envtools.add_path("user", bindir)
        msgs.append(f"added {bindir} to PATH in your shell settings")
    if not IS_MAC:
        apps = os.path.expanduser("~/.local/share/applications")
        os.makedirs(apps, exist_ok=True)
        icon = make_icon(os.path.join(ui.CONFIG_DIR, "commands.ico"))
        term_ = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or "xterm"
        with open(os.path.join(apps, "commands.desktop"), "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=Commands\nExec={src}\nTerminal=true\nIcon={icon}\n"
                    "Categories=System;TerminalEmulator;\n")
        msgs.append("app menu entry")
    return msgs


@builtin("setup", "install-commands", cat="Apps & API",
         summary="Put Commands on PATH, in the Start menu, Windows Terminal and Explorer's right-click menu.",
         usage="setup          pick what to set up\nsetup --all    everything\nsetup --remove undo it")
def cmd_setup(ctx):
    o, _ = ctx.opts({"all": bool, "remove": bool})
    os.makedirs(ui.CONFIG_DIR, exist_ok=True)
    if not IS_WIN:
        for m in setup_posix():
            ui.say(ui.ok("✓ ") + m)
        return 0
    if o["remove"]:
        return _unsetup()
    icon = make_icon(os.path.join(ui.CONFIG_DIR, "commands.ico"))
    steps = [("Add Commands to PATH (type 'commands' anywhere)", lambda: setup_path()),
             ("Start menu shortcut with the Commands icon", lambda: setup_shortcut(icon)),
             ("Windows Terminal profile + color scheme", lambda: setup_terminal(icon)),
             ("'Open Commands here' in Explorer's right-click menu", lambda: setup_explorer(icon))]
    if o["all"] or not ui.can_interact():
        chosen = steps
    else:
        labels = [s[0] for s in steps]
        pick = ui.choose(labels, ui.b("Set up Commands") + ui.dim("  (Space marks, Enter goes)"), multi=True)
        if not pick:
            ui.say(ui.dim("Nothing changed."))
            return 1
        chosen = [s for s in steps if s[0] in pick]
    rc = 0
    for label, fn in chosen:
        try:
            ui.say(ui.ok("✓ ") + fn())
        except Exception as e:
            ui.say(ui.err(f"✗ {label}: {e}"))
            rc = 1
    return rc


def _unsetup():
    import winreg
    out = []
    try:
        r = envtools.remove_path("user", ROOT)
        if r.get("changed"):
            out.append("removed from PATH")
    except envtools.EnvError as e:
        ui.say(ui.err(str(e)))
    lnk = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Commands.lnk")
    if os.path.exists(lnk):
        os.remove(lnk)
        out.append("Start menu shortcut removed")
    frag = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows Terminal", "Fragments", "Commands",
                        "commands.json")
    if os.path.exists(frag):
        os.remove(frag)
        out.append("Windows Terminal profile removed")
    for key in (r"Software\Classes\Directory\Background\shell\Commands", r"Software\Classes\Directory\shell\Commands"):
        for sub in (key + r"\command", key):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                if sub == key:
                    out.append("Explorer menu entry removed")
            except OSError:
                pass
    for m in out or ["nothing was set up"]:
        ui.say(ui.ok("✓ ") + m)
    return 0
