"""Splitting command lines the way CMD does: quotes with "", ^ escapes,
&& || & between commands, | between pipeline stages, > >> < 2> redirects."""
import os
import re
import random
import time

CHAIN_OPS = ("&&", "||", "&")


def _scan(line: str):
    """Yields (index, char, in_quotes, escaped)."""
    q = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            q = not q
            yield i, ch, True, False
        elif ch == "^" and not q and i + 1 < n:
            yield i, ch, False, True
            i += 1
            yield i, line[i], False, True
        else:
            yield i, ch, q, False
        i += 1


def split_chain(line: str):
    """'a && b || c & d' -> [(None,'a'), ('&&','b'), ('||','c'), ('&','d')]"""
    parts, op, start = [], None, 0
    skip = -1
    chars = list(_scan(line))
    for k, (i, ch, q, esc) in enumerate(chars):
        if i <= skip or q or esc:
            continue
        if ch in "&|":
            nxt = line[i + 1] if i + 1 < len(line) else ""
            prev = line[i - 1] if i > 0 else ""
            if ch == "&" and prev == ">":  # 2>&1
                continue
            if ch == "&" and nxt == "&":
                parts.append((op, line[start:i]))
                op, start, skip = "&&", i + 2, i + 1
            elif ch == "|" and nxt == "|":
                parts.append((op, line[start:i]))
                op, start, skip = "||", i + 2, i + 1
            elif ch == "&":
                parts.append((op, line[start:i]))
                op, start = "&", i + 1
    parts.append((op, line[start:]))
    return [(o, s.strip()) for o, s in parts if s.strip() or o is None]


def split_pipe(segment: str):
    stages, start = [], 0
    for i, ch, q, esc in _scan(segment):
        if ch == "|" and not q and not esc:
            stages.append(segment[start:i].strip())
            start = i + 1
    stages.append(segment[start:].strip())
    return stages


def has_unquoted(segment: str, chars: str) -> bool:
    return any(ch in chars and not q and not esc for _, ch, q, esc in _scan(segment))


_REDIR = re.compile(r"(2>&1|[12]?>>|[12]?>|<)")


def tokenize(text: str):
    """Words with quotes removed. Redirect operators come back as their own tokens."""
    tokens, cur, started, quoted = [], [], False, False
    i, n, q = 0, len(text), False

    def flush():
        nonlocal cur, started, quoted
        if started:
            tokens.append(("".join(cur), quoted))
        cur, started, quoted = [], False, False

    while i < n:
        ch = text[i]
        if ch == '"':
            q = not q
            started = quoted = True
        elif q:
            cur.append(ch)
        elif ch == "^" and i + 1 < n:
            i += 1
            cur.append(text[i])
            started = True
        elif ch in " \t":
            flush()
        elif ch in "<>" or (ch in "12" and not started and i + 1 < n and text[i + 1] == ">"):
            m = _REDIR.match(text, i)
            if m:
                flush()
                tokens.append((m.group(1), "op"))
                i = m.end()
                continue
            cur.append(ch)
            started = True
        else:
            cur.append(ch)
            started = True
        i += 1
    flush()
    out = []
    for t, quoted in tokens:
        if quoted is False and (t == "~" or t.startswith(("~/", "~\\"))):
            t = os.path.expanduser("~") + t[1:]
        out.append((t, quoted))
    return out


def words(text: str) -> list:
    return [t for t, _ in tokenize(text)]


def take_redirects(tokens):
    """Splits off > file, >> file, < file, 2> file, 2>&1. Returns (args, redirects)."""
    args, red = [], {}
    it = iter(tokens)
    for t, kind in it:
        if kind == "op":
            if t == "2>&1":
                red["err_to_out"] = True
                continue
            target = next(it, (None, None))[0]
            if target is None:
                raise ValueError(f"'{t}' needs a file after it")
            if t in (">", "1>"):
                red["out"] = (target, "w")
            elif t in (">>", "1>>"):
                red["out"] = (target, "a")
            elif t == "2>":
                red["err"] = (target, "w")
            elif t == "2>>":
                red["err"] = (target, "a")
            elif t == "<":
                red["in"] = target
        else:
            args.append(t)
    return args, red


_PCT = re.compile(r"%([^%\s=]+)%")
_PSENV = re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_()]*)", re.I)


def dynamic_var(name: str, last_rc: int):
    n = name.upper()
    if n == "CD":
        return os.getcwd()
    if n == "ERRORLEVEL":
        return str(last_rc)
    if n == "RANDOM":
        return str(random.randint(0, 32767))
    if n == "DATE":
        return time.strftime("%a %m/%d/%Y")
    if n == "TIME":
        t = time.time()
        return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 100) % 100:02d}"
    return None


def getenv_ci(name: str):
    if name in os.environ:
        return os.environ[name]
    up = name.upper()
    for k, v in os.environ.items():
        if k.upper() == up:
            return v
    return None


def expand(text: str, last_rc: int = 0) -> str:
    """%NAME% and $env:NAME. Unknown names are left as typed, like CMD."""
    def pct(m):
        name = m.group(1)
        if ":" in name:  # %PATH:a=b% substitution and %VAR:~0,3% substring
            base, _, op = name.partition(":")
            val = getenv_ci(base) or dynamic_var(base, last_rc)
            if val is None:
                return m.group(0)
            if op.startswith("~"):
                try:
                    nums = [int(x) for x in op[1:].split(",")]
                    s = nums[0]
                    val = val[s:] if len(nums) == 1 else (val[s:s + nums[1]] if nums[1] >= 0 else val[s:nums[1]])
                    return val
                except ValueError:
                    return m.group(0)
            if "=" in op:
                a, _, b = op.partition("=")
                return re.sub(re.escape(a), lambda _m: b, val, flags=re.I) if a else val
            return m.group(0)
        val = getenv_ci(name)
        if val is None:
            val = dynamic_var(name, last_rc)
        return m.group(0) if val is None else val

    text = re.sub(r"%([^%\s]+)%", pct, text)
    return _PSENV.sub(lambda m: getenv_ci(m.group(1)) or "", text)


def quote(arg: str) -> str:
    if arg and not re.search(r'[\s&|<>^"()]', arg):
        return arg
    return '"' + arg.replace('"', '""') + '"'


def join(args) -> str:
    return " ".join(quote(a) for a in args)


# ------------------------------------------------------- for highlighting
def segments_for_highlight(line: str):
    """Yields (start, end, kind) where kind is 'cmd', 'arg', 'string', 'op', 'flag', 'var'."""
    out = []
    i, n = 0, len(line)
    expect_cmd = True
    while i < n:
        ch = line[i]
        if ch in " \t":
            i += 1
            continue
        if line.startswith(("&&", "||"), i):
            out.append((i, i + 2, "op"))
            i += 2
            expect_cmd = True
            continue
        if ch in "&|":
            out.append((i, i + 1, "op"))
            i += 1
            expect_cmd = True
            continue
        m = _REDIR.match(line, i)
        if m and (ch in "<>" or line[i + 1:i + 2] == ">"):
            out.append((i, m.end(), "op"))
            i = m.end()
            continue
        # a word
        j, q = i, False
        while j < n:
            c = line[j]
            if c == '"':
                q = not q
            elif not q and (c in " \t&|<>"):
                break
            elif c == "^" and not q:
                j += 1
            j += 1
        j = min(j, n)
        word = line[i:j]
        if expect_cmd:
            out.append((i, j, "cmd"))
            expect_cmd = False
        elif word.startswith('"'):
            out.append((i, j, "string"))
        elif re.match(r"^(--?|/)[A-Za-z?]", word) and not (word.startswith("/") and "/" in word[1:]):
            out.append((i, j, "flag"))
        else:
            out.append((i, j, "arg"))
        i = j
    return out
