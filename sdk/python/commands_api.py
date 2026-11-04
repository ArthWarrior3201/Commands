"""Python client for the Commands API. Copy this one file into your app.

    from commands_api import Commands
    cx = Commands()
    if cx.available:
        pick = cx.choose(["Build", "Test", "Deploy"], title="What now?")
        cx.cd("C:/Projects/site")          # changes the folder of the user's Commands window
        cx.setenv("SITE_MODE", "debug")    # ... and its variables
        for i in range(101):
            cx.progress("Uploading", i, 100)
        cx.progress("Uploading", done=True)

Everything is a method call; see docs/API.md or `commands api` for the list.
"""
import json
import os
import socket
import threading

__all__ = ["Commands", "CommandsError"]


class CommandsError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Commands:
    def __init__(self, address=None, token=None):
        self.address = address or os.environ.get("COMMANDS_API")
        self.token = token or os.environ.get("COMMANDS_API_TOKEN")
        self._sock = None
        self._file = None
        self._id = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True when this program was started inside a Commands window."""
        if not self.address or not self.token:
            return False
        try:
            self._connect()
            return True
        except OSError:
            return False

    def _connect(self):
        if self._sock is None:
            host, _, port = self.address.rpartition(":")
            self._sock = socket.create_connection((host, int(port)), timeout=5)
            self._sock.settimeout(None)
            self._file = self._sock.makefile("rwb")

    def call(self, method, **params):
        with self._lock:
            self._connect()
            self._id += 1
            msg = {"token": self.token, "id": self._id, "method": method, "params": params}
            self._file.write((json.dumps(msg) + "\n").encode("utf-8"))
            self._file.flush()
            line = self._file.readline()
        if not line:
            raise CommandsError("closed", "the Commands window went away")
        resp = json.loads(line.decode("utf-8"))
        if "error" in resp:
            raise CommandsError(resp["error"].get("code"), resp["error"].get("message"))
        return resp.get("result")

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- the window
    def info(self): return self.call("session.info")
    def cd(self, path): return self.call("session.cd", path=path)
    def getenv(self, name): return self.call("session.getenv", name=name)
    def setenv(self, name, value): return self.call("session.setenv", name=name, value=value)
    def alias(self, name, command): return self.call("session.alias", name=name, command=command)
    def title(self, title): return self.call("session.title", title=title)
    def run_after(self, line): return self.call("session.run", line=line)
    def run(self, line, capture=False): return self.call("shell.run", line=line, capture=capture)

    # ---- talking to the user
    def print(self, text, markup=True): return self.call("ui.print", text=text, markup=markup)
    def confirm(self, question, default=False): return self.call("ui.confirm", question=question, default=default)
    def ask(self, question, default="", secret=False):
        return self.call("ui.ask", question=question, default=default, secret=secret)
    def choose(self, items, title="", multi=False): return self.call("ui.choose", items=items, title=title, multi=multi)
    def progress(self, label, value=0, total=None, done=False):
        return self.call("ui.progress", label=label, value=value, total=total, done=done)
    def table(self, rows, header=None): return self.call("ui.table", rows=rows, header=header)
    def notify(self, title, message=""): return self.call("ui.notify", title=title, message=message)

    # ---- system
    def path_add(self, folder, scope="user", front=False):
        return self.call("path.add", folder=folder, scope=scope, front=front)
    def path_remove(self, folder, scope="user"): return self.call("path.remove", folder=folder, scope=scope)
    def env_set(self, name, value, scope="user"): return self.call("env.set", name=name, value=value, scope=scope)
    def clipboard(self, text=None):
        return self.call("clipboard.get") if text is None else self.call("clipboard.set", text=text)
    def trash(self, *paths): return self.call("fs.trash", paths=list(paths))
    def odus(self, *args): return self.call("odus.run", args=list(args))
    def register(self, name, run, help=""): return self.call("commands.register", name=name, run=run, help=help)
    def store_get(self, app, key): return self.call("store.get", app=app, key=key)
    def store_set(self, app, key, value): return self.call("store.set", app=app, key=key, value=value)
