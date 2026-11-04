"""The input line: live colors, suggestions from history, Tab menus, F7 history."""
import os
import sys

from . import term, ui

IS_WIN = term.IS_WIN
WORD_BREAK = ' \t&|<>;"=' + ("\\" if IS_WIN else "/")


class LineEditor:
    def __init__(self, history, highlight=None, complete=None, suggest=None):
        self.history = history          # list of str, oldest first (shared with the shell)
        self.highlight = highlight or (lambda s: s)
        self.complete = complete
        self.suggest = suggest
        self.insert_mode = True

    # ------------------------------------------------------------ drawing
    def _render(self, prompt, pw, buf, cur, ghost):
        cols, _ = term.size()
        out = ["\r"]
        if self._row:
            out.append(f"\x1b[{self._row}A")
        out.append("\x1b[J")
        text = "".join(buf)
        out.append(prompt)
        out.append(self.highlight(text) if text else "")
        if ghost:
            out.append(ui.paint(ghost, "dim") if ui.color_on(sys.__stdout__) else "")
        out.append(ui.RESET if ui.color_on(sys.__stdout__) else "")
        total = pw + term.text_width(text) + (term.text_width(ghost) if ghost else 0)
        if total and total % cols == 0:
            out.append(" \r\x1b[K")
        end_row = total // cols
        pos = pw + term.text_width("".join(buf[:cur]))
        row, col = pos // cols, pos % cols
        if end_row > row:
            out.append(f"\x1b[{end_row - row}A")
        out.append("\r")
        if col:
            out.append(f"\x1b[{col}C")
        self._row = row
        term.write("".join(out))

    def _finish(self, prompt, pw, buf):
        """Leaves the line drawn without the suggestion and moves below it."""
        self._render(prompt, pw, buf, len(buf), "")
        term.write("\n")
        self._row = 0

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _word_left(buf, cur):
        i = cur
        while i > 0 and buf[i - 1] in WORD_BREAK:
            i -= 1
        while i > 0 and buf[i - 1] not in WORD_BREAK:
            i -= 1
        return i

    @staticmethod
    def _word_right(buf, cur):
        n = len(buf)
        i = cur
        while i < n and buf[i] in WORD_BREAK:
            i += 1
        while i < n and buf[i] not in WORD_BREAK:
            i += 1
        return i

    def _ghost(self, buf, cur):
        if not self.suggest or cur != len(buf) or not buf:
            return ""
        try:
            return self.suggest("".join(buf)) or ""
        except Exception:
            return ""

    # ------------------------------------------------------------ main
    def read(self, prompt: str):
        """Returns the line, or None for end of input (Ctrl+D / Ctrl+Z on an empty line)."""
        pw = ui.vwidth(prompt)
        buf, cur = [], 0
        self._row = 0
        hist_i = len(self.history)
        hist_prefix = None
        saved = ""
        last_tab = False
        with term.raw_mode():
            self._render(prompt, pw, buf, cur, "")
            while True:
                k = term.read_key()
                tab_now = False
                if k is None:
                    continue
                if k == "enter":
                    self._finish(prompt, pw, buf)
                    return "".join(buf)
                if k == "ctrl-c":
                    self._render(prompt, pw, buf, len(buf), "")
                    term.write(ui.paint("^C", "dim") + "\n")
                    self._row = 0
                    return ""
                if k in ("ctrl-d", "ctrl-z") and not buf:
                    self._finish(prompt, pw, buf)
                    return None
                if k == "esc":
                    buf, cur = [], 0
                    hist_i, hist_prefix = len(self.history), None
                elif k == "ctrl-l":
                    term.write("\x1b[2J\x1b[3J\x1b[H")
                    self._row = 0
                elif k == "left":
                    cur = max(0, cur - 1)
                elif k == "right":
                    if cur == len(buf):
                        g = self._ghost(buf, cur)
                        if g:
                            buf.extend(g)
                            cur = len(buf)
                    else:
                        cur += 1
                elif k == "ctrl-left":
                    cur = self._word_left(buf, cur)
                elif k == "ctrl-right":
                    if cur == len(buf):
                        g = self._ghost(buf, cur)
                        if g:  # accept one word of the suggestion
                            j = self._word_right(list(g), 0)
                            buf.extend(g[:j])
                            cur = len(buf)
                    else:
                        cur = self._word_right(buf, cur)
                elif k == "home":
                    cur = 0
                elif k == "end":
                    if cur == len(buf):
                        g = self._ghost(buf, cur)
                        buf.extend(g)
                    cur = len(buf)
                elif k == "backspace":
                    if cur > 0:
                        del buf[cur - 1]
                        cur -= 1
                elif k == "delete":
                    if cur < len(buf):
                        del buf[cur]
                elif k in ("ctrl-backspace", "ctrl-w"):
                    j = self._word_left(buf, cur)
                    del buf[j:cur]
                    cur = j
                elif k == "ctrl-delete":
                    j = self._word_right(buf, cur)
                    del buf[cur:j]
                elif k == "ctrl-u":
                    del buf[:cur]
                    cur = 0
                elif k == "ctrl-k":
                    del buf[cur:]
                elif k == "insert":
                    self.insert_mode = not self.insert_mode
                elif k in ("up", "down"):
                    if hist_prefix is None:
                        hist_prefix = "".join(buf)
                        saved = hist_prefix
                    step = -1 if k == "up" else 1
                    i = hist_i + step
                    while 0 <= i < len(self.history):
                        h = self.history[i]
                        if h.lower().startswith(hist_prefix.lower()) and h != "".join(buf):
                            break
                        i += step
                    if 0 <= i < len(self.history):
                        hist_i = i
                        buf = list(self.history[i])
                    elif k == "down":
                        hist_i = len(self.history)
                        buf = list(saved)
                    cur = len(buf)
                elif k in ("f7", "ctrl-r", "pgup"):
                    seen, items = set(), []
                    for h in reversed(self.history):
                        if h not in seen:
                            seen.add(h)
                            items.append(h)
                    if items:
                        self._render(prompt, pw, buf, len(buf), "")
                        term.write("\n")
                        self._row = 0
                        pick = ui.choose(items, ui.b("History"), query="".join(buf) if k == "ctrl-r" else "")
                        term.write("\x1b[1A")
                        if pick is not None:
                            buf = list(pick)
                            cur = len(buf)
                elif k == "f3":  # CMD: bring back the last command
                    if self.history:
                        buf = list(self.history[-1])
                        cur = len(buf)
                elif k in ("tab", "shift-tab") and self.complete:
                    tab_now = True
                    buf, cur = self._tab(prompt, pw, buf, cur, last_tab)
                elif isinstance(k, str) and len(k) == 1 and (k.isprintable() or k == "\t"):
                    chunk = [k]
                    while term.key_ready():  # pasted text: take it all before redrawing
                        k2 = term.read_key()
                        if isinstance(k2, str) and len(k2) == 1 and k2.isprintable():
                            chunk.append(k2)
                        elif k2 == "enter":
                            buf[cur:cur] = chunk
                            self._finish(prompt, pw, buf)
                            return "".join(buf)
                        else:
                            break
                    if not self.insert_mode:
                        del buf[cur:cur + len(chunk)]
                    buf[cur:cur] = chunk
                    cur += len(chunk)
                    hist_prefix = None
                    hist_i = len(self.history)
                last_tab = tab_now
                self._render(prompt, pw, buf, cur, self._ghost(buf, cur))

    def _tab(self, prompt, pw, buf, cur, second):
        text = "".join(buf)
        try:
            start, cands = self.complete(text, cur)
        except Exception:
            return buf, cur
        if not cands:
            term.write("\a")
            return buf, cur
        word = text[start:cur]

        def put(insert):
            new = text[:start] + insert + text[cur:]
            return list(new), start + len(insert)

        if len(cands) == 1:
            ins = cands[0][1]
            if not ins.endswith(("\\", "/", '\\"', '/"')):
                ins += " "
            return put(ins)
        inserts = [c[1] for c in cands]
        pre = os.path.commonprefix([i.lower() for i in inserts]) if IS_WIN else os.path.commonprefix(inserts)
        if len(pre) > len(word.strip('"')) + (1 if word.startswith('"') else 0) and not second:
            return put(inserts[0][:len(pre)])
        # show the choices; a second Tab opens a menu
        self._render(prompt, pw, buf, len(buf), "")
        term.write("\n")
        self._row = 0
        if second:
            pick = ui.choose([c[0] for c in cands], ui.b("Complete"))
            term.write("\x1b[1A")
            if pick is not None:
                ins = next(c[1] for c in cands if c[0] == pick)
                if not ins.endswith(("\\", "/")):
                    ins += " "
                return put(ins)
            return buf, cur
        cols, rows = term.size()
        names = [c[0] for c in cands]
        w = min(max(ui.vwidth(n) for n in names) + 2, cols)
        per = max(1, cols // w)
        shown = names[:per * max(1, rows - 4)]
        lines = []
        for i in range(0, len(shown), per):
            lines.append("".join(ui.pad(n, w) for n in shown[i:i + per]).rstrip())
        if len(shown) < len(names):
            lines.append(ui.dim(f"… and {len(names) - len(shown)} more (Tab again for a menu)"))
        else:
            lines.append(ui.dim("Tab again to pick from a menu"))
        term.write("\n".join(lines) + "\n")
        return buf, cur
