# The review page as a local service

The review page (`http://127.0.0.1:8765/`) is meant to always be there, for every project on the
machine that uses pbrain, without a process manager and without editing a list by hand. Three
pieces make that work: a per-user **registry** of projects, a background **daemon** that serves
the page, and an **autostart** entry that brings the daemon back at login.

Everything lives in `~/.pbrain/` (move it with `PBRAIN_HOME`):

| File | What |
|---|---|
| `projects.json` | the registry: projects shown on the page and scanned by `pbrain scan --all` |
| `serve.lock` | held (OS lock) by the running daemon; it is how a second one knows to exit |
| `serve.json` | pid, version, port and status (`waiting`/`serving`) of the daemon holding the lock |
| `serve.log` | the daemon's output (it has no console); rotated past 1 MB |

## Install on a new machine

```
uv tool install git+https://github.com/nhonhoa/projectbrain   # or pip install ...
pbrain setup                                                  # once per machine
cd some-project && pbrain init                                # per project
```

`pbrain setup` installs the autostart, registers the current project if it has a `pbrain.toml`,
starts the daemon and waits until the page answers. pip has no post-install hook, which is why
this is a command. Without `setup` the page still comes up: every pbrain command (except `serve`,
`setup`, `review` and `hook`) ends with a cheap check (one lock attempt, no network) that starts
the daemon if none is alive, and the session-start hook does the same at the start of each agent
session. It does not
come back after a reboot, though, until something runs pbrain again.

Undo: `pbrain setup --uninstall` removes the autostart entry and stops the daemon.
`pbrain serve --status` shows what serves the port, whether the daemon is alive, the autostart
entry and the registry.

## Registry

`~/.pbrain/projects.json`:

```json
{"version": 1, "projects": [
  {"root": "Z:\\projects\\bank-hub", "name": "bank-hub",
   "added": "2026-11-27T10:00:00+00:00", "last_seen": "2026-11-27T10:00:00+00:00"}
]}
```

- **Written by** `pbrain init` (always, once the project has a `pbrain.toml`), the session-start
  hook (adds the project if missing, refreshes `last_seen` at most once a day; never fails the
  hook), `pbrain setup`, `pbrain review` when it hands over to a running page, and
  `pbrain projects add|remove|prune`. `pbrain check` never writes it, so CI stays read-only.
- **Read by** the review server on each project list request and `pbrain scan --all`.
- Writes take a short lock file (`projects.lock`) and replace the file atomically.
- Roots are compared resolved and case-folded where the file system is (Windows).
- A project that loses its `pbrain.toml` (deleted, moved) stays listed: the page shows it struck
  out and refuses to open it, `pbrain projects list` marks it `missing`, `pbrain scan --all` skips
  it with a warning. `pbrain projects prune` drops all such entries; `remove ROOT` drops one.

```
pbrain projects                 # list (add --json for a machine-readable form)
pbrain projects add [ROOT ...]  # default: the current project
pbrain projects remove [ROOT ...]
pbrain projects prune
```

## The server

`pbrain serve` runs the page for the registry plus any `ROOT ...` / `--roots-file` given, and the
current project when it has a `pbrain.toml` (`--no-current` to leave it out).

- **Live project list.** The registry is read again on every `/api/projects` request (and when a
  page asks for an index it does not know yet), and a project's `pbrain.toml` is reloaded when its
  modification time changes. A project registered while the page is open appears when the browser
  tab regains focus. Project slots are append-only for the life of the server, so the indexes the
  page holds keep naming the same project; a project taken out of the registry is hidden, not
  renumbered.
- **One server per port.** On Windows Python's default `SO_REUSEADDR` lets a second server bind a
  port that is already in use; the review server binds with `SO_EXCLUSIVEADDRUSE` there instead.
- **Standby.** `--standby` (always on for `--daemon`) means: if the port is taken, log who holds
  it and try again every 10 s (`PBRAIN_STANDBY_POLL`), taking over as soon as it is free.
- **Daemon.** `pbrain serve --daemon` is what autostart and the automatic start run: windowless
  (`pythonw` on Windows), detached from the caller (and out of its job object where Windows
  allows it), cwd = home, output to `serve.log`. It takes `serve.lock` first and exits at once when
  another daemon holds it, so starting it twice is harmless. When pbrain is upgraded, the next
  automatic start sees the version in `serve.json` differ, stops the old daemon and starts the new
  one. `pbrain serve --stop` stops it by hand.
- `/api/health` answers `{"app": "pbrain", "version", "pid", "registry"}`; `pbrain serve --status`
  and the standby loop use it to tell a pbrain server from another program.

`PBRAIN_AUTOSERVE=0` (or a `CI` environment variable) turns the automatic start off;
`PBRAIN_AUTOSERVE=1` forces it on in CI. `PBRAIN_PORT` changes the default port of `serve`, the
automatic start and `setup`.

### Autostart per platform

| Platform | Entry `pbrain setup` writes |
|---|---|
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\pbrain-serve` = `"<venv>\pythonw.exe" -m pbrain serve --daemon` (no admin, no console window) |
| macOS | `~/Library/LaunchAgents/dev.pbrain.serve.plist` (`RunAtLoad`) |
| Linux | `~/.config/systemd/user/pbrain-serve.service`, enabled with `systemctl --user`; without systemd, `~/.config/autostart/pbrain-serve.desktop` |

The entry points at the Python that ran `pbrain setup`. Run `setup` again after moving or
reinstalling pbrain into another environment. `PBRAIN_HOME` and `PBRAIN_PORT` are not stored in
the entry: set them in the user environment if you use them.

## Under a process manager (WebHost and the like)

A process manager can keep running the page too. Have it run, in the foreground:

```
pbrain serve --standby --no-open [--roots-file review_projects.txt]
```

Together with the daemon, exactly one of them serves the port and the other waits: if the manager
starts first the daemon waits, and if the daemon was first the managed process waits (it stays
alive, so the manager's port health check still passes against the daemon). Whichever stops,
the other takes over within a poll interval. A machine without the manager needs nothing else.

### myprobrain (the shared knowledge repo)

Until 0.6.0 the shared repo `myprobrain` ran the page through its `webhost.bat`, with the project
list in `review_projects.txt`, and its `scan.bat` scanned that list. After 0.6.0:

- `webhost.bat` runs `pbrain serve --standby --no-open --roots-file review_projects.txt`. On a
  machine with WebHost it is one of the two servers above and they never run twice; on a machine
  without WebHost nothing calls it, and the daemon serves the page.
- `review_projects.txt` is deprecated but still read (by `webhost.bat` and `scan.bat`). New
  projects need not be added to it: `pbrain init` registers them on the machine where it runs.
- `scan.bat` runs `pbrain scan . --all --roots-file review_projects.txt`.
- `requirements.txt` pins the pbrain release.

Once the daemon serves the page on a machine, the WebHost app for the page can be removed; nothing
depends on it.
