"""Session + Permanent allowlist management for SAGE.

Simple two-tier allowlist:
- Session: in-memory, cleared on restart
- Permanent: persisted to config (.env array)
"""

import os
import threading
from pathlib import Path
from typing import Set


class AllowlistManager:
    """Manages session + permanent allowlists with config persistence."""
    
    def __init__(self, config_path: Path | None = None):
        self._lock = threading.Lock()
        self._session_allowed: Set[str] = set()
        
        # Config path for permanent allowlist
        self.config_path = config_path or Path(".env")
        self._permanent_allowed: Set[str] = set()
        self._load_permanent()
    
    def is_allowed(self, pattern_key: str) -> bool:
        """Check if a pattern key is allowed (session or permanent)."""
        if not pattern_key:
            return False
        with self._lock:
            return pattern_key in self._session_allowed or pattern_key in self._permanent_allowed
    
    def allow_session(self, pattern_key: str) -> None:
        """Allow pattern for current session only."""
        if not pattern_key:
            return
        with self._lock:
            self._session_allowed.add(pattern_key)
    
    def allow_permanent(self, pattern_key: str) -> None:
        """Allow pattern permanently (persisted to config)."""
        if not pattern_key:
            return
        with self._lock:
            self._permanent_allowed.add(pattern_key)
            self._session_allowed.discard(pattern_key)  # No need in session if permanent
            self._save_permanent()
    
    def revoke(self, pattern_key: str) -> bool:
        """Revoke a pattern from both session and permanent."""
        if not pattern_key:
            return False
        with self._lock:
            session_removed = self._session_allowed.discard(pattern_key)
            permanent_removed = self._permanent_allowed.discard(pattern_key)
            if permanent_removed:
                self._save_permanent()
            return True
    
    def list_session(self) -> list[str]:
        """List session-only allowed patterns."""
        with self._lock:
            return sorted(self._session_allowed - self._permanent_allowed)
    
    def list_permanent(self) -> list[str]:
        """List permanently allowed patterns."""
        with self._lock:
            return sorted(self._permanent_allowed)
    
    def list_all(self) -> list[str]:
        """List all allowed patterns."""
        with self._lock:
            return sorted(self._session_allowed | self._permanent_allowed)
    
    def _load_permanent(self) -> None:
        """Load permanent allowlist from .env file."""
        self._permanent_allowed = set()
        if not self.config_path.exists():
            return
        
        try:
            content = self.config_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("SAGE_ALLOWLIST="):
                    value = line[len("SAGE_ALLOWLIST="):].strip()
                    if value:
                        # Parse comma-separated list
                        patterns = [p.strip() for p in value.split(",") if p.strip()]
                        self._permanent_allowed.update(patterns)
        except Exception:
            # Silent fail - allowlist starts empty
            pass
    
    def _save_permanent(self) -> None:
        """Save permanent allowlist to .env file."""
        try:
            # Read current .env
            lines = []
            if self.config_path.exists():
                lines = self.config_path.read_text(encoding="utf-8").splitlines()
            
            # Filter out existing SAGE_ALLOWLIST lines
            new_lines = [line for line in lines if not line.strip().startswith("SAGE_ALLOWLIST=")]
            
            # Add new SAGE_ALLOWLIST line
            if self._permanent_allowed:
                allowlist_str = ",".join(sorted(self._permanent_allowed))
                new_lines.append(f"SAGE_ALLOWLIST={allowlist_str}")
            
            # Write back
            self.config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception:
            pass  # Silent fail
    
    def clear_session(self) -> None:
        """Clear session allowlist (on restart)."""
        with self._lock:
            self._session_allowed.clear()