"""Low-level terminal access: ANSI on Windows, raw key reading on every OS."""
import os
import sys
import shutil
import contextlib
import unicodedata

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def enable_vt() -> bool:
    """Turn on ANSI escape codes in Windows consoles (some programs switch them off)."""
    if not IS_WIN:
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        ok = False
        for std in (-11, -12):  # stdout, stderr
            h = k.GetStdHandle(std)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0001 | 0x0004)
                ok = True
        return ok
    except Exception:
        return False


def is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.__stdout__.isatty()
    except Exception:
        return False


def size():
    s = shutil.get_terminal_size((100, 30))
    return max(20, s.columns), max(5, s.lines)


def char_width(ch: str) -> int:
    if ch == "\t":
        return 4
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(s: str) -> int:
    return sum(char_width(ch) for ch in s)


def write(s: str):
    out = sys.__stdout__
    out.write(s)
    out.flush()


# ------------------------------------------------------------------ keys
if IS_WIN:
    import msvcrt

    _EXT = {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home", "O": "end",
            "S": "delete", "s": "ctrl-left", "t": "ctrl-right", "A": "f7", "I": "pgup",
            "Q": "pgdn", "\x0f": "shift-tab", "\x93": "ctrl-delete", "R": "insert",
            ";": "f1", "=": "f3", "w": "ctrl-home", "u": "ctrl-end"}
    _CTRL = {"\r": "enter", "\n": "enter", "\x08": "backspace", "\t": "tab", "\x1b": "esc",
             "\x03": "ctrl-c", "\x04": "ctrl-d", "\x0c": "ctrl-l", "\x7f": "ctrl-backspace",
             "\x01": "home", "\x05": "end", "\x15": "ctrl-u", "\x0b": "ctrl-k", "\x17": "ctrl-w",
             "\x12": "ctrl-r", "\x1a": "ctrl-z", "\x10": "up", "\x0e": "down"}

    def read_key():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return _EXT.get(msvcrt.getwch())
        return _CTRL.get(ch, ch)

    def key_ready() -> bool:
        return msvcrt.kbhit()

    @contextlib.contextmanager
    def raw_mode():
        """Let Ctrl+C arrive as a key while editing a line."""
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        got = k.GetConsoleMode(h, ctypes.byref(mode))
        if got:
            k.SetConsoleMode(h, mode.value & ~0x0001)
        try:
            yield
        finally:
            if got:
                k.SetConsoleMode(h, mode.value)
else:
    import select
    import termios
    import codecs

    _SEQ = {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "[H": "home", "[F": "end",
            "OH": "home", "OF": "end", "[1~": "home", "[4~": "end", "[7~": "home", "[8~": "end",
            "[3~": "delete", "[5~": "pgup", "[6~": "pgdn", "[Z": "shift-tab", "[18~": "f7",
            "[1;5C": "ctrl-right", "[1;5D": "ctrl-left", "[1;3C": "ctrl-right", "[1;3D": "ctrl-left",
            "OP": "f1", "OR": "f3", "[2~": "insert", "[3;5~": "ctrl-delete",
            "b": "ctrl-left", "f": "ctrl-right"}
    _CTRL = {"\r": "enter", "\n": "enter", "\x7f": "backspace", "\x08": "ctrl-backspace",
             "\t": "tab", "\x03": "ctrl-c", "\x04": "ctrl-d", "\x0c": "ctrl-l", "\x01": "home",
             "\x05": "end", "\x15": "ctrl-u", "\x0b": "ctrl-k", "\x17": "ctrl-w", "\x12": "ctrl-r",
             "\x1a": "ctrl-z", "\x10": "up", "\x0e": "down"}
    _dec = codecs.getincrementaldecoder("utf-8")("replace")

    def _byte(timeout=None):
        fd = sys.stdin.fileno()
        if timeout is not None:
            r, _, _ = select.select([fd], [], [], timeout)
            if not r:
                return None
        b = os.read(fd, 1)
        return b if b else None

    def _char(timeout=None):
        while True:
            b = _byte(timeout)
            if b is None:
                return None
            s = _dec.decode(b)
            if s:
                return s

    def read_key():
        ch = _char()
        if ch is None:
            return "ctrl-d"
        if ch == "\x1b":
            seq = ""
            nxt = _char(0.03)
            if nxt is None:
                return "esc"
            seq = nxt
            if nxt in "[O":
                while True:
                    c = _char(0.03)
                    if c is None:
                        break
                    seq += c
                    if c.isalpha() or c == "~":
                        break
            return _SEQ.get(seq)
        return _CTRL.get(ch, ch)

    def key_ready() -> bool:
        r, _, _ = select.select([sys.stdin.fileno()], [], [], 0)
        return bool(r)

    @contextlib.contextmanager
    def raw_mode():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)
        new[0] &= ~(termios.IXON | termios.ICRNL)
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
