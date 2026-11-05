"""System, network and data tools."""
import os
import re
import io
import sys
import ast
import json
import math
import time
import uuid
import base64
import socket
import shutil
import secrets
import string
import platform
import threading
import subprocess

from . import VERSION, ui, term, lexer, backends, envtools
from .registry import builtin, Usage, resolve

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WIN else 0


def _out(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout.decode(backends.console_encoding(), "replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ------------------------------------------------------------ processes
def process_list():
    """[(pid, name, memory_bytes)]"""
    out = []
    if IS_WIN:
        import csv
        for row in csv.reader(io.StringIO(_out(["tasklist", "/fo", "csv", "/nh"]))):
            if len(row) >= 5:
                try:
                    mem = int(re.sub(r"[^\d]", "", row[4]) or 0) * 1024
                    out.append((int(row[1]), row[0], mem))
                except ValueError:
                    pass
    else:
        for line in _out(["ps", "-axo", "pid=,rss=,comm="]).splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit():
                out.append((int(parts[0]), os.path.basename(parts[2]), int(parts[1]) * 1024))
    return out


@builtin("procs", "top", cat="System", summary="Running programs, biggest memory first. Filter by name.",
         usage="procs [name] [-n 25]")
def cmd_procs(ctx):
    o, rest = ctx.opts({"n|top": int})
    q = " ".join(rest).lower()
    ps = [p for p in process_list() if q in p[1].lower() or q == str(p[0])]
    ps.sort(key=lambda p: -p[2])
    total = sum(p[2] for p in ps) or 1
    n = o["top"] or (len(ps) if q else 25)
    ui.table([[str(pid), ui.b(name), ui.human(mem), ui.paint(ui.bar(mem / ps[0][2] if ps else 0, 12), "accent")]
              for pid, name, mem in ps[:n]], header=["PID", "Program", "Memory", ""], aligns="rlrl")
    ui.say(ui.dim(f"{len(ps)} processes · {ui.human(total)}"))
    return 0


def kill_pid(pid, force=True):
    if IS_WIN:
        r = subprocess.run(["taskkill", "/pid", str(pid), "/t"] + (["/f"] if force else []),
                           capture_output=True, creationflags=NO_WINDOW)
        return r.returncode == 0, r.stderr.decode(backends.console_encoding(), "replace").strip()
    import signal
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True, ""
    except OSError as e:
        return False, e.strerror


@builtin("kill", "end", cat="System", summary="Stop programs by name or PID (asks if several match).",
         usage="kill chrome      kill 1234      kill node --all")
def cmd_kill(ctx):
    o, rest = ctx.opts({"all": bool, "y|yes": bool})
    if not rest:
        raise Usage()
    rc = 0
    for what in rest:
        if what.isdigit():
            targets = [(int(what), what, 0)]
        else:
            w = what.lower().removesuffix(".exe")
            targets = [p for p in process_list() if p[1].lower().removesuffix(".exe") == w] or \
                      [p for p in process_list() if w in p[1].lower()]
        targets = [t for t in targets if t[0] != os.getpid()]
        if not targets:
            ui.say(ui.err(f"Nothing running called '{what}'."))
            rc = 1
            continue
        if len(targets) > 1 and not o["all"] and not o["yes"]:
            names = sorted({t[1] for t in targets})
            if not ui.confirm(f"Stop {len(targets)} processes ({', '.join(names[:4])})?"):
                continue
        for pid, name, _ in targets:
            good, msg = kill_pid(pid)
            ui.say((ui.ok("stopped ") if good else ui.err("couldn't stop ")) + f"{name} ({pid})" +
                   (ui.dim(f"  {msg}") if not good and msg else ""))
            rc |= 0 if good else 1
    return rc


def listening_ports():
    """[(proto, port, address, pid, program)]"""
    names = {pid: name for pid, name, _ in process_list()}
    out = []
    if IS_WIN:
        for line in _out(["netstat", "-ano"]).splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] in ("TCP", "UDP"):
                if parts[0] == "TCP" and (len(parts) < 5 or parts[3] != "LISTENING"):
                    continue
                addr = parts[1]
                pid = int(parts[-1]) if parts[-1].isdigit() else 0
                host, _, port = addr.rpartition(":")
                if port.isdigit():
                    out.append((parts[0], int(port), host, pid, names.get(pid, "?")))
    else:
        text = _out(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]) if shutil.which("lsof") else ""
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 9:
                host, _, port = parts[8].rpartition(":")
                if port.isdigit():
                    out.append(("TCP", int(port), host, int(parts[1]), parts[0]))
        if not text and shutil.which("ss"):
            for line in _out(["ss", "-ltnpH"]).splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    host, _, port = parts[3].rpartition(":")
                    m = re.search(r'"(.+?)",pid=(\d+)', line)
                    if port.isdigit():
                        out.append(("TCP", int(port), host, int(m.group(2)) if m else 0, m.group(1) if m else "?"))
    uniq = {}
    for r in out:
        uniq.setdefault((r[0], r[1], r[3]), r)
    return sorted(uniq.values(), key=lambda r: (r[0], r[1]))


@builtin("ports", cat="Network", summary="Which programs are listening on which ports.",
         usage="ports            TCP\nports --udp      UDP too\nports 3000       who has port 3000")
def cmd_ports(ctx):
    o, rest = ctx.opts({"udp": bool})
    rows = []
    for proto, port, host, pid, prog in listening_ports():
        if proto == "UDP" and not o["udp"]:
            continue
        if rest and str(port) not in rest:
            continue
        where = "everyone" if host in ("0.0.0.0", "[::]", "*", "::") else "this PC" if host in \
            ("127.0.0.1", "[::1]", "localhost", "::1") else host
        rows.append([ui.acc(str(port)), proto, ui.b(prog), str(pid), ui.dim(where)])
    if not rows:
        ui.say(ui.dim("Nothing is listening" + (f" on {', '.join(rest)}." if rest else ".")))
        return 1 if rest else 0
    ui.table(rows, header=["Port", "Type", "Program", "PID", "Reachable by"], aligns="rllrl")
    return 0


@builtin("killport", "freeport", cat="Network", summary="Stop whatever is using a port.", usage="killport 3000")
def cmd_killport(ctx):
    if not ctx.args or not all(a.isdigit() for a in ctx.args):
        raise Usage()
    rc = 0
    for a in ctx.args:
        hits = [r for r in listening_ports() if r[1] == int(a) and r[3] not in (0, 4)]
        if not hits:
            ui.say(ui.dim(f"Port {a} is free."))
            continue
        for proto, port, host, pid, prog in {(r[3]): r for r in hits}.values():
            good, msg = kill_pid(pid)
            ui.say((ui.ok(f"freed port {port}: stopped ") if good else ui.err("couldn't stop ")) + f"{prog} ({pid})")
            rc |= 0 if good else 1
    return rc


# -------------------------------------------------------------- sysinfo
def _win_reg(path, name, hive="HKEY_LOCAL_MACHINE"):
    try:
        import winreg
        with winreg.OpenKey(getattr(winreg, hive), path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def memory():
    if IS_WIN:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
        m = MS()
        m.len = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total, m.total - m.avail
    if IS_MAC:
        total = int(_out(["sysctl", "-n", "hw.memsize"]).strip() or 0)
        vm = _out(["vm_stat"])
        page = 4096
        m = re.search(r"page size of (\d+)", vm)
        if m:
            page = int(m.group(1))
        free = sum(int(x) for x in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", vm)) * page
        return total, total - free
    info = {}
    try:
        for l in open("/proc/meminfo"):
            k, v = l.split(":")
            info[k] = int(v.split()[0]) * 1024
    except OSError:
        return 0, 0
    return info.get("MemTotal", 0), info.get("MemTotal", 0) - info.get("MemAvailable", 0)


def uptime_seconds():
    if IS_WIN:
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return ctypes.windll.kernel32.GetTickCount64() / 1000
    try:
        return float(open("/proc/uptime").read().split()[0])
    except OSError:
        m = re.search(r"sec = (\d+)", _out(["sysctl", "-n", "kern.boottime"]))
        return time.time() - int(m.group(1)) if m else 0


def cpu_name():
    if IS_WIN:
        return (_win_reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or platform.processor()).strip()
    if IS_MAC:
        return _out(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.machine()
    try:
        for l in open("/proc/cpuinfo"):
            if l.startswith("model name"):
                return l.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def os_name():
    if IS_WIN:
        name = _win_reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName") or "Windows"
        build = _win_reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuild") or ""
        disp = _win_reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion") or ""
        if build.isdigit() and int(build) >= 22000:
            name = name.replace("Windows 10", "Windows 11")
        return f"{name} {disp} (build {build})".strip()
    if IS_MAC:
        return f"macOS {platform.mac_ver()[0]}"
    try:
        for l in open("/etc/os-release"):
            if l.startswith("PRETTY_NAME="):
                return l.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def battery():
    if IS_WIN:
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_byte), ("flag", ctypes.c_byte), ("pct", ctypes.c_byte),
                        ("saver", ctypes.c_byte), ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]
        s = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)) and s.flag != -128 and 0 <= s.pct <= 100:
            return f"{s.pct}%" + (" charging" if s.ac == 1 else "")
        return None
    for b in ("/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"):
        try:
            return open(b + "/capacity").read().strip() + "% " + open(b + "/status").read().strip().lower()
        except OSError:
            pass
    if IS_MAC:
        m = re.search(r"(\d+)%;\s*(\w+)", _out(["pmset", "-g", "batt"]))
        return f"{m.group(1)}% {m.group(2)}" if m else None
    return None


def drives():
    out = []
    if IS_WIN:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                d = f"{chr(65 + i)}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(d) in (2, 3, 4, 6):
                    try:
                        u = shutil.disk_usage(d)
                        out.append((d, u.total, u.used))
                    except OSError:
                        pass
    else:
        for m in ("/", os.path.expanduser("~")):
            try:
                u = shutil.disk_usage(m)
                if not any(o[1] == u.total and o[2] == u.used for o in out):
                    out.append((m, u.total, u.used))
            except OSError:
                pass
    return out


LOGO = [
    "  ┌───────────┐ ",
    "  │ ›_        │ ",
    "  │           │ ",
    "  │  Commands │ ",
    "  └───────────┘ ",
]


@builtin("sysinfo", "neofetch", "fetch", cat="System", summary="This computer at a glance.")
def cmd_sysinfo(ctx):
    total, used = memory()
    up = uptime_seconds()
    d, h, m = int(up // 86400), int(up % 86400 // 3600), int(up % 3600 // 60)
    facts = [
        ("User", f"{os.environ.get('USERNAME') or os.environ.get('USER', '?')}@{socket.gethostname()}"),
        ("System", os_name()),
        ("CPU", f"{cpu_name()} · {os.cpu_count()} threads"),
        ("Memory", f"{ui.human(used)} / {ui.human(total)}  " + ui.paint(ui.bar(used / total if total else 0, 14), "accent")),
        ("Up for", (f"{d}d " if d else "") + f"{h}h {m}m"),
    ]
    for dv, t, u in drives():
        facts.append((f"Disk {dv}", f"{ui.human(u)} / {ui.human(t)}  " + ui.paint(ui.bar(u / t if t else 0, 14), "accent")))
    bat = battery()
    if bat:
        facts.append(("Battery", bat))
    facts.append(("Shell", f"Commands {VERSION} · Odus {backends.odus_version() or '-'} · Python {platform.python_version()}"))
    rows = max(len(LOGO), len(facts))
    for i in range(rows):
        logo = ui.paint(LOGO[i], "accent", bold=True) if i < len(LOGO) else " " * len(LOGO[0])
        fact = f"{ui.paint(ui.pad(facts[i][0], 9), 'glyph', bold=True)} {facts[i][1]}" if i < len(facts) else ""
        ui.say(logo + "  " + fact)
    return 0


# ------------------------------------------------------------------ net
def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


@builtin("ip", "myip", cat="Network", summary="Your IP addresses. --public asks the internet for your public one.",
         usage="ip [--public]")
def cmd_ip(ctx):
    o, _ = ctx.opts({"public": bool})
    for i in local_ips():
        ui.say(ui.dim("local   ") + ui.acc(i))
    if o["public"]:
        import urllib.request
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=8) as r:
                ui.say(ui.dim("public  ") + ui.acc(r.read().decode().strip()))
        except OSError as e:
            ui.say(ui.err(f"Couldn't reach the internet: {e}"))
            return 1
    return 0


@builtin("serve", cat="Network", summary="Share a folder over HTTP (on this PC and your network).",
         usage="serve [folder] [-p 8000] [--local]   (--local: only this PC)")
def cmd_serve(ctx):
    import http.server
    import functools
    o, rest = ctx.opts({"p|port": int, "local": bool})
    root = resolve(rest[0]) if rest else os.getcwd()
    port = o["port"] or 8000
    bind = "127.0.0.1" if o["local"] else "0.0.0.0"

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            code = str(args[1]) if len(args) > 1 else ""
            role = "ok" if code.startswith("2") else "warn" if code.startswith("3") else "err"
            ui.say(ui.dim(time.strftime("%H:%M:%S ")) + ui.paint(code, role) + f" {self.command} {self.path}"
                   + ui.dim(f"  {self.client_address[0]}"))

    handler = functools.partial(Quiet, directory=root)
    for p in range(port, port + 20):
        try:
            httpd = http.server.ThreadingHTTPServer((bind, p), handler)
            port = p
            break
        except OSError:
            continue
    else:
        ui.say(ui.err("No free port found."))
        return 1
    ui.say(ui.b("Serving ") + ui.paint(root, "path"))
    ui.say("  " + ui.acc(f"http://localhost:{port}/"))
    if not o["local"]:
        for i in local_ips():
            ui.say("  " + ui.acc(f"http://{i}:{port}/") + ui.dim("  (other devices on your network)"))
    ui.say(ui.dim("Ctrl+C stops."))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    ui.say(ui.dim("Stopped."))
    return 0


def color_json(obj, indent=0):
    sp = "  "
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [f"{sp * (indent + 1)}{ui.paint(json.dumps(k, ensure_ascii=False), 'var')}: {color_json(v, indent + 1)}"
                 for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + sp * indent + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        return "[\n" + ",\n".join(sp * (indent + 1) + color_json(v, indent + 1) for v in obj) + "\n" + sp * indent + "]"
    if isinstance(obj, str):
        return ui.paint(json.dumps(obj, ensure_ascii=False), "string")
    if isinstance(obj, bool) or obj is None:
        return ui.paint(json.dumps(obj), "op")
    return ui.paint(json.dumps(obj), "exe")


def json_query(obj, path: str):
    for part in re.findall(r"[^.\[\]]+", path):
        if isinstance(obj, list):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            if part not in obj:
                raise KeyError(part)
            obj = obj[part]
        else:
            raise KeyError(part)
    return obj


@builtin("json", cat="Text & data", summary="Pretty, colored JSON from a file or pipe. Pick parts with a path.",
         usage="json data.json              pretty print\njson data.json users.0.name\ncurl -s URL | json .items")
def cmd_json(ctx):
    args = list(ctx.args)
    path = None
    if args and (args[-1].startswith(".") and not os.path.exists(args[-1]) or
                 (len(args) == 2 and not os.path.exists(args[-1]))):
        path = args.pop()
    text = ctx.text_in(args)
    try:
        obj = json.loads(text)
    except ValueError as e:
        ui.say(ui.err(f"That isn't valid JSON: {e}"))
        return 1
    if path:
        try:
            obj = json_query(obj, path)
        except (KeyError, IndexError, ValueError):
            ui.say(ui.err(f"Nothing at {path}"))
            return 1
    if isinstance(obj, str) and path:
        ui.say(obj)
    else:
        ui.say(color_json(obj) if ui.color_on() else json.dumps(obj, indent=2, ensure_ascii=False))
    return 0


@builtin("http", "fetch-url", cat="Network",
         summary="Talk to web APIs: GET/POST with JSON, colored responses.",
         usage="http example.com/api\nhttp POST api.test/users name=Ann age:=30   (= text, := JSON)\n"
               "http GET api.test q==search  Authorization:\"Bearer x\"   (== query, : header)\n"
               "-v shows headers · -o file saves the body")
def cmd_http(ctx):
    import urllib.request
    import urllib.parse
    import urllib.error
    o, rest = ctx.opts({"v|verbose": bool, "o|output": str})
    if not rest:
        raise Usage()
    method = "GET"
    if rest[0].upper() in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        method = rest.pop(0).upper()
    if not rest:
        raise Usage()
    url = rest.pop(0)
    if not re.match(r"^https?://", url):
        url = ("http://" if url.startswith(("localhost", "127.")) else "https://") + url
    headers, query, body = {"User-Agent": f"Commands/{VERSION}"}, [], {}
    for item in rest:
        if "==" in item:
            k, v = item.split("==", 1)
            query.append((k, v))
        elif ":=" in item:
            k, v = item.split(":=", 1)
            body[k] = json.loads(v)
        elif "=" in item:
            k, v = item.split("=", 1)
            body[k] = v
        elif ":" in item:
            k, v = item.split(":", 1)
            headers[k.strip()] = v.strip()
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    data = None
    if body:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
        if method == "GET":
            method = "POST"
    if ctx.stdin and data is None and method in ("POST", "PUT", "PATCH"):
        data = ctx.stdin.encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        status = resp.status
    except urllib.error.HTTPError as e:
        resp, status = e, e.code
    except (urllib.error.URLError, OSError) as e:
        ui.say(ui.err(f"Couldn't connect: {getattr(e, 'reason', e)}"))
        return 1
    raw = resp.read()
    ms = (time.time() - t) * 1000
    role = "ok" if status < 300 else "warn" if status < 400 else "err"
    ui.say(ui.paint(f"{status} {getattr(resp, 'reason', '')}", role, bold=True) +
           ui.dim(f"  {method} {url} · {ms:.0f} ms · {ui.human(len(raw))}"))
    if o["verbose"]:
        for k, v in resp.headers.items():
            ui.say(ui.paint(k, "var") + ui.dim(": ") + v)
    ui.say()
    if o["output"]:
        with open(o["output"], "wb") as f:
            f.write(raw)
        ui.say(ui.ok(f"Saved to {o['output']}"))
        return 0 if status < 400 else 1
    ctype = resp.headers.get("Content-Type", "")
    text = raw.decode("utf-8", "replace")
    if "json" in ctype or text[:1] in "[{":
        try:
            ui.say(color_json(json.loads(text)) if ui.color_on() else json.dumps(json.loads(text), indent=2))
            return 0 if status < 400 else 1
        except ValueError:
            pass
    ui.say(text if len(text) < 20000 else text[:20000] + ui.dim(f"\n… {len(text) - 20000} more characters (use -o)"))
    return 0 if status < 400 else 1


# ----------------------------------------------------------------- data
_MATH = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_MATH.update({"abs": abs, "round": round, "min": min, "max": max, "int": int, "float": float, "hex": hex,
              "bin": bin, "oct": oct, "sum": sum})
_UNITS = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}


def safe_eval(expr: str):
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb)\b", lambda m: f"({m.group(1)}*{_UNITS[m.group(2).lower()]})", expr, flags=re.I)
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)
    tree = ast.parse(expr, mode="eval")
    ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call, ast.Name, ast.Load, ast.operator,
          ast.unaryop, ast.Tuple, ast.Compare, ast.cmpop, ast.BoolOp, ast.boolop, ast.IfExp)
    for node in ast.walk(tree):
        if not isinstance(node, ok):
            raise ValueError(f"'{type(node).__name__}' isn't allowed in calc")
        if isinstance(node, ast.Name) and node.id not in _MATH and node.id != "ans":
            raise ValueError(f"unknown name '{node.id}'")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            r = node.right
            if isinstance(r, ast.Constant) and isinstance(r.value, (int, float)) and r.value > 10000:
                raise ValueError("that power is too big")
    return eval(compile(tree, "calc", "eval"), {"__builtins__": {}}, dict(_MATH, ans=_last_ans))


_last_ans = 0


@builtin("calc", "=", cat="Text & data", summary="Math: calc 2^10 / 3, sqrt(2), 15% * 80, 4GB / 3, hex(255).",
         usage="calc <expression>       calc (no words) opens a calculator; 'ans' is the last answer")
def cmd_calc(ctx):
    global _last_ans
    def one(expr):
        global _last_ans
        try:
            v = safe_eval(expr)
        except ZeroDivisionError:
            ui.say(ui.err("can't divide by zero"))
            return 1
        except (SyntaxError, ValueError, TypeError, OverflowError) as e:
            ui.say(ui.err(str(e) if not isinstance(e, SyntaxError) else "that isn't a math expression"))
            return 1
        _last_ans = v
        if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
            v = int(v)
        line = ui.acc(f"{v:,}" if isinstance(v, int) and abs(v) >= 10000 else str(v))
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            line += ui.dim(f"   hex {v:#x} · bin {v:#b}" if v < 2 ** 64 else "")
            if v >= 1024:
                line += ui.dim(f" · {ui.human(v)}")
        ui.say(line)
        return 0
    if ctx.args:
        return one(ctx.raw if ctx.raw and ctx.raw.strip() else " ".join(ctx.args))
    ui.say(ui.dim("Calculator · empty line or Esc to leave"))
    while True:
        e = ui.ask(ui.paint("=", "glyph", bold=True))
        if not e:
            return 0
        one(e)


@builtin("uuid", "guid", cat="Text & data", summary="Make random IDs.", usage="uuid [count]")
def cmd_uuid(ctx):
    for _ in range(int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1):
        ui.say(str(uuid.uuid4()))
    return 0


@builtin("password", "pw", cat="Text & data", summary="Strong random password (copied to the clipboard).",
         usage="password [length] [--simple]   (--simple: letters and digits only)")
def cmd_password(ctx):
    o, rest = ctx.opts({"simple": bool, "no-copy": bool})
    n = int(rest[0]) if rest and rest[0].isdigit() else 20
    chars = string.ascii_letters + string.digits + ("" if o["simple"] else "!@#$%&*-_=+?")
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(n))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            break
    ui.say(pw)
    if not o["no-copy"] and sys.stdout.isatty():
        try:
            clipboard_set(pw)
            ui.say(ui.dim("copied to the clipboard"))
        except OSError:
            pass
    return 0


@builtin("base64", "b64", cat="Text & data", summary="Encode or decode base64 (-d). Text, file or pipe.",
         usage="base64 hello      base64 -d aGVsbG8=      base64 -f picture.png")
def cmd_b64(ctx):
    o, rest = ctx.opts({"d|decode": bool, "f|file": bool})
    if o["file"]:
        data = open(rest[0], "rb").read()
    else:
        data = (" ".join(rest) if rest else (ctx.stdin or "").rstrip("\n")).encode()
    if o["decode"]:
        try:
            out = base64.b64decode(data + b"=" * (-len(data) % 4))
        except ValueError:
            ui.say(ui.err("That isn't base64."))
            return 1
        ui.say(out.decode("utf-8", "replace"))
    else:
        ui.say(base64.b64encode(data).decode())
    return 0


@builtin("choose", "pick", cat="Text & data",
         summary="Pick a line from piped text with arrow keys; prints what you picked.",
         usage="dir /b | choose          git branch | choose --multi\nchoose red green blue")
def cmd_choose(ctx):
    o, rest = ctx.opts({"multi": bool, "title": str})
    items = rest or [l for l in (ctx.stdin or "").splitlines() if l.strip()]
    if not items:
        raise Usage("nothing to choose from")
    pick = ui.choose(items, ui.b(o["title"]) if o["title"] else "", multi=o["multi"])
    if pick is None:
        return 1
    for p in (pick if isinstance(pick, list) else [pick]):
        ui.say(p.strip())
    return 0


@builtin("confirm", "ask", cat="Text & data", summary="Ask yes/no; exit code 0 for yes. For scripts.",
         usage='confirm "Deploy now?" && deploy.cmd')
def cmd_confirm(ctx):
    return 0 if ui.confirm(" ".join(ctx.args) or "Continue?") else 1


# ------------------------------------------------------------ clipboard
def _win_clip():
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    return ctypes, u32, k32


def _win_open_clip(u32):
    for _ in range(20):
        if u32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("the clipboard is busy")


def clipboard_set(text: str):
    if IS_WIN:
        ctypes, u32, k32 = _win_clip()
        data = text.encode("utf-16-le") + b"\x00\x00"
        _win_open_clip(u32)
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(0x0002, len(data))
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(13, h):
                raise OSError("couldn't set the clipboard")
        finally:
            u32.CloseClipboard()
        return
    for argv in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(argv[0]):
            subprocess.run(argv, input=text.encode())
            return
    raise OSError("no clipboard tool found (install wl-clipboard or xclip)")


def clipboard_get() -> str:
    if IS_WIN:
        ctypes, u32, k32 = _win_clip()
        if not u32.IsClipboardFormatAvailable(13):
            return ""
        _win_open_clip(u32)
        try:
            h = u32.GetClipboardData(13)
            if not h:
                return ""
            p = k32.GlobalLock(h)
            try:
                return ctypes.wstring_at(p)
            finally:
                k32.GlobalUnlock(h)
        finally:
            u32.CloseClipboard()
    for argv in (["pbpaste"], ["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-ob"]):
        if shutil.which(argv[0]):
            return subprocess.run(argv, capture_output=True).stdout.decode("utf-8", "replace")
    raise OSError("no clipboard tool found")


@builtin("clip", "copy-text", cat="Text & data", summary="Copy piped text or words to the clipboard (Unicode-safe).",
         usage="dir | clip      clip some text      clip -f notes.txt")
def cmd_clip(ctx):
    o, rest = ctx.opts({"f|file": str})
    if o["file"]:
        text = open(o["file"], encoding="utf-8", errors="replace").read()
    elif rest:
        text = " ".join(rest)
    else:
        text = ctx.stdin or ""
    clipboard_set(text)
    if sys.stdout.isatty():
        ui.say(ui.dim(f"copied {len(text)} characters"))
    return 0


@builtin("paste", cat="Text & data", summary="Print what's on the clipboard (pipe it: paste | grep x).")
def cmd_paste(ctx):
    t = clipboard_get()
    sys.stdout.write(t if t.endswith("\n") or not t else t + "\n")
    return 0


# ------------------------------------------------------- notifications
def desktop_notify(title: str, message: str, quiet=False):
    try:
        if IS_WIN:
            ps = shutil.which("powershell") or "powershell"
            q = lambda s: s.replace("'", "''").replace("&", "&amp;").replace("<", "&lt;")
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$x = $t.GetElementsByTagName('text');"
                f"$x.Item(0).AppendChild($t.CreateTextNode('{q(title)}')) > $null;"
                f"$x.Item(1).AppendChild($t.CreateTextNode('{q(message)}')) > $null;"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show("
                "[Windows.UI.Notifications.ToastNotification]::new($t))")
            enc = base64.b64encode(script.encode("utf-16-le")).decode()
            subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        elif IS_MAC:
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'])
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message])
        else:
            raise OSError("no notification tool")
        return True
    except OSError as e:
        if not quiet:
            ui.say(ui.err(f"Couldn't show a notification: {e}"))
        return False


@builtin("notify", cat="System", summary="Show a desktop notification. Handy at the end of long jobs.",
         usage='notify "Build done"\nnpm run build && notify "Build finished" || notify "Build failed"')
def cmd_notify(ctx):
    if not ctx.args:
        raise Usage()
    title = ctx.args[0] if len(ctx.args) > 1 else "Commands"
    msg = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ctx.args[0]
    return 0 if desktop_notify(title, msg) else 1


_reminders = []


@builtin("remind", "timer", cat="System", summary="A reminder after some time (works while you keep typing).",
         usage='remind 10m "tea is ready"     remind 90 stretch      remind (list them)')
def cmd_remind(ctx):
    from .builtins_core import parse_duration
    if not ctx.args:
        now = time.time()
        live = [r for r in _reminders if r[0] > now]
        if not live:
            ui.say(ui.dim("No reminders."))
        for t, msg in live:
            ui.say(f"{ui.acc(time.strftime('%H:%M:%S', time.localtime(t)))}  {msg}  {ui.dim(f'in {int(t - now)}s')}")
        return 0
    secs = parse_duration(ctx.args[0])
    msg = " ".join(ctx.args[1:]) or "Time's up!"
    when = time.time() + secs
    _reminders.append((when, msg))
    sh = ctx.sh

    def fire():
        sh.notices.append(ui.paint("⏰ ", "warn") + ui.b(msg))
        term.write("\a")
        desktop_notify("Reminder", msg, quiet=True)
    th = threading.Timer(secs, fire)
    th.daemon = True
    th.start()
    ui.say(ui.ok(f"Reminder set for {time.strftime('%H:%M:%S', time.localtime(when))}."))
    return 0


# ------------------------------------------------------------------ jobs
class Job:
    def __init__(self, n, line, proc, log):
        self.n, self.line, self.proc, self.log = n, line, proc, log
        self.start = time.time()
        self.reported = False


@builtin("bg", "background", cat="System", summary="Run a command in the background; output goes to a log.",
         usage="bg npm run dev        then: jobs, jobs log 1, jobs stop 1")
def cmd_bg(ctx):
    if not ctx.args:
        raise Usage()
    line = lexer.join(ctx.args) if len(ctx.args) > 1 else ctx.args[0]
    n = (max((j.n for j in ctx.sh.jobs), default=0)) + 1
    log = os.path.join(ui.CONFIG_DIR, "jobs", f"job-{os.getpid()}-{n}.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    f = open(log, "wb")
    env = ctx.sh.api_env()
    if IS_WIN:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        argv = [backends.python_exe(), os.path.join(here, "run.py"), "--plain", "-c", line]
        env["PYTHONPATH"] = here + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                creationflags=0x00000200 | NO_WINDOW)
    else:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = here + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen([backends.python_exe(), os.path.join(here, "run.py"), "--plain", "-c", line], stdout=f,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    ctx.sh.jobs.append(Job(n, line, proc, log))
    ui.say(ui.dim(f"[{n}] ") + ui.b(f"started") + ui.dim(f" pid {proc.pid} · log: jobs log {n}"))
    return 0


@builtin("jobs", cat="System", summary="Background jobs: list, log, stop.", usage="jobs | jobs log <n> | jobs stop <n>")
def cmd_jobs(ctx):
    jobs = ctx.sh.jobs
    if ctx.args and ctx.args[0] in ("log", "stop", "kill") and len(ctx.args) > 1:
        j = next((j for j in jobs if str(j.n) == ctx.args[1].lstrip("%")), None)
        if not j:
            ui.say(ui.err("No such job."))
            return 1
        if ctx.args[0] == "log":
            sys.stdout.write(open(j.log, encoding="utf-8", errors="replace").read())
            return 0
        kill_pid(j.proc.pid)
        ui.say(ui.ok(f"stopped [{j.n}]"))
        return 0
    if not jobs:
        ui.say(ui.dim("No background jobs. Start one with: bg <command>"))
        return 0
    rows = []
    for j in jobs:
        rc = j.proc.poll()
        st = ui.paint("running", "accent") if rc is None else (ui.ok("done") if rc == 0 else ui.err(f"failed ({rc})"))
        rows.append([f"[{j.n}]", st, f"{int(time.time() - j.start)}s", j.line])
    ui.table(rows)
    return 0


def check_jobs(sh):
    for j in sh.jobs:
        if not j.reported and j.proc.poll() is not None:
            j.reported = True
            rc = j.proc.returncode
            sh.notices.append(ui.dim(f"[{j.n}] ") + (ui.ok("done") if rc == 0 else ui.err(f"failed ({rc})")) +
                              ui.dim(f"  {j.line}"))
