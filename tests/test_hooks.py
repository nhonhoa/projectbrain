import json

import pytest

from pbrain import wiki


def hook(run_cli, stdin, root, event, payload=None, raw=None):
    stdin(raw if raw is not None else json.dumps(payload if payload is not None else {}))
    return run_cli("--root", root, "hook", event)


def write_payload(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def test_pre_tool_use_blocks_violation(guarded, run_cli, stdin, bad_js):
    payload = write_payload("src/n.js", bad_js)
    code, _, err = hook(run_cli, stdin, guarded, "pre-tool-use", payload)
    assert code == 2
    assert "Notify" in err


def test_pre_tool_use_allows_clean_edit(guarded, run_cli, stdin):
    payload = write_payload("src/n.js", "const x = 1;\n")
    code, out, err = hook(run_cli, stdin, guarded, "pre-tool-use", payload)
    assert (code, out, err) == (0, "", "")


@pytest.mark.parametrize("raw", ["", "   ", "{not json", "[1, 2]", "null"])
def test_pre_tool_use_bad_payload_is_ignored(guarded, run_cli, stdin, raw):
    code, _, err = hook(run_cli, stdin, guarded, "pre-tool-use", raw=raw)
    assert code == 0 and err == ""


def test_session_start_mentions_wiki_and_drift(drift, run_cli, stdin):
    code, out, _ = hook(run_cli, stdin, drift, "session-start")
    assert code == 0
    assert "Project wiki" in out and "index.md" in out
    assert "needing review" in out and "totals.md" in out


def test_session_start_clean_project_has_no_review_line(project, run_cli, stdin):
    code, out, _ = hook(run_cli, stdin, project, "session-start")
    assert code == 0
    assert "Project wiki" in out
    assert "needing review" not in out


def test_session_start_without_wiki_is_silent(tmp_path, run_cli, stdin):
    code, out, _ = hook(run_cli, stdin, tmp_path, "session-start")
    assert (code, out) == (0, "")


def test_stop_without_changes_is_silent(project, run_cli, stdin):
    code, out, _ = hook(run_cli, stdin, project, "stop", {"session_id": "s1"})
    assert (code, out) == (0, "")


def test_stop_blocks_once_per_session(drift, run_cli, stdin):
    payload = {"session_id": "s1"}
    code, out, _ = hook(run_cli, stdin, drift, "stop", payload)
    assert code == 0
    reply = json.loads(out)
    assert reply["decision"] == "block"
    assert "totals.md" in reply["reason"]
    code, out, _ = hook(run_cli, stdin, drift, "stop", payload)
    assert (code, out) == (0, "")
    # A different session is reminded again.
    _, out, _ = hook(run_cli, stdin, drift, "stop", {"session_id": "s2"})
    assert json.loads(out)["decision"] == "block"


def test_stop_hook_active_is_silent(drift, run_cli, stdin):
    payload = {"session_id": "s1", "stop_hook_active": True}
    code, out, _ = hook(run_cli, stdin, drift, "stop", payload)
    assert (code, out) == (0, "")


def test_stop_unrelated_change_is_silent(project, helpers, run_cli, stdin):
    helpers.write(project, "src/other.py", "x = 1\n")
    code, out, _ = hook(run_cli, stdin, project, "stop", {"session_id": "s1"})
    assert (code, out) == (0, "")


def test_hooks_fail_open_on_internal_error(drift, run_cli, stdin, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(wiki, "pages_covering", boom)
    code, out, _ = hook(run_cli, stdin, drift, "stop", {"session_id": "s1"})
    assert (code, out) == (0, "")


def test_unknown_event_is_rejected_by_argparse(project, run_cli):
    with pytest.raises(SystemExit) as info:
        run_cli("--root", project, "hook", "nope")
    assert info.value.code == 2


def _agy_tool_payload(name, args):
    return {
        "hookEventName": "PreToolUse",
        "conversationId": "conv-1",
        "toolCall": {"id": "t1", "name": name, "args": args},
    }


def test_agy_pre_tool_use_denies_write_file_and_edit_file(guarded, run_cli, stdin, bad_js):
    payload = _agy_tool_payload("write_file", {"Path": "src/n.js", "Content": bad_js})
    code, out, err = hook(run_cli, stdin, guarded, "pre-tool-use", payload)
    assert code == 0 and err == ""
    reply = json.loads(out)
    assert reply["decision"] == "deny"
    assert "Notify" in reply["reason"]

    edit_payload = _agy_tool_payload(
        "edit_file", {"Path": "src/n.js", "Target": "x", "Replacement": bad_js}
    )
    code, out, _ = hook(run_cli, stdin, guarded, "pre-tool-use", edit_payload)
    assert code == 0
    assert json.loads(out)["decision"] == "deny"


def test_agy_pre_tool_use_allows_clean_edit(guarded, run_cli, stdin):
    payload = _agy_tool_payload("write_file", {"Path": "src/n.js", "Content": "const x = 1;\n"})
    code, out, err = hook(run_cli, stdin, guarded, "pre-tool-use", payload)
    assert code == 0 and err == ""
    assert json.loads(out) == {"decision": "allow"}


def test_agy_session_start_outputs_inject_steps(drift, run_cli, stdin):
    payload = {"hookEventName": "SessionStart", "conversationId": "conv-1"}
    code, out, _ = hook(run_cli, stdin, drift, "session-start", payload)
    assert code == 0
    reply = json.loads(out)
    msg = reply["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "Project wiki" in msg and "totals.md" in msg


def test_agy_session_start_without_wiki_outputs_empty_json(tmp_path, run_cli, stdin):
    payload = {"hookEventName": "SessionStart", "conversationId": "conv-1"}
    code, out, _ = hook(run_cli, stdin, tmp_path, "session-start", payload)
    assert code == 0
    assert json.loads(out) == {}


def test_agy_stop_continues_once_per_conversation(drift, run_cli, stdin):
    payload = {"hookEventName": "Stop", "conversationId": "conv-1"}
    code, out, _ = hook(run_cli, stdin, drift, "stop", payload)
    assert code == 0
    reply = json.loads(out)
    assert reply["decision"] == "continue"
    assert "totals.md" in reply["reason"]

    code, out, _ = hook(run_cli, stdin, drift, "stop", payload)
    assert code == 0
    assert json.loads(out) == {}

    code, out, _ = hook(
        run_cli,
        stdin,
        drift,
        "stop",
        {"hookEventName": "Stop", "conversationId": "conv-2", "continuationActive": True},
    )
    assert code == 0
    assert json.loads(out) == {}
