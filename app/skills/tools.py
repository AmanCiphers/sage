"""Skill management tools for SAGE.

Provides:
- skill_manage: create, update, delete, list skills
- skill_view: view a skill's content and metadata
- skill_index: search and list skills
"""

import json
import os
from typing import Optional

from app.config import settings
from app.skills.store import SkillStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput


class SkillManageInput(ToolInput):
    action: str  # "create" | "update" | "delete" | "list"
    skill_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[list[str]] = None


class SkillViewInput(ToolInput):
    skill_id: str
    version: Optional[int] = None


class SkillIndexInput(ToolInput):
    query: str = ""
    tag: Optional[str] = None


def _resolve_skill_store() -> 'SkillStore':
    """Get the skill store (global or per-employee)."""
    from app.config import settings
    from app.tools.workspace import current_employee_key
    from pathlib import Path

    key = None
    try:
        from app.tools.workspace import current_employee_key
        key = current_employee_key()
    except Exception:
        pass
    
    if key:
        root = Path(settings.database_path).parent / "employees" / key
        return SkillStore(root / "skills.jsonl")
    
    return SkillStore(Path(settings.database_path).parent / "skills.jsonl")


def register_skill_tools(registry) -> None:
    from app.skills.store import SkillStore
    
    def skill_manage(action: str, skill_id: Optional[str] = None,
                     name: Optional[str] = None, description: Optional[str] = None,
                     content: Optional[str] = None, author: Optional[str] = None,
                     tags: Optional[list[str]] = None) -> str:
        store = _resolve_skill_store()
        
        if action == "create":
            if not name or not content:
                return json.dumps({"error": "name and content are required for create"})
            result = store.create_skill(
                name=name,
                description=description or "",
                content=content,
                author=author or "",
                tags=tags or [],
            )
            return json.dumps(result)
        
        elif action == "update":
            if not skill_id:
                return json.dumps({"error": "skill_id required for update"})
            result = store.update_skill(
                skill_id=skill_id,
                content=content,
                description=description,
                author=author or "",
                tags=tags,
            )
            return json.dumps(result)
        
        elif action == "delete":
            if not skill_id:
                return json.dumps({"error": "skill_id required for delete"})
            result = store.delete_skill(skill_id)
            return json.dumps(result)
        
        elif action == "list":
            skills = store.list_skills()
            return json.dumps({"skills": skills})
        
        else:
            return json.dumps({"error": f"unknown action: {action}"})

    def skill_view(skill_id: str, version: Optional[int] = None) -> str:
        store = _resolve_skill_store()
        
        if version is not None:
            versions = store.get_skill_versions(skill_id)
            matching = [v for v in v if v.get("version") == version]
            if not matching:
                return json.dumps({"error": f"Version {version} not found for skill {skill_id}"})
            skill = matching[0]
        else:
            skill = store.get_skill(skill_id)
        
        if not skill:
            return json.dumps({"error": f"Skill not found: {skill_id}"})
        
        return json.dumps(skill, indent=2)

    def skill_index(query: str = "", tag: Optional[str] = None) -> str:
        store = _resolve_skill_store()
        
        if tag:
            # Filter by tag after search
            skills = store.search_skills(query)
            skills = [s for s in skills if tag in s.get("tags", [])]
        else:
            skills = store.search_skills(query)
        
        # Return summary
        results = []
        for skill in skills:
            results.append({
                "skill_id": skill.get("skill_id"),
                "name": skill.get("name"),
                "description": skill.get("description", "")[:100],
                "version": skill.get("version"),
                "tags": skill.get("tags", []),
                "updated_at": skill.get("updated_at"),
            })
        
        return json.dumps({"results": results, "count": len(results)})

    registry.register(
        ToolDefinition(
            name="skill_manage",
            description=(
                "Manage skills: create, update, delete, list. "
                "Actions: create (name, description, content, author, tags), "
                "update (skill_id, content, description, author, tags), "
                "delete (skill_id), list."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=SkillManageInput,
            destructive=False,
            employee_eligible=True,
            handler=skill_manage,
        )
    )

    registry.register(
        ToolDefinition(
            name="skill_view",
            description="View a skill's content and metadata. Optionally specify version.",
            classification=ToolClassification.READ_ONLY,
            input_schema=SkillViewInput,
            destructive=False,
            employee_eligible=True,
            handler=skill_view,
        )
    )

    registry.register(
        ToolDefinition(
            name="skill_index",
            description="Search and list skills. Optional query and tag filter.",
            classification=ToolClassification.READ_ONLY,
            input_schema=SkillIndexInput,
            destructive=False,
            employee_eligible=True,
            handler=skill_index,
        )
    )