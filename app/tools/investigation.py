"""Investigation & web tools: grep, glob, web_search, browse_url.

Modeled on OpenCode's investigation tooling (grep/glob use ripgrep, which is
fast and handles filesystem search well). These let SAGE and its employees
find files, search contents, and look things up on the web efficiently.

All tools are read-only (READ_ONLY classification), bounded by limits and
timeouts so a pathological search can't hang a turn.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path

from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput

DEFAULT_SEARCH_LIMIT = 50
DEFAULT_SEARCH_TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 20_000
NO_RIPGREP_MSG = "ripgrep (rg) is not installed. Install it: brew install ripgrep"


def _has_ripgrep() -> bool:
    return shutil.which("rg") is not None


async def _run(args: list[str], cwd: Path, timeout: int) -> tuple[str, str, int]:
    """Run a subprocess and return (stdout, stderr, returncode).

    Async so it can be awaited inside SAGE's running event loop (the
    previous `asyncio.run()` crashed with "cannot be called from a running
    event loop").
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
            proc.returncode,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return "", f"timed out after {timeout}s", -1


# ---------------------------------------------------------------- grep ----

class GrepInput(ToolInput):
    pattern: str
    path: str = "."
    include: str | None = None
    literal: bool = False
    case_sensitive: bool = True
    limit: int = DEFAULT_SEARCH_LIMIT


def _resolve_search_root(path: str) -> Path:
    from app.tools.file_tools import resolve_path

    root = resolve_path(path)
    if root.is_file():
        return root.parent
    return root


async def grep(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    literal: bool = False,
    case_sensitive: bool = True,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> str:
    """Search file contents using ripgrep. Returns `path: Line N: text` lines."""
    if not pattern:
        return json.dumps({"error": "pattern is required"})
    if not _has_ripgrep():
        return json.dumps({"error": NO_RIPGREP_MSG})

    limit = max(1, min(limit, 200))
    cwd = _resolve_search_root(path)

    cmd = ["rg", "--no-heading", "--line-number", "--max-count", "1"]
    if literal:
        cmd.append("--fixed-strings")
    if not case_sensitive:
        cmd.append("-i")
    if include:
        cmd.extend(["-g", include])
    cmd.extend(["--", pattern])

    started = time.perf_counter()
    out, err, code = await _run(cmd, cwd, DEFAULT_SEARCH_TIMEOUT_SECONDS)

    if code == 2:
        return json.dumps({"error": f"ripgrep error: {err.strip() or 'invalid pattern'}"})

    lines = out.splitlines()
    truncated = len(lines) > limit
    lines = lines[:limit]

    # Convert `path:line:text` to absolute-ish relative paths for clarity.
    rendered = [
        f"Found {len(lines)} match(es)"
    ]
    if lines:
        rendered.append("")
        rendered.extend(lines)
    if truncated:
        rendered.append("")
        rendered.append(f"(Results truncated: showing first {limit}. Narrow with path or include.)")

    return json.dumps({
        "matches": lines,
        "count": len(lines),
        "truncated": truncated,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "output": "\n".join(rendered),
    })


# ---------------------------------------------------------------- glob ----

class GlobInput(ToolInput):
    pattern: str
    path: str = "."
    hidden: bool = False
    limit: int = DEFAULT_SEARCH_LIMIT


async def glob(
    pattern: str,
    path: str = ".",
    hidden: bool = False,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> str:
    """Find files by glob pattern (e.g. **/*.ts) using ripgrep's file listing."""
    if not pattern:
        return json.dumps({"error": "pattern is required"})
    if not _has_ripgrep():
        return json.dumps({"error": NO_RIPGREP_MSG})

    limit = max(1, min(limit, 500))
    cwd = _resolve_search_root(path)

    cmd = ["rg", "--files"]
    if hidden:
        cmd.append("--hidden")
    cmd.extend(["-g", pattern])

    started = time.perf_counter()
    out, err, code = await _run(cmd, cwd, DEFAULT_SEARCH_TIMEOUT_SECONDS)

    if code == 2:
        return json.dumps({"error": f"ripgrep error: {err.strip() or 'invalid glob pattern'}"})

    entries = [ln for ln in out.splitlines() if ln.strip()]
    truncated = len(entries) > limit
    entries = entries[:limit]

    rendered = [f"Found {len(entries)} file(s)"]
    if entries:
        rendered.append("")
        rendered.extend(entries)
    if truncated:
        rendered.append("")
        rendered.append(f"(Results truncated: showing first {limit}. Narrow the pattern.)")

    return json.dumps({
        "files": entries,
        "count": len(entries),
        "truncated": truncated,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "output": "\n".join(rendered),
    })


# ------------------------------------------------------- web_search ------

class WebSearchInput(ToolInput):
    query: str
    max_results: int = 5


def _web_search_provider() -> str | None:
    from app.config import settings

    if settings.tavily_api_key:
        return "tavily"
    # Bing RSS is the free, keyless fallback (reliable, no CAPTCHA).
    return "bing"


def _search_bing(query: str, max_results: int) -> list[dict]:
    import html as _html
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({"q": query, "format": "rss"})
    url = f"https://www.bing.com/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SAGE)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            rss = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [{"error": f"Bing search failed: {e}"}]

    def clean(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        return " ".join(_html.unescape(s).split())

    results: list[dict] = []
    for item in re.findall(r"<item>(.*?)</item>", rss, re.S)[:max_results]:
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        desc = re.search(r"<description>(.*?)</description>", item, re.S)
        results.append({
            "title": clean(title.group(1)) if title else "",
            "url": clean(link.group(1)) if link else "",
            "snippet": (clean(desc.group(1)) if desc else "")[:300],
        })
    return results


def _search_tavily(query: str, max_results: int) -> list[dict]:
    import urllib.request

    from app.config import settings

    body = json.dumps({
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"Tavily search failed: {e}"}]

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:300],
        }
        for r in data.get("results", [])[:max_results]
    ]


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web. Uses Tavily if configured, else Bing RSS (free)."""
    if not query.strip():
        return json.dumps({"error": "query is required"})

    max_results = max(1, min(max_results, 10))
    provider = _web_search_provider()

    if provider == "tavily":
        results = _search_tavily(query, max_results)
    else:
        results = _search_bing(query, max_results)

    if any("error" in r for r in results):
        return json.dumps({"provider": provider, "results": results})

    rendered = [f"Web search results ({provider}):", ""]
    for i, r in enumerate(results, 1):
        rendered.append(f"{i}. {r['title']}")
        rendered.append(f"   {r['url']}")
        if r.get("snippet"):
            rendered.append(f"   {r['snippet']}")
        rendered.append("")

    return json.dumps({
        "provider": provider,
        "results": results,
        "output": "\n".join(rendered),
    })


# ------------------------------------------------------ browse_url --------

class BrowseUrlInput(ToolInput):
    url: str
    max_chars: int = 8000


def _html_to_text(html: str) -> str:
    # Strip scripts/styles, then tags, collapse whitespace.
    html = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|li|h[1-6]|tr)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def browse_url(url: str, max_chars: int = 8000) -> str:
    """Fetch and read a web page, returning its text content as markdown-ish."""
    import urllib.request

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (SAGE; research)"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read()
    except Exception as e:
        return json.dumps({"error": f"Cannot fetch {url}: {e}"})

    if "text/html" in ctype.lower() or b"<html" in raw[:2000].lower():
        text = _html_to_text(raw.decode("utf-8", errors="replace"))
    else:
        text = raw.decode("utf-8", errors="replace")

    text = text.strip()
    truncated = len(text) > max_chars
    text = text[:max_chars]

    return json.dumps({
        "url": url,
        "content_type": ctype,
        "truncated": truncated,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "content": text,
    })


# --------------------------------------------------- registration --------

def register_investigation_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            name="grep",
            description=(
                "Search file contents using ripgrep regex or literal matching. "
                "Use to locate code, symbols, or text. Narrow with `path` (a "
                "file or directory) and `include` (a glob like '*.py' or "
                "'*.{ts,tsx}'). Returns 'path: Line N: text'. Bounded by "
                "timeout and limit."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=GrepInput,
            destructive=False,
            employee_eligible=True,
            handler=grep,
        )
    )

    registry.register(
        ToolDefinition(
            name="glob",
            description=(
                "Find files by glob pattern (e.g. '**/*.ts', 'src/**/*.tsx'). "
                "Set `hidden` to include dotfiles. Returns matching file paths. "
                "Bounded by limit."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=GlobInput,
            destructive=False,
            employee_eligible=True,
            handler=glob,
        )
    )

    registry.register(
        ToolDefinition(
            name="web_search",
            description=(
                "Search the web for current information beyond the knowledge "
                "cutoff. Returns ranked results with title, URL, and snippet. "
                "Uses Tavily if configured, else Bing (free, no key needed)."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=WebSearchInput,
            destructive=False,
            employee_eligible=True,
            handler=web_search,
        )
    )

    registry.register(
        ToolDefinition(
            name="browse_url",
            description=(
                "Fetch and read a web page, returning its text content. Use "
                "to read a full page (article, docs) rather than just search "
                "snippets. The page text is stripped of HTML/scripts."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=BrowseUrlInput,
            destructive=False,
            employee_eligible=True,
            handler=browse_url,
        )
    )
