import os
import subprocess
import sys
from pathlib import Path

import pytest

from pbrain import __version__, cli

SRC = str(Path(__file__).resolve().parent.parent / "src")
RUNNER = Path(__file__).resolve().parent.parent / "src/pbrain/templates/pbrain_run.py"


def pbrain_env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_missing_subcommand_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == 2
    assert "required" in capsys.readouterr().err


def test_unknown_subcommand_is_an_argparse_error():
    with pytest.raises(SystemExit) as info:
        cli.main(["nope"])
    assert info.value.code == 2


def test_root_option_selects_another_project(tmp_path, project, run_cli, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run_cli("--root", project, "wiki", "lint")
    assert code == 0
    assert "1 pages" in out


def test_root_option_without_config_uses_given_directory(tmp_path, run_cli):
    code, out, _ = run_cli("--root", tmp_path, "init")
    assert code == 0
    assert f"Project root: {tmp_path.resolve()}" in out


def test_module_help_in_subprocess():
    done = subprocess.run(
        [sys.executable, "-m", "pbrain", "--help"],
        capture_output=True,
        encoding="utf-8",
        env=pbrain_env(),
    )
    assert done.returncode == 0
    for command in ("init", "wiki", "check", "hook", "reuse", "sync", "cases"):
        assert command in done.stdout


def test_module_exit_code_propagates(tmp_path, helpers):
    helpers.write(tmp_path, "pbrain.toml", "")
    helpers.write(tmp_path, "docs/wiki/index.md", "- [x](missing.md) - gone\n")
    done = subprocess.run(
        [sys.executable, "-m", "pbrain", "--root", str(tmp_path), "wiki", "lint"],
        capture_output=True,
        encoding="utf-8",
        env=pbrain_env(),
    )
    assert done.returncode == 1


def test_runner_runs_pbrain_from_a_project(tmp_path, helpers):
    helpers.write(tmp_path, "pbrain.toml", "")
    runner = helpers.write(tmp_path, ".claude/hooks/pbrain_run.py", RUNNER.read_text("utf-8"))
    done = subprocess.run(
        [sys.executable, str(runner), "--version"],
        capture_output=True,
        encoding="utf-8",
        env=pbrain_env(PBRAIN_PYTHON=sys.executable),
    )
    assert done.returncode == 0
    assert __version__ in done.stdout


def test_runner_propagates_failure_exit_code(tmp_path, helpers):
    helpers.write(tmp_path, "pbrain.toml", "")
    helpers.write(tmp_path, "docs/wiki/index.md", "- [x](missing.md) - gone\n")
    runner = helpers.write(tmp_path, ".claude/hooks/pbrain_run.py", RUNNER.read_text("utf-8"))
    done = subprocess.run(
        [sys.executable, str(runner), "wiki", "lint"],
        capture_output=True,
        encoding="utf-8",
        env=pbrain_env(PBRAIN_PYTHON=sys.executable),
    )
    assert done.returncode == 1


def test_runner_exits_zero_when_no_interpreter_has_pbrain(tmp_path, helpers):
    helpers.write(tmp_path, "pbrain.toml", "")
    runner = helpers.write(tmp_path, ".claude/hooks/pbrain_run.py", RUNNER.read_text("utf-8"))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PBRAIN_PYTHON"] = str(tmp_path / "does-not-exist")
    # -I (isolated) ignores PYTHONPATH and user site, so the running interpreter cannot import
    # pbrain unless it is installed system-wide; an installed pbrain only makes the probe pass,
    # in which case the runner legitimately runs it, so build a script that cannot reach it.
    probe = subprocess.run(
        [sys.executable, "-I", "-c", "import pbrain"], capture_output=True, env=env
    )
    if probe.returncode == 0:
        pytest.skip("pbrain is installed in this interpreter, cannot simulate its absence")
    done = subprocess.run(
        [sys.executable, "-I", str(runner), "--version"],
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    assert done.returncode == 0
    assert done.stdout == ""


def test_invalid_config_reports_error(tmp_path, run_cli):
    (tmp_path / "pbrain.toml").write_text("project = \n", encoding="utf-8")
    code, _, err = run_cli("--root", tmp_path, "check")
    assert code == 2
    assert "pbrain.toml" in err


def test_invalid_config_fails_open_for_hooks(tmp_path, run_cli, stdin):
    (tmp_path / "pbrain.toml").write_text("project = \n", encoding="utf-8")
    stdin("{}")
    code, out, _ = run_cli("--root", tmp_path, "hook", "stop")
    assert (code, out) == (0, "")
