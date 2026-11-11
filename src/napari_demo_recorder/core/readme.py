"""Markdown snippets with absolute URLs (napari-hub and PyPI need them) and README updates."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .encode import EncodeResult
from .format import DemoSpec

GITHUB_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def github_raw_url(path: Path, branch: str | None = None) -> str | None:
    """``https://raw.githubusercontent.com/OWNER/REPO/BRANCH/rel/path`` for a file in a git repo."""
    path = Path(path).resolve()
    cwd = path.parent
    root = _git(["rev-parse", "--show-toplevel"], cwd)
    remote = _git(["remote", "get-url", "origin"], cwd)
    if not root or not remote:
        return None
    m = GITHUB_RE.search(remote)
    if not m:
        return None
    if branch is None:
        branch = os.environ.get("DEMO_RECORDER_BRANCH") or _default_branch(cwd)
    rel = path.relative_to(Path(root).resolve()).as_posix()
    return f"https://raw.githubusercontent.com/{m['owner']}/{m['repo']}/{branch}/{rel}"


def _default_branch(cwd: Path) -> str:
    head = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd)
    if head and "/" in head:
        return head.split("/", 1)[1]
    return "main"


def markdown_snippet(spec: DemoSpec, result: EncodeResult) -> str:
    out = spec.output
    readme = out.get("readme")
    alt = (readme.get("alt") if isinstance(readme, dict) else None) or spec.data.get("title") or result.path.stem
    if out.get("url_base"):
        url = out["url_base"].rstrip("/") + "/" + result.path.name
    else:
        url = github_raw_url(result.path) or result.path.as_posix()
    if result.path.suffix.lower() == ".mp4":
        return f'<video src="{url}" controls muted loop width="100%"></video>'
    return f"![{alt}]({url})"


def update_readme_markers(readme: Path, name: str, snippet: str) -> bool:
    """Replace the text between ``<!-- demo:NAME -->`` and ``<!-- /demo:NAME -->``."""
    text = readme.read_text(encoding="utf-8")
    pat = re.compile(rf"(<!--\s*demo:{re.escape(name)}\s*-->)(.*?)(<!--\s*/demo:{re.escape(name)}\s*-->)", re.S)
    if not pat.search(text):
        return False
    new = pat.sub(lambda m: f"{m.group(1)}\n{snippet}\n{m.group(3)}", text)
    if new != text:
        readme.write_text(new, encoding="utf-8")
    return True


def publish(spec: DemoSpec, result: EncodeResult, *, markdown: bool = False, update_readme: bool = True) -> str:
    snippet = markdown_snippet(spec, result)
    out = spec.output
    if markdown or out.get("markdown"):
        print(f"\nMarkdown:\n{snippet}\n")
    readme = out.get("readme")
    if readme and update_readme:
        cfg = {"path": readme} if isinstance(readme, str) else dict(readme)
        path = Path(cfg["path"])
        path = path if path.is_absolute() else spec.base_dir / path
        name = cfg.get("name") or (spec.path.name.split(".")[0] if spec.path else result.path.stem)
        if not path.exists():
            print(f"README {path} not found; not updated")
        elif update_readme_markers(path, name, snippet):
            print(f"updated {path} (demo:{name})")
        else:
            print(f"add <!-- demo:{name} --><!-- /demo:{name} --> to {path} to embed the demo automatically")
    return snippet
