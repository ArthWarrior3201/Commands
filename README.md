# Commands

A command prompt with the CMD feel - black screen, a path and a prompt, every
CMD and PowerShell command you know - but its own look and a lot more inside.

```
▌Commands [Version 1.0.0]

C:\Users\Arth\Projects (main) › install vlc && ports
```

Works on Windows, macOS and Linux. The downloads need nothing else installed.

## Download

Get it from the [releases page](https://github.com/ArthWarrior3201/Commands/releases/latest).
The Windows, macOS and Linux downloads are **standalone**: Python is packed
inside, so there's nothing else to install.

- **Windows:** `commands.bat` - double-click it or run it in Command Prompt.
- **macOS:** `commands-macos.command` - in Terminal run
  `chmod +x commands-macos.command` once, then double-click it (or run
  `sh commands-macos.command`). Works on Intel and Apple Silicon Macs. It isn't
  signed by Apple, so the first time right-click it and choose Open.
  On **macOS Tahoe 26 Developer Beta or newer** right-click → Open no longer
  gets past the warning; run this instead, then double-click it:
  `xattr -d com.apple.quarantine ~/Downloads/commands-macos.command`
- **Linux:** `commands-linux.sh` - `sh commands-linux.sh` (or `chmod +x` it and
  run `./commands-linux.sh`). x86_64 and ARM64.
- **Python:** `commands-python.zip` - the source files for any system that has
  **Python 3.9 or newer**. Unzip it and run `python run.py`.

The first start unpacks Commands once (about 11 MB on Windows, 60-100 MB on
macOS/Linux) into `%LOCALAPPDATA%\Commands\runtime` or
`~/.local/share/commands/runtime`; after that it opens straight away.

This repository is the raw source code. To run it straight from here:
`python run.py`.

Running from source needs **Python 3.9 or newer**. If you're working on the
source you most likely have it already; if not, get it from
[python.org](https://www.python.org/downloads/) and install it first.

Then type `setup` once to add Commands to PATH, the Start menu, Windows
Terminal (with its own color scheme and icon) and Explorer's right-click menu.
You choose which. `setup --remove` undoes it.

## What it looks like

- **Live colors while you type**: built-ins, programs, Odus words, PowerShell
  cmdlets and unknown commands each get their own color, so a typo is red
  before you press Enter. Strings, flags, `&&`/`|` and `%VARS%` are colored too.
- **Suggestions from history** in grey: `→` or `End` takes it, `Ctrl+→` takes one word.
- **Tab** completes commands, folders, files and sub-commands; Tab twice opens a menu.
- **F7** (like CMD) or **Ctrl+R**: history menu with type-to-filter. **F3** brings back the last command.
- The prompt shows the folder, git branch, the last error code, slow-command time and `admin`.
- Themes: `theme` (menu) - `commands`, `classic`, `phosphor`, `amber`, `ice`.
  `prompt $P$G` gives the old CMD prompt, `color 0A` still works.

## Who runs your command

You just type. Commands picks:

1. **Commands built-ins** - `cd`, `set`, `path`, `env`, `ls`, `ports`, ... (`help` lists them)
2. **Commands apps** - your own commands that get the Commands API
3. **Odus** for app/media/download words: `install`, `uninstall`, `update`, `search`,
   `compress`, `compress-app`, `download`, `clone`, presets `fast` / `fastplus` / `ufa`
4. **Scripts** by type - `.bat`/`.cmd` (their variables and folder changes stay, like
   in CMD), `.ps1`, `.py`, `.sh`
5. **PowerShell** for cmdlets and PowerShell syntax: `Get-Process`, `$x = 5`,
   `... | Where-Object`. `$env:X = 1` and `Set-Location` stick in the window.
6. **CMD** for everything else: `dir`, `copy`, `winget`, `git`, any program

Force one with a prefix: `ps: …`, `cmd: …`, `sh: …`, `odus: …`. A word Commands
doesn't know but Odus understands (`fast update all my apps`) goes to Odus.
Pipes work between all of them: `dir /b | grep -i log | count`.

Odus is found in `%COMMANDS_ODUS%`, `~/Odus/odus.py`, or on PATH. `odus get`
downloads it if it's missing.

**First start:** if Odus isn't on your PATH yet, Commands asks once whether to
add it. Yes: its folder goes straight into the registry (never `setx`), with a
backup first, so `odus` works in Commands, CMD and everywhere. No: the answer
is remembered, Commands won't run Odus, and you use Odus by running its file
(`odus.cmd` / `odus.sh`). Change your mind with `odus-path add` / `odus-path remove`.

## PATH and variables, done right

```
path                              this window's PATH, numbered, problems marked
path add C:\Tools                 saved for you + this window, at the end
path add C:\Tools --front         ... at the start
path add C:\Tools --machine       for everyone (sudo path add ... from a normal window)
path remove C:\Tools | path remove 12
path clean                        drop missing and duplicate folders (asks first)
path doctor                       missing, duplicates, programs hiding other programs
path find python                  every python on PATH, which one wins
path backups / path restore       every change is backed up first
env set JAVA_HOME "C:\Java\jdk"   setx without the 1024 limit
env diff / env refresh            what differs from a new window / load installer changes
```

- No 1024-character limit (setx's). The registry value keeps its type
  (`%USERPROFILE%` keeps working), and other programs are told about the change.
- **Never wiped**: Commands edits single entries, refuses to write an empty
  PATH or drop entries you didn't ask it to, and saves a backup before every write.
- `setx PATH "..."` inside Commands adds the missing folders instead of replacing.
- **Live refresh**: when an installer adds to PATH, this window picks it up right after.
- macOS/Linux: a managed block in `~/.profile`, `~/.bashrc`, `~/.zshrc` and fish's
  `conf.d`; `--machine` uses `/etc/paths.d` (macOS) or `/etc/profile.d` (Linux).

## Built-ins

| | |
|---|---|
| **Folders** | `cd -` (back), `j proj` (jump to a folder you've been to), `mark name`, `up 2`, `mkcd`, `pushd`/`popd` |
| **Files** | `ls` (colors, `-l`, `--tree`), `cat -n`, `du` (sizes with bars), `bigfiles`, `dupes`, `hash`, `trash` (Recycle Bin), `zip`, `extract`, `rename-all` (with preview), `open`, `touch` |
| **Text & data** | `grep`, `head`, `tail -f`, `count`, `json` (colors + paths like `users.0.name`), `calc`, `base64`, `uuid`, `password`, `clip`/`paste`, `choose`, `confirm` |
| **System** | `sysinfo`, `procs`, `kill chrome`, `sudo` (admin window), `notify`, `remind 10m tea`, `bg`/`jobs` |
| **Network** | `ports`, `killport 3000`, `serve` (share a folder), `http POST api.test name=Ann`, `ip --public` |
| **Shell** | `alias`, `history`, `!`, `timeit`, `watch -n 2`, `repeat`, `which`, `source`, `call`, `config`, `theme` |

`help <command>` or `<command> /?` explains any of them. On macOS/Linux the
system's own `ls`, `grep`, `kill`, ... win over Commands' versions.

## Commands API

Programs started from Commands can change the window's folder and variables,
show menus, progress bars and password prompts, change PATH for good, register
new commands, and more - things a `.cmd` or `.ps1` can't do by itself.

```bat
commands api session.cd path=C:\Projects
commands api ui.choose items="[\"Debug\",\"Release\"]" --raw
```

Python and Node clients are in `sdk/`. `app new hello` makes a working app.
Full reference: [docs/API.md](docs/API.md).

## Command line

```
commands                       open the prompt
commands path add C:\Tools     run one command and exit (works from plain CMD/PowerShell)
commands -c "line" | -k "line" run a line (and stay open with -k)
commands script.cmds           run a file of Commands lines
commands api <method> k=v      call the API
--cd <folder>  --plain  --pause  --version
```

Settings, history, backups and apps live in `%APPDATA%\Commands`
(`~/.config/commands` on macOS/Linux).

## Making a release

1. Set `VERSION` in `commands/__init__.py`.
2. Run `python build.py`. It writes the four downloads into `dist/`:
   `commands.bat`, `commands-macos.command`, `commands-linux.sh` (each with
   Python packed inside) and `commands-python.zip` (the source files).
   The packed Pythons come from python.org (Windows) and
   [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
   (macOS, Linux); they're downloaded once into `build-cache/` and checked.
3. On GitHub, create a release tagged `v<VERSION>` (for example `v1.0.0`) and
   upload all four files.

## Tests

```bat
python -m unittest discover tests
```

PATH tests use a fake registry; your real PATH is never touched.
