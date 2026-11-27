"""`pbrain serve`: singleton daemon, exclusive port, standby takeover, live project list."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pbrain import registry, review, serve

SRC = str(Path(__file__).resolve().parents[1] / "src")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def pbrain_process(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": SRC, "PBRAIN_AUTOSERVE": "0", **(env_extra or {})}
    return subprocess.Popen(
        [sys.executable, "-m", "pbrain", *map(str, args)],
        env=env,
        cwd=os.environ["PBRAIN_HOME"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return None


def make_project(root, name):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pbrain.toml").write_text(f'project = "{name}"\n', encoding="utf-8")
    return root


@pytest.fixture
def page_server():
    servers = []

    def start(configs=(), use_registry=True):
        server = review.ReviewServer(("127.0.0.1", 0), list(configs), use_registry=use_registry)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server, server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_autoserve_switches(monkeypatch):
    monkeypatch.setenv("PBRAIN_AUTOSERVE", "0")
    assert not serve.autoserve_enabled() and serve.ensure_server() == "off"
    monkeypatch.setenv("PBRAIN_AUTOSERVE", "")
    monkeypatch.setenv("CI", "true")
    assert not serve.autoserve_enabled()
    monkeypatch.setenv("PBRAIN_AUTOSERVE", "1")
    assert serve.autoserve_enabled()


def test_ensure_server_spawns_only_without_daemon(monkeypatch):
    monkeypatch.setenv("PBRAIN_AUTOSERVE", "1")
    calls = []
    monkeypatch.setattr(serve, "spawn_daemon", lambda: calls.append(1) or True)
    assert serve.ensure_server() == "started" and calls == [1]
    holder = serve.Lock(serve.lock_path())
    assert holder.acquire()
    try:
        # A second handle cannot take the lock: that is how a live daemon is detected.
        assert serve.daemon_alive()
        assert serve.ensure_server() == "running" and calls == [1]
    finally:
        holder.release()
    assert not serve.daemon_alive()


def test_cli_keeps_page_up_after_commands(monkeypatch, project, run_cli):
    seen = []
    monkeypatch.setattr(serve, "ensure_server", lambda force=False: seen.append(1) or "running")
    run_cli("--root", project, "projects", "list")
    run_cli("--root", project, "serve", "--status")
    assert seen == [1]  # `projects` triggers it, `serve` does not


def test_second_bind_on_same_port_fails(page_server):
    _, port = page_server()
    with pytest.raises(OSError):
        review.ReviewServer(("127.0.0.1", port), [])


def test_health_and_registry_read_live(page_server, tmp_path):
    server, port = page_server()
    status, health = get(port, "/api/health")
    assert status == 200 and health["app"] == "pbrain" and health["registry"] is True
    assert get(port, "/api/projects")[1]["projects"] == []

    a = make_project(tmp_path / "a", "alpha")
    registry.add(a)
    projects = get(port, "/api/projects")[1]["projects"]
    assert [(p["name"], p["status"], p["origin"]) for p in projects] == [
        ("alpha", "ok", "registry")]
    assert get(port, "/api/queue?project=0")[0] == 200

    b = make_project(tmp_path / "b", "beta")
    registry.add(b)
    # A page that never listed projects again can still open a fresh index.
    assert get(port, "/api/queue?project=1")[0] == 200

    (a / "pbrain.toml").unlink()
    projects = get(port, "/api/projects")[1]["projects"]
    assert [p["status"] for p in projects] == ["missing", "ok"]
    assert get(port, "/api/queue?project=0")[0] == 410
    assert projects[1]["index"] == 1  # indexes stay put

    registry.remove(b)
    projects = get(port, "/api/projects")[1]["projects"]
    assert projects[1]["status"] == "removed"
    registry.add(b)
    assert get(port, "/api/projects")[1]["projects"][1]["status"] == "ok"


def test_config_edits_are_reloaded(page_server, tmp_path):
    root = make_project(tmp_path / "p", "first")
    registry.add(root)
    _, port = page_server()
    assert get(port, "/api/projects")[1]["projects"][0]["name"] == "first"
    toml = root / "pbrain.toml"
    toml.write_text('project = "second"\n', encoding="utf-8")
    later = time.time() + 5
    os.utime(toml, (later, later))
    assert get(port, "/api/projects")[1]["projects"][0]["name"] == "second"
    toml.write_text("project = [broken\n", encoding="utf-8")
    os.utime(toml, (later + 5, later + 5))
    entry = get(port, "/api/projects")[1]["projects"][0]
    assert entry["status"] == "error" and "pbrain.toml" in entry["error"]


def test_fixed_projects_come_first_and_are_not_duplicated(page_server, project):
    registry.add(project)
    from pbrain.config import load

    _, port = page_server([load(project)])
    projects = get(port, "/api/projects")[1]["projects"]
    assert len(projects) == 1 and projects[0]["origin"] == "fixed"


def test_review_reuses_running_page(monkeypatch, project, run_cli):
    monkeypatch.setattr(serve, "probe", lambda *a, **k: {"app": "pbrain", "registry": True})
    code, out, _ = run_cli("--root", project, "review", "--no-open")
    assert code == 0 and "already running" in out
    assert registry.find(project)


def test_daemon_is_a_singleton_and_stops(tmp_path):
    port = free_port()
    first = pbrain_process("serve", "--daemon", "--port", port)
    try:
        health = wait_for(lambda: serve.probe(port, timeout=0.5))
        assert health and health["app"] == "pbrain"
        assert wait_for(serve.daemon_alive)
        state = wait_for(lambda: serve.read_state() or None)
        assert state["pid"] == first.pid and state["port"] == port
        second = pbrain_process("serve", "--daemon", "--port", port)
        assert second.wait(timeout=20) == 0  # the lock is taken: exits at once
        assert serve.stop_daemon(timeout=15)
        first.wait(timeout=15)
    finally:
        if first.poll() is None:
            first.kill()
    assert "pbrain serve" in serve.log_path().read_text(encoding="utf-8")


def test_standby_takes_over_when_port_frees(page_server):
    server, port = page_server()
    waiting = pbrain_process(
        "serve", "--standby", "--no-current", "--port", port,
        env_extra={"PBRAIN_STANDBY_POLL": "0.2"},
    )
    try:
        time.sleep(1.0)
        assert waiting.poll() is None  # still waiting, did not bind twice
        assert get(port, "/api/health")[1]["pid"] == os.getpid()
        server.shutdown()
        server.server_close()
        health = wait_for(
            lambda: (serve.probe(port, timeout=0.5) or {}).get("pid") == waiting.pid
        )
        assert health
    finally:
        waiting.kill()
        waiting.wait(timeout=15)


def test_status_lines(run_cli):
    code, out, _ = run_cli("serve", "--status", "--port", free_port())
    assert code == 0 and "daemon: not running" in out and "registry:" in out
