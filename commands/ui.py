"""The look of Commands: themes, colors, markup, tables, menus and prompts."""
import os
import re
import sys
import json

from . import term

IS_WIN = term.IS_WIN
CONFIG_DIR = (os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Commands") if IS_WIN
              else os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "commands"))

# Each theme keeps the black console of CMD but gives every kind of word its own color.
THEMES = {
    "commands": {  # default: warm amber paths, cool cyan prompt
        "text": (214, 214, 214), "dim": (110, 110, 118), "path": (240, 178, 62), "glyph": (86, 200, 236),
        "git": (150, 130, 230), "err": (255, 98, 98), "ok": (110, 214, 128), "warn": (246, 200, 90),
        "builtin": (110, 214, 160), "exe": (126, 200, 255), "odus": (214, 140, 255), "cmdlet": (98, 160, 255),
        "unknown": (255, 98, 98), "string": (236, 210, 120), "flag": (120, 200, 210), "op": (230, 120, 170),
        "var": (100, 220, 220), "accent": (240, 178, 62), "title": (255, 255, 255), "sel": (40, 60, 90),
    },
    "classic": {  # as close to old CMD as it gets, a few hints of color
        "text": (192, 192, 192), "dim": (128, 128, 128), "path": (192, 192, 192), "glyph": (192, 192, 192),
        "git": (128, 128, 128), "err": (255, 85, 85), "ok": (85, 255, 85), "warn": (255, 255, 85),
        "builtin": (230, 230, 230), "exe": (230, 230, 230), "odus": (230, 230, 230), "cmdlet": (230, 230, 230),
        "unknown": (255, 85, 85), "string": (200, 200, 200), "flag": (200, 200, 200), "op": (200, 200, 200),
        "var": (200, 200, 200), "accent": (255, 255, 255), "title": (255, 255, 255), "sel": (60, 60, 60),
    },
    "phosphor": {  # green-screen terminal
        "text": (90, 230, 110), "dim": (40, 130, 60), "path": (150, 255, 150), "glyph": (200, 255, 200),
        "git": (80, 200, 100), "err": (255, 120, 80), "ok": (150, 255, 150), "warn": (220, 255, 120),
        "builtin": (180, 255, 180), "exe": (140, 240, 150), "odus": (210, 255, 160), "cmdlet": (120, 220, 140),
        "unknown": (255, 120, 80), "string": (200, 255, 140), "flag": (120, 210, 130), "op": (220, 255, 200),
        "var": (170, 255, 200), "accent": (150, 255, 150), "title": (220, 255, 220), "sel": (20, 70, 30),
    },
    "amber": {  # amber monitor
        "text": (255, 176, 0), "dim": (150, 100, 0), "path": (255, 210, 110), "glyph": (255, 230, 170),
        "git": (220, 150, 40), "err": (255, 90, 60), "ok": (255, 220, 120), "warn": (255, 240, 150),
        "builtin": (255, 220, 140), "exe": (255, 196, 80), "odus": (255, 236, 180), "cmdlet": (240, 170, 50),
        "unknown": (255, 90, 60), "string": (255, 236, 160), "flag": (230, 160, 40), "op": (255, 240, 200),
        "var": (255, 220, 160), "accent": (255, 210, 110), "title": (255, 240, 210), "sel": (80, 50, 0),
    },
    "ice": {  # cold blues and whites
        "text": (220, 230, 240), "dim": (100, 120, 140), "path": (140, 200, 255), "glyph": (255, 255, 255),
        "git": (160, 170, 255), "err": (255, 110, 130), "ok": (130, 240, 210), "warn": (250, 220, 130),
        "builtin": (150, 230, 255), "exe": (190, 215, 255), "odus": (200, 170, 255), "cmdlet": (120, 170, 255),
        "unknown": (255, 110, 130), "string": (190, 255, 230), "flag": (150, 200, 230), "op": (230, 170, 255),
        "var": (130, 240, 240), "accent": (140, 200, 255), "title": (255, 255, 255), "sel": (30, 50, 80),
    },
}
BASIC = {"black": (0, 0, 0), "red": (255, 98, 98), "green": (110, 214, 128), "yellow": (246, 200, 90),
         "blue": (98, 160, 255), "magenta": (214, 140, 255), "cyan": (86, 200, 236), "white": (240, 240, 240),
         "gray": (130, 130, 138), "grey": (130, 130, 138), "orange": (240, 160, 60)}

_theme_name = "commands"
_force_color = None


def set_theme(name: str) -> bool:
    global _theme_name
    if name in THEMES:
        _theme_name = name
        return True
    return False


def theme_name() -> str:
    return _theme_name


def color_on(stream=None) -> bool:
    if _force_color is not None:
        return _force_color
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    try:
        return stream.isatty()
    except Exception:
        return False


def rgb(role: str):
    t = THEMES[_theme_name]
    return t.get(role) or BASIC.get(role) or t["text"]


def fg(role: str) -> str:
    r, g, b = rgb(role)
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(role: str) -> str:
    r, g, b = rgb(role)
    return f"\x1b[48;2;{r};{g};{b}m"


RESET = "\x1b[0m"
BOLD = "\x1b[1m"


def paint(text: str, role: str, bold=False, stream=None) -> str:
    if not color_on(stream):
        return text
    return f"{BOLD if bold else ''}{fg(role)}{text}{RESET}"


# short names used everywhere
def dim(t): return paint(t, "dim")
def err(t): return paint(t, "err")
def ok(t): return paint(t, "ok")
def warn(t): return paint(t, "warn")
def acc(t): return paint(t, "accent")
def b(t): return paint(t, "title", bold=True)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def vwidth(s: str) -> int:
    return term.text_width(strip_ansi(s))


def pad(s: str, width: int, right=False) -> str:
    gap = max(0, width - vwidth(s))
    return (" " * gap + s) if right else (s + " " * gap)


_TAG = re.compile(r"\[(/?)([a-z ]*)\]")


def markup(text: str, stream=None) -> str:
    """[ok]done[/]  [b]bold[/]  [dim]...[/]  [red]...[/]  [[ for a literal ["""
    on = color_on(stream)
    out, stack, i = [], [], 0
    text = text.replace("[[", "\x00")
    for m in _TAG.finditer(text):
        out.append(text[i:m.start()])
        i = m.end()
        close, name = m.group(1), m.group(2).strip()
        if close:
            if stack:
                stack.pop()
            if on:
                out.append(RESET + "".join(_codes(n) for n in stack))
            continue
        if name not in THEMES[_theme_name] and name not in BASIC and name not in ("b", "bold", "i", "u"):
            out.append(m.group(0))
            continue
        stack.append(name)
        if on:
            out.append(_codes(name))
    out.append(text[i:])
    if on and stack:
        out.append(RESET)
    return "".join(out).replace("\x00", "[")


def _codes(name: str) -> str:
    return {"b": BOLD, "bold": BOLD, "i": "\x1b[3m", "u": "\x1b[4m"}.get(name) or fg(name)


def say(*parts, end="\n"):
    try:
        print(*parts, end=end, flush=True)
    except UnicodeEncodeError:
        print(*(p.encode("ascii", "replace").decode() for p in parts), end=end, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def table(rows, header=None, aligns=None, roles=None, emit=None):
    """Prints aligned columns. rows: list of lists of str (may contain color)."""
    emit = emit or say
    rows = [[str(c) for c in r] for r in rows]
    if header:
        rows_all = [list(header)] + rows
    else:
        rows_all = rows
    if not rows_all:
        return
    n = max(len(r) for r in rows_all)
    widths = [max((vwidth(r[i]) if i < len(r) else 0) for r in rows_all) for i in range(n)]
    aligns = aligns or "l" * n
    if header:
        emit("  ".join(paint(pad(h, widths[i]), "dim") for i, h in enumerate(header)).rstrip())
        emit(dim("  ".join("─" * w for w in widths)))
    for r in rows:
        cells = []
        for i in range(n):
            c = r[i] if i < len(r) else ""
            if roles and i < len(roles) and roles[i]:
                c = paint(c, roles[i])
            cells.append(pad(c, widths[i], right=(aligns[i] == "r")))
        emit("  ".join(cells).rstrip())


def bar(fraction: float, width: int = 24) -> str:
    fraction = max(0.0, min(1.0, fraction))
    full = int(fraction * width)
    rest = fraction * width - full
    parts = " ▏▎▍▌▋▊▉"
    s = "█" * full + (parts[int(rest * 8)] if full < width else "")
    return s.ljust(width)


def progress_line(label: str, value: float, total: float | None) -> str:
    cols, _ = term.size()
    if total:
        frac = value / total if total else 0
        txt = f" {frac * 100:5.1f}%"
        w = max(10, min(40, cols - vwidth(label) - 12))
        return f"{label} {paint(bar(frac, w), 'accent')}{txt}"
    return f"{label} {paint(str(value), 'accent')}"


# --------------------------------------------------------- interactive bits
def can_interact() -> bool:
    return term.is_tty()


def confirm(question: str, default=False) -> bool:
    if not can_interact():
        return default
    hint = "Y/n" if default else "y/N"
    term.write(f"{question} {dim('[' + hint + ']')} ")
    try:
        with term.raw_mode():
            while True:
                k = term.read_key()
                if k in ("y", "Y"):
                    ans = True
                    break
                if k in ("n", "N", "esc", "ctrl-c"):
                    ans = False
                    break
                if k == "enter":
                    ans = default
                    break
    except Exception:
        ans = default
    term.write(("yes" if ans else "no") + "\n")
    return ans


def ask(question: str, default: str = "", secret=False) -> str | None:
    """One line of input with a default. None if cancelled."""
    if not can_interact():
        return default
    buf = list(default)
    pre = f"{question} "
    def draw():
        shown = ("•" * len(buf)) if secret else "".join(buf)
        term.write("\r\x1b[K" + pre + shown)
    draw()
    with term.raw_mode():
        while True:
            k = term.read_key()
            if k == "enter":
                term.write("\n")
                return "".join(buf)
            if k in ("esc", "ctrl-c"):
                term.write("\n")
                return None
            if k == "backspace":
                if buf:
                    buf.pop()
            elif k == "ctrl-u":
                buf.clear()
            elif isinstance(k, str) and len(k) == 1 and k.isprintable():
                buf.append(k)
                while term.key_ready():
                    k2 = term.read_key()
                    if isinstance(k2, str) and len(k2) == 1 and k2.isprintable():
                        buf.append(k2)
                    elif k2 == "enter":
                        draw()
                        term.write("\n")
                        return "".join(buf)
            draw()


def choose(items: list, title: str = "", multi=False, start=0, query=""):
    """Arrow-key menu. Type to filter. Returns the chosen item (or list for multi), None if cancelled."""
    if not items or not can_interact():
        return None
    sel, top, picked = start, 0, set()
    _, rows = term.size()
    view = max(3, min(len(items), rows - 4, 15))
    drawn = 0

    def matches():
        if not query:
            return list(range(len(items)))
        q = query.lower()
        return [i for i, it in enumerate(items) if all(part in str(it).lower() for part in q.split())]

    def draw():
        nonlocal drawn, top
        cols, _ = term.size()
        idx = matches()
        out = []
        if drawn:
            out.append(f"\x1b[{drawn}A")
        out.append("\r\x1b[J")
        head = (title + "  " if title else "") + dim("↑↓ move · type to filter · Enter pick" +
                                                    (" · Space mark" if multi else "") + " · Esc cancel")
        out.append(head + "\n")
        out.append(paint("› ", "glyph") + query + "\n")
        s = min(sel, max(0, len(idx) - 1))
        if s < top:
            top = s
        if s >= top + view:
            top = s - view + 1
        lines = 0
        for pos in range(top, min(len(idx), top + view)):
            i = idx[pos]
            mark = (paint("◆ ", "ok") if i in picked else "◇ ") if multi else ""
            label = str(items[i])
            if vwidth(label) > cols - 6:
                label = label[:cols - 7] + "…"
            if pos == s:
                out.append(bg("sel") + fg("title") + " ▸ " + mark + label + " " + RESET + "\n")
            else:
                out.append("   " + mark + label + "\n")
            lines += 1
        if not idx:
            out.append(dim("   nothing matches") + "\n")
            lines += 1
        more = len(idx) - (top + view)
        if more > 0:
            out.append(dim(f"   … {more} more") + "\n")
            lines += 1
        drawn = 2 + lines
        term.write("".join(out))

    term.write("\x1b[?25l")
    try:
        with term.raw_mode():
            draw()
            while True:
                k = term.read_key()
                idx = matches()
                if k == "up":
                    sel = (sel - 1) % max(1, len(idx))
                elif k == "down":
                    sel = (sel + 1) % max(1, len(idx))
                elif k == "pgup":
                    sel = max(0, sel - view)
                elif k == "pgdn":
                    sel = min(len(idx) - 1, sel + view)
                elif k == "home":
                    sel = 0
                elif k == "end":
                    sel = len(idx) - 1
                elif k in ("esc", "ctrl-c"):
                    return None
                elif k == "enter":
                    if multi:
                        if not picked and idx:
                            picked.add(idx[min(sel, len(idx) - 1)])
                        return [items[i] for i in sorted(picked)]
                    if idx:
                        return items[idx[min(sel, len(idx) - 1)]]
                elif k == " " and multi:
                    if idx:
                        i = idx[min(sel, len(idx) - 1)]
                        picked.symmetric_difference_update({i})
                elif k == "backspace":
                    query = query[:-1]
                    sel = top = 0
                elif isinstance(k, str) and len(k) == 1 and k.isprintable():
                    query += k
                    sel = top = 0
                draw()
    finally:
        term.write(f"\x1b[{drawn}A\r\x1b[J\x1b[?25h" if drawn else "\x1b[?25h")


# --------------------------------------------------------------- config
def load_json(name: str, default):
    try:
        with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(name: str, data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    path = os.path.join(CONFIG_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
