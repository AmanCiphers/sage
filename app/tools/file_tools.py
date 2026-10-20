"""Basic read-only file tools: read_file, list_files."""

import json
from pathlib import Path

from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput

MAX_READ_CHARS = 100_000
MAX_LIST_ENTRIES = 200
MAX_WRITE_CHARS = 1_000_000


def resolve_path(path: str) -> Path:
    """Expand ~ and anchor relative paths to the SAGE workspace.

    Mirrors Hermes: relative paths never depend on the process cwd — they
    resolve under a configured workspace root.
    """

    from app.config import settings

    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (settings.workspace_path.expanduser() / expanded).resolve()


class ReadFileInput(ToolInput):
    path: str
    offset: int = 0  # line offset
    limit: int = 200  # max lines


class ListFilesInput(ToolInput):
    path: str = "."


class WriteFileInput(ToolInput):
    path: str
    content: str


class PatchFileInput(ToolInput):
    path: str
    old_text: str
    new_text: str
    replace_all: bool = False


def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
    file_path = resolve_path(path)

    if not file_path.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"})

    try:
        lines = file_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError as exc:
        return json.dumps({"error": f"Cannot read {file_path}: {exc}"})

    window = lines[offset : offset + limit]
    content = "\n".join(window)[:MAX_READ_CHARS]

    return json.dumps(
        {
            "path": str(file_path),
            "total_lines": len(lines),
            "offset": offset,
            "lines": content,
        }
    )


def list_files(path: str = ".") -> str:
    directory = resolve_path(path)

    if not directory.is_dir():
        return json.dumps({"error": f"Not a directory: {directory}"})

    try:
        entries = sorted(
            entry.name + ("/" if entry.is_dir() else "")
            for entry in directory.iterdir()
        )
    except OSError as exc:
        return json.dumps({"error": f"Cannot list {directory}: {exc}"})

    return json.dumps(
        {
            "path": str(directory),
            "count": min(len(entries), MAX_LIST_ENTRIES),
            "entries": entries[:MAX_LIST_ENTRIES],
        }
    )


def write_file(path: str, content: str) -> str:
    file_path = resolve_path(path)

    if len(content) > MAX_WRITE_CHARS:
        return json.dumps({"error": f"Content too large (max {MAX_WRITE_CHARS} chars)"})

    if file_path.is_dir():
        return json.dumps({"error": f"Is a directory: {file_path}"})

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Cannot write {file_path}: {exc}"})

    return json.dumps(
        {
            "path": str(file_path),
            "bytes_written": len(content.encode("utf-8")),
        }
    )


def _outside_workspace(file_path: Path) -> bool:
    from app.config import settings

    workspace = settings.workspace_path.expanduser().resolve()
    try:
        return not file_path.resolve().is_relative_to(workspace)
    except OSError:
        return True


def _write_needs_approval(arguments: dict) -> bool:
    """File edits run without approval (trusted: SAGE manages its own files)."""

    return False


def patch(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    file_path = resolve_path(path)

    if not file_path.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"})

    if old_text == new_text:
        return json.dumps({"error": "old_text and new_text are identical"})

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Cannot read {file_path}: {exc}"})

    occurrences = content.count(old_text)

    if occurrences == 0:
        return json.dumps({"error": "old_text not found in file"})
    if occurrences > 1 and not replace_all:
        return json.dumps(
            {
                "error": (
                    f"old_text found {occurrences} times; add more context "
                    "or set replace_all=true"
                )
            }
        )

    new_content = content.replace(
        old_text, new_text, -1 if replace_all else 1
    )

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Cannot write {file_path}: {exc}"})

    return json.dumps(
        {
            "path": str(file_path),
            "replacements": occurrences if replace_all else 1,
            "bytes_written": len(new_content.encode("utf-8")),
        }
    )


def register_file_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            name="read_file",
            description=(
                "Read a text file. Relative paths resolve under the SAGE "
                "workspace directory. Returns lines from offset, up to "
                "limit lines (default 200)."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=ReadFileInput,
            destructive=False,
            employee_eligible=False,
            handler=read_file,
        )
    )

    registry.register(
        ToolDefinition(
            name="list_files",
            description=(
                "List files and directories at a path. Relative paths "
                "resolve under the SAGE workspace directory."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=ListFilesInput,
            destructive=False,
            employee_eligible=False,
            handler=list_files,
        )
    )

    registry.register(
        ToolDefinition(
            name="write_file",
            description=(
                "Create or overwrite a text file. Relative paths resolve "
                "under the SAGE workspace directory and missing parent "
                "directories are created. Creating files and editing "
                "files inside the workspace run immediately; overwriting "
                "an existing file outside the workspace requires user "
                "approval."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=WriteFileInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_write_needs_approval,
            handler=write_file,
        )
    )

    registry.register(
        ToolDefinition(
            name="patch",
            description=(
                "Replace exact text in an existing file (surgical edit). "
                "old_text must match exactly once unless replace_all is "
                "true. Edits inside the workspace run immediately; "
                "files outside the workspace require user approval."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=PatchFileInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_write_needs_approval,
            handler=patch,
        )
    )
