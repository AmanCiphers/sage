"""File tools: read_file, list_files, write_file, patch."""

import json
from pathlib import Path

from app.tools.models import ToolCapability, ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput

MAX_READ_CHARS = 100_000
MAX_LIST_ENTRIES = 200
MAX_WRITE_CHARS = 1_000_000


def resolve_path(path: str) -> Path:
    """Expand ~ and anchor relative paths to the current workspace.

    Mirrors Hermes: relative paths never depend on the process cwd — they
    resolve under a configured workspace root. The root is the global SAGE
    workspace except during an employee turn, when it is the employee's own
    workspace (see app/tools/workspace.py).

    Paths that don't exist under the workspace but do exist under the
    project root (e.g. `data/employees/...`) resolve from there, so SAGE
    can inspect its own data/employee stores without needing absolute paths.
    """

    from app.tools.workspace import current_workspace

    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()

    workspace_candidate = (current_workspace() / expanded).resolve()
    if workspace_candidate.exists():
        return workspace_candidate

    from pathlib import Path as _Path
    project_root = _Path(__file__).resolve().parents[2]
    project_candidate = (project_root / expanded).resolve()
    if project_candidate.exists():
        return project_candidate

    return workspace_candidate


class ReadFileInput(ToolInput):
    path: str
    offset: int = 0  # line offset
    limit: int = 200  # max lines


class ListFilesInput(ToolInput):
    path: str = "."
    recursive: bool = False
    limit: int = 200


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
    # Prefix each line with its 1-based number: "N: content" so the model can
    # reference exact lines and request the next slice precisely.
    content = "\n".join(
        f"{offset + i + 1}: {line}" for i, line in enumerate(window)
    )[:MAX_READ_CHARS]

    return json.dumps(
        {
            "path": str(file_path),
            "total_lines": len(lines),
            "offset": offset,
            "limit": limit,
            "lines": content,
        }
    )


def list_files(path: str = ".", recursive: bool = False, limit: int = 200) -> str:
    directory = resolve_path(path)

    if not directory.is_dir():
        return json.dumps({"error": f"Not a directory: {directory}"})

    limit = max(1, min(limit, 1000))
    try:
        if recursive:
            entries = [
                str(p.relative_to(directory)) + ("/" if p.is_dir() else "")
                for p in sorted(directory.rglob("*"))
            ]
        else:
            entries = sorted(
                entry.name + ("/" if entry.is_dir() else "")
                for entry in directory.iterdir()
            )
    except OSError as exc:
        return json.dumps({"error": f"Cannot list {directory}: {exc}"})

    truncated = len(entries) > limit
    entries = entries[:limit]

    return json.dumps(
        {
            "path": str(directory),
            "recursive": recursive,
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
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
    from app.tools.workspace import current_workspace

    workspace = current_workspace()
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
                "workspace. Each line is prefixed with its 1-based number. "
                "Use offset and limit to read large files in sections. "
                "Prefer one larger read over many small slices; use grep to "
                "find specific content in large files."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=ReadFileInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.READ],
            handler=read_file,
        )
    )

    registry.register(
        ToolDefinition(
            name="list_files",
            description=(
                "List files and directories at a path. Relative paths resolve "
                "under the SAGE workspace. Set recursive=true to list the "
                "entire tree; limit bounds the number of entries."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=ListFilesInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.READ],
            handler=list_files,
        )
    )

    registry.register(
        ToolDefinition(
            name="write_file",
            description=(
                "Create or overwrite a text file. Relative paths resolve "
                "under the workspace directory and missing parent "
                "directories are created. Edits run immediately — no "
                "approval is required."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=WriteFileInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.WRITE],
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
                "true. Edits run immediately — no approval is required."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=PatchFileInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.WRITE],
            requires_approval=_write_needs_approval,
            handler=patch,
        )
    )
