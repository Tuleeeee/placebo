"""Find the skills each coding agent can see.

Skill locations differ per agent and change often. They are kept in one table
(`AGENT_SKILL_DIRS`) so they are easy to correct. Please send a PR if an agent
moved its directories.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from placebo_cli.skills.model import Skill, Visibility, parse_skill

# (scope, path). "~" = user home, "." = project root.
AGENT_SKILL_DIRS: dict[str, list[tuple[str, str]]] = {
    "claude-code": [("user", "~/.claude/skills"), ("project", "./.claude/skills")],
    "codex": [
        ("user", "~/.codex/skills"),
        ("user", "~/.agents/skills"),
        ("project", "./.agents/skills"),
        ("project", "./.codex/skills"),
    ],
    "gemini-cli": [("user", "~/.gemini/skills"), ("project", "./.gemini/skills")],
    "opencode": [("user", "~/.config/opencode/skills"), ("project", "./.opencode/skills")],
    "cursor": [("user", "~/.cursor/skills"), ("project", "./.cursor/skills")],
    "copilot": [("user", "~/.copilot/skills"), ("project", "./.github/skills")],
    "pi": [("user", "~/.pi/agent/skills"), ("project", "./.pi/skills")],
}

AGENT_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "gemini-cli": "Gemini CLI",
    "opencode": "OpenCode",
    "cursor": "Cursor",
    "copilot": "Copilot",
    "pi": "Pi",
    "path": "Path",
}


def home_dir() -> Path:
    # PLACEBO_HOME lets tests (and unusual setups) relocate the home directory.
    override = os.environ.get("PLACEBO_HOME")
    return Path(override) if override else Path.home()


def _expand(p: str, project: Path) -> Path:
    if p.startswith("~/"):
        return home_dir() / p[2:]
    if p.startswith("./"):
        return project / p[2:]
    return Path(p)


@dataclass
class Location:
    agent: str
    scope: str
    path: Path
    detail: str = ""


def claude_plugin_skill_dirs() -> list[tuple[Path, str]]:
    """Skill directories of *enabled* Claude Code plugins: [(dir, plugin_id)]."""
    base = home_dir() / ".claude"
    installed = base / "plugins" / "installed_plugins.json"
    if not installed.exists():
        return []
    try:
        data = json.loads(installed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    enabled: dict = {}
    settings = base / "settings.json"
    if settings.exists():
        try:
            enabled = json.loads(settings.read_text(encoding="utf-8")).get("enabledPlugins") or {}
        except (OSError, json.JSONDecodeError):
            enabled = {}
    plugins = data.get("plugins", data) if isinstance(data, dict) else {}
    out: list[tuple[Path, str]] = []
    for plugin_id, entries in plugins.items():
        if not isinstance(plugin_id, str):
            continue
        if enabled and enabled.get(plugin_id) is False:
            continue
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("installPath"):
                continue
            root = Path(entry["installPath"])
            skill_roots = [root / "skills"]
            manifest = root / ".claude-plugin" / "plugin.json"
            if manifest.exists():
                try:
                    declared = json.loads(manifest.read_text(encoding="utf-8")).get("skills")
                except (OSError, json.JSONDecodeError):
                    declared = None
                if isinstance(declared, str):
                    declared = [declared]
                if isinstance(declared, list):
                    skill_roots += [root / str(d) for d in declared]
            for sr in skill_roots:
                if sr.is_dir():
                    out.append((sr, plugin_id))
    return out


def agent_locations(project: Path, agents: list[str] | None = None) -> list[Location]:
    locs: list[Location] = []
    for agent, dirs in AGENT_SKILL_DIRS.items():
        if agents and agent not in agents:
            continue
        for scope, raw in dirs:
            locs.append(Location(agent, scope, _expand(raw, project)))
        if agent == "claude-code":
            for d, pid in claude_plugin_skill_dirs():
                locs.append(Location(agent, "plugin", d, pid))
    return locs


def _skill_files_in(dir_: Path) -> list[Path]:
    """SKILL.md files one level below `dir_` (the standard layout)."""
    if not dir_.is_dir():
        return []
    files = []
    try:
        children = sorted(dir_.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_dir():
            md = child / "SKILL.md"
            if md.is_file():
                files.append(md)
    return files


def find_skill_files_recursive(root: Path, max_files: int = 5000) -> list[Path]:
    """All SKILL.md files under `root` (for scanning a skills repo)."""
    out = []
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", ".placebo", "dist", "build"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if fn == "SKILL.md":
                out.append(Path(dirpath) / fn)
                if len(out) >= max_files:
                    return out
    return sorted(out)


def _key(p: Path) -> str:
    try:
        return str(p.resolve()).lower() if os.name == "nt" else str(p.resolve())
    except OSError:
        return str(p)


def discover_installed(project: Path, agents: list[str] | None = None) -> tuple[list[Skill], list[Location]]:
    """Skills visible to each agent from the user's home and the project."""
    locations = agent_locations(project, agents)
    by_path: dict[str, Skill] = {}
    for loc in locations:
        for md in _skill_files_in(loc.path):
            k = _key(md)
            skill = by_path.get(k)
            if skill is None:
                skill = parse_skill(md)
                by_path[k] = skill
            vis = Visibility(loc.agent, loc.scope, loc.detail)
            if not any(v.agent == vis.agent and v.scope == vis.scope and v.detail == vis.detail for v in skill.visibility):
                skill.visibility.append(vis)
    return list(by_path.values()), [l for l in locations if l.path.is_dir()]


def discover_path(root: Path) -> list[Skill]:
    """Every SKILL.md below a directory (e.g. a skills repository)."""
    skills = []
    if root.is_file() and root.name == "SKILL.md":
        files = [root]
    else:
        files = find_skill_files_recursive(root)
    for md in files:
        s = parse_skill(md)
        s.visibility.append(Visibility("path", "path", str(root)))
        skills.append(s)
    return skills
