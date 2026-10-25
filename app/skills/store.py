"""Skill store: JSON-lines persistence for skills with versioning.

Each skill has:
- skill_id: unique identifier
- name: human-readable name
- description: what the skill does
- content: the actual skill content (prompt/markdown)
- version: integer version number
- created_at: timestamp
- updated_at: timestamp
- author: who created/updated
- tags: list of tags for categorization
- version_history: list of previous versions
"""

import json
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class SkillEntry:
    """A single skill entry with versioning."""
    skill_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    description: str = ""
    content: str = ""
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    author: str = ""
    tags: List[str] = field(default_factory=list)
    version_history: List[dict] = field(default_factory=list)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> 'SkillEntry':
        data = json.loads(line)
        return SkillEntry(**data)


class SkillStore:
    """Skill store with JSON-lines persistence and versioning."""
    
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def _load_raw(self) -> List[dict]:
        """Load all skill entries from JSONL file."""
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Failed to parse skill line: %s", line[:100])
        return entries

    def _write_raw(self, entries: List[dict]) -> None:
        """Write all skill entries to JSONL file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _get_entries(self) -> List[dict]:
        return self._load_raw()

    def _save_entries(self, entries: List[dict]) -> None:
        self._write_raw(entries)

    def list_skills(self) -> List[dict]:
        """List all skills (latest version only)."""
        entries = self._get_entries()
        # Keep only latest version of each skill
        latest = {}
        for e in entries:
            sid = e.get("skill_id")
            if sid and (sid not in latest or e.get("version", 0) > latest[sid].get("version", 0)):
                latest[sid] = e
        return list(latest.values())

    def get_skill(self, skill_id: str) -> Optional[dict]:
        """Get latest version of a skill by ID."""
        entries = self._get_entries()
        matching = [e for e in entries if e.get("skill_id") == skill_id]
        if not matching:
            return None
        return max(matching, key=lambda x: x.get("version", 0))

    def get_skill_versions(self, skill_id: str) -> List[dict]:
        """Get all versions of a skill."""
        entries = self._get_entries()
        return [e for e in entries if e.get("skill_id") == skill_id]

    def create_skill(self, name: str, description: str, content: str, 
                     author: str = "", tags: List[str] = None) -> dict:
        """Create a new skill (version 1)."""
        entries = self._get_entries()
        
        # Check for duplicate name
        if any(e.get("name", "").lower() == name.lower() for e in entries):
            return {"error": f"Skill with name '{name}' already exists"}
        
        skill = {
            "skill_id": uuid.uuid4().hex[:8],
            "name": name,
            "description": description,
            "content": content,
            "version": 1,
            "created_at": time.time(),
            "updated_at": time.time(),
            "author": author,
            "tags": tags or [],
            "version_history": [],
        }
        
        entries.append(skill)
        self._save_entries(entries)
        return {"result": f"Created skill '{name}' (id: {skill['skill_id']})", "skill": skill}

    def update_skill(self, skill_id: str, content: str = None, 
                     description: str = None, author: str = "",
                     tags: List[str] = None) -> dict:
        """Update a skill, creating a new version."""
        entries = self._get_entries()
        skill = self.get_skill(skill_id)
        if not skill:
            return {"error": f"Skill not found: {skill_id}"}
        
        # Archive current version
        history_entry = {
            "version": skill["version"],
            "content": skill["content"],
            "description": skill.get("description", ""),
            "updated_at": skill["updated_at"],
            "author": skill.get("author", ""),
        }
        
        version_history = skill.get("version_history", [])
        version_history.append(history_entry)
        
        # Create new version
        new_version = skill["version"] + 1
        new_skill = skill.copy()
        new_skill["version"] = new_version
        new_skill["updated_at"] = time.time()
        if content is not None:
            new_skill["content"] = content
        if description is not None:
            new_skill["description"] = description
        if author:
            new_skill["author"] = author
        if tags is not None:
            new_skill["tags"] = tags
        new_skill["version_history"] = version_history
        
        # Replace in entries
        entries = [e for e in entries if not (e.get("skill_id") == skill_id and e.get("version") == skill["version"])]
        entries.append(new_skill)
        
        self._save_entries(entries)
        return {"result": f"Updated skill '{skill['name']}' to version {new_version}", "skill": new_skill}

    def get_skill_latest(self, skill_id: str) -> Optional[dict]:
        """Get latest version of a skill by ID."""
        entries = self._get_entries()
        matching = [e for e in entries if e.get("skill_id") == skill_id]
        if not matching:
            return None
        return max(matching, key=lambda x: x.get("version", 0))

    def list_skills(self) -> List[dict]:
        """List all skills (latest version only)."""
        entries = self._get_entries()
        # Keep only latest version of each skill
        latest = {}
        for e in entries:
            sid = e.get("skill_id")
            if sid and (sid not in latest or e.get("version", 0) > latest[sid].get("version", 0)):
                latest[sid] = e
        return list(latest.values())

    def delete_skill(self, skill_id: str) -> dict:
        """Delete a skill entirely (all versions)."""
        entries = self._get_entries()
        original_len = len(entries)
        entries = [e for e in entries if e.get("skill_id") != skill_id]
        if len(entries) == original_len:
            return {"error": f"Skill not found: {skill_id}"}
        self._save_entries(entries)
        return {"result": f"Deleted skill {skill_id}"}

    def search_skills(self, query: str) -> List[dict]:
        """Search skills by name, description, or tags."""
        entries = self.list_skills()
        query = query.lower()
        results = []
        for skill in entries:
            if (query in skill.get("name", "").lower() or
                query in skill.get("description", "").lower() or
                any(query in tag.lower() for tag in skill.get("tags", []))):
                results.append(skill)
        return results

    def get_skill_versions(self, skill_id: str) -> List[dict]:
        entries = self._get_entries()
        return [e for e in entries if e.get("skill_id") == skill_id]