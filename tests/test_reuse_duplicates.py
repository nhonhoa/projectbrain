from pbrain.config import Config, ReuseConfig
from pbrain.reuse.duplicates import audit, find_duplicates

ORIGINAL = '''
def parse_date_text(value):
    """Turn a dd/mm/yyyy or yyyy-mm-dd string into a date; empty or invalid gives None."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
'''
COPY = ORIGINAL.replace("parse_date_text", "_to_date").replace("value", "raw").replace("text", "s")
UNRELATED = """
def total_amount(rows):
    total = 0
    for row in rows:
        if row.get("amount") and not row.get("cancelled"):
            total += int(row["amount"]) * int(row.get("qty") or 1)
    return total if total > 0 else None
"""


def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/dates.py").write_text(ORIGINAL, encoding="utf-8")
    (tmp_path / "src/page.py").write_text(COPY + UNRELATED, encoding="utf-8")
    return Config(root=tmp_path, reuse=ReuseConfig(roots=("src",)))


def test_copied_function_with_renamed_variables_is_reported(tmp_path):
    findings = find_duplicates(project(tmp_path), ["src/page.py"])
    assert len(findings) == 1, findings
    assert "_to_date" in findings[0]["new"]
    assert "src/dates.py:2 parse_date_text" in findings[0]["existing"]
    assert "identical body" in findings[0]["why"]


def test_unrelated_function_is_not_reported(tmp_path):
    findings = find_duplicates(project(tmp_path), ["src/page.py"])
    assert all("total_amount" not in f["new"] for f in findings)


def test_same_name_in_another_file_is_reported_unless_common(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text(
        "def slugify(t):\n    return t\n\ndef main():\n    return 1\n"
    )
    (tmp_path / "src/b.py").write_text(
        "def slugify(t):\n    return t\n\ndef main():\n    return 2\n"
    )
    config = Config(root=tmp_path, reuse=ReuseConfig(roots=("src",)))
    findings = find_duplicates(config, ["src/b.py"])
    assert [f["why"] for f in findings] == ["same function name in another file"]
    assert [c["why"] for c in audit(config)] == ["same function name"]
    # project-specific generic names can be added through the config
    config = Config(root=tmp_path, reuse=ReuseConfig(roots=("src",), common_names=("slugify",)))
    assert find_duplicates(config, ["src/b.py"]) == []
    assert audit(config) == []


def test_audit_groups_identical_bodies(tmp_path):
    clusters = audit(project(tmp_path))
    assert len(clusters) == 1
    assert clusters[0]["why"] == "identical body"
    assert len(clusters[0]["members"]) == 2


def test_empty_project_has_no_findings(tmp_path):
    config = Config(root=tmp_path)
    assert find_duplicates(config, ["x.py"]) == []
    assert audit(config) == []
