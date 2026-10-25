"""Memory v2 with provenance, evolution, forgetting."""
from __future__ import annotations
import json
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

@dataclass
class MemoryEntry:
    text: str
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_msg_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    confidence: float = 1.0
    explicit: bool = True
    status: str = "active"  # active, superseded, forgotten
    supersedes: Optional[str] = None  # entry_id of superseded entry

class MemoryStoreV2:
    def __init__(self, path: Path, max_chars: int = 4000) -> None:
        self.path = path
        self.max_chars = max_chars

    def _load_raw(self) -> List[MemoryEntry]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line=line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(MemoryEntry(**data))
            except json.JSONDecodeError:
                # legacy line
                txt = line.lstrip("- ").strip()
                entries.append(MemoryEntry(text=txt))
        return entries

    def _write_raw(self, entries: List[MemoryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(asdict(e), ensure_ascii=False) for e in entries]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def load(self) -> str:
        entries = self._load_raw()
        active = [e for e in entries if e.status == "active"]
        return "\n".join(f"- {e.text}" for e in active) + ("\n" if active else "")

    def entries(self) -> List[MemoryEntry]:
        return [e for e in self._load_raw() if e.status == "active"]

    def all_entries(self) -> List[MemoryEntry]:
        return self._load_raw()

    def _render_for_prompt(self, entries: List[MemoryEntry]) -> str:
        return "\n".join(f"- {e.text}" for e in entries) + ("\n" if entries else "")

    def _total_chars(self, entries: List[MemoryEntry]) -> int:
        return len(self._render_for_prompt(entries))

    def add(self, text: str, source_msg_id: Optional[str]=None, confidence: float=1.0, explicit: bool=True) -> str:
        fact = text.strip().lstrip("- ").strip()
        if not fact:
            return "Error: empty memory entry."
        entries = self._load_raw()
        # check duplicate active
        if any(e.text.lower() == fact.lower() and e.status=="active" for e in entries):
            return "Already in memory."
        # supersede any contradictory active entries (simple heuristic: same keywords)
        # For simplicity, we just add new entry
        entry = MemoryEntry(
            text=fact,
            source_msg_id=source_msg_id,
            confidence=confidence,
            explicit=explicit,
        )
        entries.append(entry)
        # enforce size limit
        while self._total_chars([e for e in entries if e.status=="active"]) > self.max_chars:
            # remove oldest active
            oldest = next((e for e in entries if e.status=="active"), None)
            if oldest:
                oldest.status = "forgotten"
            else:
                break
        self._write_raw(entries)
        return f"Remembered: {fact}"

    def remove(self, text: str) -> str:
        needle = text.strip().lower()
        entries = self._load_raw()
        removed = 0
        for e in entries:
            if e.status=="active" and needle in e.text.lower():
                e.status = "forgotten"
                removed += 1
        if removed==0:
            return f"No memory entry matches: {text}"
        self._write_raw(entries)
        return f"Removed {removed} entr{'y' if removed==1 else 'ies'}."

    def forget(self, text: str) -> str:
        # alias for remove
        return self.remove(text)

    def supersede(self, old_text: str, new_text: str, source_msg_id: Optional[str]=None) -> str:
        entries = self._load_raw()
        for e in entries:
            if e.status=="active" and e.text.lower() == old_text.strip().lower():
                e.status = "superseded"
                new_entry = MemoryEntry(
                    text=new_text.strip().lstrip("- ").strip(),
                    source_msg_id=source_msg_id,
                    confidence=1.0,
                    explicit=True,
                    supersedes=e.entry_id,
                )
                entries.append(new_entry)
                self._write_raw(entries)
                return f"Superseded: {old_text} -> {new_text}"
        return f"No active entry matches: {old_text}"

    def status(self) -> dict:
        entries = self._load_raw()
        active = [e for e in entries if e.status=="active"]
        used = sum(len(e.text)+2 for e in active)
        return {
            "used_chars": used,
            "max_chars": self.max_chars,
            "free_chars": self.max_chars - used,
            "percent_used": round(used/self.max_chars*100,1) if self.max_chars else 0,
            "entry_count": len(active),
            "entries": sorted([{"text":e.text,"chars":len(e.text)+2,"id":e.entry_id,"status":e.status} for e in active],
                               key=lambda x: -x["chars"])
        }

    def clear(self) -> str:
        self._write_raw([])
        return "Memory cleared."