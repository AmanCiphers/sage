import json

from app.tools.file_tools import list_files, read_file


def test_read_file_window(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("\n".join(f"line {i}" for i in range(50)))

    result = json.loads(read_file(str(target), offset=10, limit=5))

    assert result["total_lines"] == 50
    assert result["offset"] == 10
    assert result["lines"] == "line 10\nline 11\nline 12\nline 13\nline 14"


def test_read_file_missing(tmp_path):
    result = json.loads(read_file(str(tmp_path / "nope.txt")))
    assert "error" in result


def test_list_files(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()

    result = json.loads(list_files(str(tmp_path)))

    assert result["entries"] == ["a.txt", "sub/"]


def test_list_files_missing(tmp_path):
    result = json.loads(list_files(str(tmp_path / "nope")))
    assert "error" in result
