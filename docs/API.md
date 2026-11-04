# The Commands API

Programs started from a Commands window can talk back to that window. This
lets an app do things a `.cmd` or `.ps1` file can't do on its own:

- change the **folder** and **variables of the window that started it** (not just its own copy)
- show **arrow-key menus**, yes/no questions, hidden password prompts, **progress bars** and tables in the user's window
- add **aliases** and **register new commands** that stay after restarts
- queue commands to run **after the app exits**
- change **PATH and variables for good** with no 1024-character limit, with a backup first and wipe protection
- clipboard (Unicode-safe), Recycle Bin, desktop notifications, processes, ports, system info, Odus
- a small per-app key/value **store** that survives restarts

## How it works

Each Commands window starts a server on `127.0.0.1` with a random token and
gives every program it starts two variables:

| Variable | Example |
|---|---|
| `COMMANDS_API` | `127.0.0.1:52144` |
| `COMMANDS_API_TOKEN` | `9f2c…` (secret, different every window) |

Send one JSON object per line over TCP, get one JSON line back:

```json
→ {"token": "9f2c…", "id": 1, "method": "session.cd", "params": {"path": "C:\\Projects"}}
← {"id": 1, "result": {"cwd": "C:\\Projects"}}
← {"id": 1, "error": {"code": "not_found", "message": "C:\\Nope isn't a folder"}}
```

Error codes: `bad_token`, `bad_request`, `bad_params`, `unknown_method`,
`needs_session`, `needs_admin`, `not_found`, `refused`, `failed`.

## Using it

**From any script (.cmd, .ps1, .sh, anything)**, with the `commands` command:

```bat
commands api session.cd path=C:\Projects
commands api ui.choose items="[\"Debug\",\"Release\"]" title="Build type" --raw
commands api path.add folder=C:\Tools scope=user
```

Values are read as JSON when they can be (`true`, `5`, `["a","b"]`), otherwise
as text. `--raw` prints text results without quotes. Exit code is 0 on success;
errors go to stderr as JSON.

```bat
@echo off
rem pick.cmd - a batch file that shows a real menu and moves the window
commands api ui.choose items="[\"C:\Projects\",\"C:\Tools\"]" title="Go to" --raw > "%TEMP%\pick.txt"
set /p PICK=<"%TEMP%\pick.txt"
commands api session.cd path="%PICK%"
```

(Backslashes in Windows paths can be written as-is inside JSON lists.)

**Python** - copy [`sdk/python/commands_api.py`](../sdk/python/commands_api.py):

```python
from commands_api import Commands
cx = Commands()
if cx.available:
    env = cx.choose(["dev", "staging", "prod"], title="Deploy to")
    if env and cx.confirm(f"Deploy to {env}?"):
        for i in range(101):
            cx.progress("Deploying", i, 100)
        cx.progress("Deploying", done=True)
        cx.setenv("LAST_DEPLOY", env)       # visible in the window afterwards
        cx.run_after("git log -1 --oneline")
```

**Node.js** - copy [`sdk/node/commands-api.js`](../sdk/node/commands-api.js):

```js
const { Commands } = require("./commands-api");
const cx = new Commands();
const pick = await cx.choose(["a", "b"], { title: "Pick" });
await cx.cd("C:/Projects");
cx.close();
```

**Any other language**: open a TCP socket, write the JSON line, read a line back.

Without a Commands window (`commands api ...` from plain CMD or PowerShell) the
methods marked *window* return `needs_session`; the rest still work.

## Commands apps

Put a `.py`, `.js`, `.ps1`, `.cmd`, `.bat`, `.sh` or `.exe` into the apps folder
(`%APPDATA%\Commands\apps`, or `~/.config/commands/apps`) and its name becomes a
command. Or a folder with `main.py` / `index.js`. `app new hello` makes a
working example; `app list`, `app remove`, `app folder` manage them.
An app can also add a command that runs any line with `commands.register`.

## Methods

Run `commands api` for the live list. *window* = needs to be started from a Commands window.

| Method | Params | What it does |
|---|---|---|
| `api.version` | | API, Commands and Odus versions |
| `api.methods` | | every method |
| `session.info` *window* | | folder, admin, last exit code, aliases, jobs |
| `session.cd` *window* | `path` | change the window's folder |
| `session.getenv` *window* | `name` | read a window variable |
| `session.setenv` *window* | `name, value` | set one (`null` removes) |
| `session.alias` *window* | `name, command` | add/remove an alias (saved) |
| `session.title` *window* | `title` | window title |
| `session.run` *window* | `line` | run a line after your app exits |
| `session.history` *window* | `limit` | recent commands |
| `shell.run` | `line, capture` | run a line through Commands; `capture` returns output |
| `ui.print` | `text, markup` | print; markup `[ok] [err] [warn] [dim] [b] [accent] [path] … [/]` |
| `ui.confirm` | `question, default` | yes/no → `true`/`false` |
| `ui.ask` | `question, default, secret` | a line of text, `null` if cancelled |
| `ui.choose` | `items, title, multi` | arrow-key menu with filtering |
| `ui.progress` | `label, value, total, done` | one-line progress bar |
| `ui.table` | `rows, header` | aligned columns |
| `ui.notify` | `title, message` | desktop notification |
| `path.get` | `scope` | PATH entries (`user`, `machine`, `session`) |
| `path.add` | `folder, scope, front` | add for good: no length limit, backup, window updated |
| `path.remove` | `folder, scope` | remove a folder or number |
| `path.clean` | `scope, dry` | drop missing and duplicate folders |
| `path.which` | `name` | every copy of a program on PATH |
| `env.get` | `name, scope` | read a variable |
| `env.set` | `name, value, scope` | save a variable for good (`null` removes) |
| `env.refresh` *window* | | load installer changes into the window |
| `clipboard.get` / `clipboard.set` | `text` | clipboard text |
| `fs.trash` | `paths` | Recycle Bin / Trash |
| `sys.info` | | OS, CPU, memory, uptime, disks, battery |
| `sys.processes` | `filter` | running processes |
| `sys.ports` | | listening ports and their programs |
| `sys.kill` | `pid` | stop a process tree |
| `odus.run` | `args` | run Odus in the user's window (refused if the user kept Odus off PATH) |
| `odus.status` | | where Odus is, version, on PATH or not |
| `sys.open` | `target` | open a file, folder or link with its usual app |
| `sys.elevate` | `line, pause` | run a line as administrator in a new admin window (UAC) |
| `startup.list` / `startup.add` / `startup.remove` | `name, command` | run something every time you sign in |
| `shortcut.create` | `path, target, args, icon, workdir` | make a shortcut (.lnk / .desktop) |
| `net.download` | `url, dest, progress` | download with a progress bar in the window |
| `net.free_port` | `start` | a free TCP port |
| `fs.hash` | `path, algo` | sha256 / md5 / sha1 / sha512 |
| `fs.size` | `path` | size of a file or a whole folder |
| `fs.watch` | `path, timeout` | wait until something changes; says what was added/removed/modified |
| `jobs.start` / `jobs.list` *window* | `line` | background jobs in the window |
| `commands.register` / `commands.unregister` | `name, run, help` | add/remove a command |
| `store.get` / `store.set` | `app, key, value` | small saved values per app |

## Safety

The server only listens on `127.0.0.1` and refuses any request without the
window's token, which only programs started from that window receive. Machine
PATH and machine variables still need an administrator window (`sudo ...`).
