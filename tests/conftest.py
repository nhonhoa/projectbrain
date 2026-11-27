"""Shared helpers: temporary git repos and a CLI runner."""

import io
import os
import subprocess
import sys
from datetime import date, timedelta

import pytest

from pbrain import cli

CATALOG = r"""
REUSE_CATALOG = (
    {"name": "Notify", "kind": "js", "path": "src/notify.js", "symbol": "Notify",
     "use_when": "Show a notice.", "keywords": "notice",
     "instead_of": (r"function\s+showNotice\s*\(",)},
)
"""
BAD_JS = "function show" + "Notice(msg) { alert(msg); }\n"
PAGE = """---
title: Totals
type: concept
status: active
trust: agreed
covers:
  - src/app.py::total
sources:
  - src/app.py
verified: {verified}
---
## Intent
x
## Reality
x
## Gaps
x
## Related
x
"""


def run_git(root, *args, date_str=None):
    env = dict(os.environ)
    if date_str:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date_str
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


def init_repo(root):
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "Test")
    run_git(root, "config", "user.email", "test@example.com")
    run_git(root, "config", "commit.gpgsign", "false")


def commit_all(root, date_str="2025-12-31T12:00:00"):
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "change", date_str=date_str)


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture(autouse=True)
def pbrain_home(tmp_path_factory, monkeypatch):
    """Each test gets its own ~/.pbrain and never starts the real review daemon."""
    home = tmp_path_factory.mktemp("pbrain-home")
    monkeypatch.setenv("PBRAIN_HOME", str(home))
    monkeypatch.setenv("PBRAIN_AUTOSERVE", "0")
    return home


@pytest.fixture
def helpers():
    class Helpers:
        git = staticmethod(run_git)
        init_repo = staticmethod(init_repo)
        commit_all = staticmethod(commit_all)
        write = staticmethod(write)

    return Helpers


@pytest.fixture
def project(tmp_path):
    """Clean git project: config, wiki page covering ``src/app.py::total``, committed."""
    init_repo(tmp_path)
    write(tmp_path, "src/app.py", "def total(x):\n    return x + 1\n")
    write(tmp_path, "pbrain.toml", 'project = "demo"\n[check]\nruff = false\n')
    from pbrain.config import load
    from pbrain.init import install_wiki

    install_wiki(load(tmp_path))
    write(tmp_path, "docs/wiki/totals.md", PAGE.format(verified=date.today().isoformat()))
    write(tmp_path, "docs/wiki/index.md", "- [Totals](totals.md) - totals\n")
    yesterday = date.today() - timedelta(days=1)
    commit_all(tmp_path, f"{yesterday.isoformat()}T12:00:00")
    return tmp_path


@pytest.fixture
def drift(project):
    """Make the page of ``project`` drift: change the covered function after ``verified``."""
    write(project, "src/app.py", "def total(x):\n    return x + 2\n")
    return project


@pytest.fixture
def stdin(monkeypatch):
    def feed(text):
        fake = io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8")
        monkeypatch.setattr(sys, "stdin", fake)

    return feed


@pytest.fixture
def run_cli(capsys):
    """Call ``pbrain.cli.main``; return ``(exit code, stdout, stderr)``."""

    def call(*argv):
        code = cli.main([str(a) for a in argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    return call


@pytest.fixture
def bad_js():
    """Code that re-implements the ``Notify`` component of the test catalog."""
    return BAD_JS


@pytest.fixture
def guarded(project):
    """``project`` plus a reuse catalog whose entry forbids re-implementing ``Notify``."""
    write(
        project,
        "pbrain.toml",
        'project = "demo"\n[check]\nruff = false\n'
        '[reuse]\ncatalog = "catalog.py"\nroots = ["src"]\n',
    )
    write(project, "catalog.py", CATALOG)
    write(project, "src/notify.js", "const Notify = {};\n")
    commit_all(project)
    return project
