"""File tools: read_file, list_files, write_file, patch."""

import json
from pathlib import Path

from app.tools.models import ToolCapability, ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput
from app.tools.workspace import (
    current_workspace,
    is_path_authorized,
)

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

    Authorization: Paths must be within the authorized workspace unless
    they are project/system paths that SAGE needs to access internally.
    """

    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        resolved = expanded.resolve()
        # Check authorization for absolute paths
        if not is_path_authorized(resolved) and not _is_system_path(resolved):
            raise PermissionError(
                f"Access denied: '{resolved}' is outside the authorized workspace. "
                f"Use the 'authorize_workspace' tool to grant access to this directory."
            )
        return resolved

    workspace_candidate = (current_workspace() / expanded).resolve()
    if workspace_candidate.exists():
        if not is_path_authorized(workspace_candidate):
            raise PermissionError(
                f"Access denied: '{workspace_candidate}' is outside the authorized workspace. "
                f"Use the 'authorize_workspace' tool to grant access to this directory."
            )
        return workspace_candidate

    project_root = Path(__file__).resolve().parents[2]
    project_candidate = (project_root / expanded).resolve()
    if project_candidate.exists():
        return project_candidate

    return workspace_candidate


def _is_system_path(path: Path) -> bool:
    """True for project-root paths SAGE may access internally."""
    try:
        project_root = Path(__file__).resolve().parents[2]
        # Allow access to project root and employee data directories
        path.resolve().relative_to(project_root)
        return True
    except ValueError:
        return False


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


class AuthorizeWorkspaceInput(ToolInput):
    path: str


def authorize_workspace_tool(path: str) -> str:
    """Grant SAGE full access to a directory the user explicitly named."""
    from app.tools.workspace import authorize_workspace

    root = authorize_workspace(path, persistent=True)
    return json.dumps(
        {
            "authorized": str(root),
            "message": f"Full read/write access granted to {root} for this session.",
        }
    )


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
                "directories are created. Ordinary files are edited "
                "immediately; secrets, system files, and data stores outside "
                "an authorized directory require approval."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=WriteFileInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.WRITE],
            handler=write_file,
        )
    )

    registry.register(
        ToolDefinition(
            name="patch",
            description=(
                "Replace exact text in an existing file (surgical edit). "
                "old_text must match exactly once unless replace_all is "
                "true. Ordinary files are edited immediately; protected "
                "files outside an authorized directory require approval."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=PatchFileInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.WRITE],
            handler=patch,
        )
    )

    registry.register(
        ToolDefinition(
            name="authorize_workspace",
            description=(
                "Grant SAGE full read/write access to a directory the user "
                "explicitly pointed at (e.g. 'work in ~/projects/xyz' or "
                "'fix the bugs in /Users/me/app'). Call this before editing "
                "files in that directory so edits there run without prompts, "
                "including sensitive-looking files; sensitive paths outside "
                "the directory still require approval."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=AuthorizeWorkspaceInput,
            destructive=False,
            employee_eligible=True,
            capabilities=[ToolCapability.WRITE],
            handler=authorize_workspace_tool,
        )
    )
