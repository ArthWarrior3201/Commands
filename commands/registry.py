"""The table of built-in commands and small helpers they share."""
import os
import glob

BUILTINS = {}   # name -> Builtin (aliases point to the same object)


class Builtin:
    def __init__(self, names, func, summary, usage, cat, complete, hidden):
        self.name = names[0]
        self.names = names
        self.func = func
        self.summary = summary
        self.usage = usage
        self.cat = cat
        self.complete = complete
        self.hidden = hidden


def builtin(*names, summary="", usage="", cat="Shell", complete=None, hidden=False):
    def deco(func):
        b = Builtin(list(names), func, summary, usage, cat, complete, hidden)
        for n in names:
            BUILTINS[n.lower()] = b
        return func
    return deco


class Usage(Exception):
    """Raised by a built-in when it was used the wrong way; the shell prints its usage."""


class Ctx:
    """What a built-in gets: the shell, its words, and piped-in text (or None)."""

    def __init__(self, sh, name, args, stdin=None, raw=None):
        self.raw = raw  # the words after the command name, exactly as typed (after %VAR% expansion)
        self.sh = sh
        self.name = name
        self.args = args
        self.stdin = stdin

    def opts(self, spec: dict, args=None):
        """spec: {"n|lines": int, "a|all": bool, "o|out": str}. Accepts -n 5, --lines=5, /a."""
        args = list(self.args if args is None else args)
        names = {}
        for key, typ in spec.items():
            for alias in key.split("|"):
                names[alias] = (key.split("|")[-1], typ)
        out = {k.split("|")[-1]: (False if t is bool else None) for k, t in spec.items()}
        rest = []
        it = iter(args)
        for a in it:
            if a == "--":
                rest.extend(it)
                break
            name, val = None, None
            if a.startswith("--") and len(a) > 2:
                name, _, val = a[2:].partition("=")
                val = val if "=" in a else None
            elif (a.startswith("-") or a.startswith("/")) and len(a) >= 2 and not a[1:].lstrip("-").replace(".", "").isdigit():
                name = a[1:]
                if ":" in name and a.startswith("/"):
                    name, _, val = name.partition(":")
                    val = val or None
            if name is not None and (name in names or name.lower() in names):
                key, typ = names.get(name) or names[name.lower()]
                if typ is bool:
                    out[key] = True
                else:
                    if val is None:
                        val = next(it, None)
                        if val is None:
                            raise Usage(f"{a} needs a value")
                    try:
                        out[key] = typ(val)
                    except ValueError:
                        raise Usage(f"{a} expects a {typ.__name__}")
                continue
            if name is not None and a.startswith("-") and all(ch in names for ch in name) and len(name) > 1:
                ok = True  # combined short flags: -la
                for ch in name:
                    if names[ch][1] is not bool:
                        ok = False
                if ok:
                    for ch in name:
                        out[names[ch][0]] = True
                    continue
            rest.append(a)
        return out, rest

    def text_in(self, files):
        """Piped text if there is some, otherwise the files' contents."""
        if self.stdin is not None and not files:
            return self.stdin
        parts = []
        for f in expand_globs(files):
            with open(f, encoding="utf-8", errors="replace") as fh:
                parts.append(fh.read())
        return "".join(parts)


def expand_globs(args):
    out = []
    for a in args:
        if any(ch in a for ch in "*?["):
            hits = sorted(glob.glob(a))
            out.extend(hits or [a])
        else:
            out.append(a)
    return out


def resolve(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
