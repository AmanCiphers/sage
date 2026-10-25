"""Threat pattern detection for context files and untrusted content.

Adapted from Hermes' threat_patterns.py - scans for prompt injection,
promptware, C2 patterns, and other attacks in context files before
they enter the system prompt.

This is a DEFENSIVE layer - it does not prevent the model from being
attacked via other vectors, but it prevents known-bad patterns from
being directly injected into the system prompt via context files.
"""

import re
from enum import Enum
from typing import Literal


class ThreatScope(Enum):
    """Scope of threat scanning - determines which patterns are checked."""
    CONTEXT = "context"      # Context files (AGENTS.md, SOUL.md, etc.) - strict
    TOOL_RESULT = "tool_result"  # Tool outputs - moderate
    USER_INPUT = "user_input"    # Direct user input - permissive


# =============================================================================
# THREAT PATTERNS BY CATEGORY
# =============================================================================

# Classic prompt injection patterns
INJECTION_PATTERNS = [
    # Direct instruction override
    r"(?i)ignore\s+(?:previous|prior|above|all)\s+(?:instructions?|directions?|prompts?)",
    r"(?i)disregard\s+(?:previous|prior|above|all)\s+(?:instructions?|directions?|prompts?)",
    r"(?i)forget\s+(?:previous|prior|above|all)\s+(?:instructions?|directions?|prompts?)",
    r"(?i)new\s+(?:instructions?|directions?|prompts?)\s*:",
    r"(?i)override\s+(?:previous|prior|above|all)\s+(?:instructions?|directions?|prompts?)",
    
    # Role manipulation
    r"(?i)you\s+are\s+(?:now|henceforth)\s+(?:a|an)\s+\w+",
    r"(?i)act\s+as\s+(?:a|an)\s+\w+",
    r"(?i)pretend\s+to\s+be\s+(?:a|an)\s+\w+",
    r"(?i)roleplay\s+as\s+(?:a|an)\s+\w+",
    r"(?i)simulate\s+(?:a|an)\s+\w+",
    
    # System prompt extraction
    r"(?i)what\s+(?:is|are)\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"(?i)show\s+me\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"(?i)repeat\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"(?i)print\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"(?i)output\s+(?:your|the)\s+(?:system\s+)?prompt",
    
    # Instruction repetition
    r"(?i)repeat\s+(?:the\s+)?(?:above|previous|prior)\s+(?:instructions?|text|prompt)",
    r"(?i)say\s+(?:the\s+)?(?:above|previous|prior)\s+(?:instructions?|text|prompt)",
    
    # Jailbreak attempts
    r"(?i)DAN\s+(?:mode|prompt)",
    r"(?i)developer\s+mode",
    r"(?i)unrestricted\s+mode",
    r"(?i)no\s+restrictions",
    r"(?i)ignore\s+(?:all\s+)?(?:rules|guidelines|constraints|limits)",
]

# Promptware / C2 patterns (command and control via prompt)
PROMPTWARE_PATTERNS = [
    # Data exfiltration attempts
    r"(?i)send\s+(?:data|info|information)\s+to\s+(?:http|https|ftp|smtp)",
    r"(?i)post\s+(?:data|info|information)\s+to\s+(?:http|https|ftp|smtp)",
    r"(?i)exfiltrate\s+(?:data|info|information)",
    r"(?i)upload\s+(?:data|info|information|files?)\s+to",
    
    # Command execution
    r"(?i)execute\s+(?:command|code|script)\s*:",
    r"(?i)run\s+(?:command|code|script)\s*:",
    r"(?i)eval\s*\([^)]*\)",
    r"(?i)exec\s*\([^)]*\)",
    r"(?i)system\s*\([^)]*\)",
    r"(?i)subprocess\s*\.\s*(?:run|popen|call)\s*\(",
    
    # File system access
    r"(?i)read\s+(?:file|directory)\s*:",
    r"(?i)write\s+(?:file|directory)\s*:",
    r"(?i)delete\s+(?:file|directory)\s*:",
    r"(?i)list\s+(?:files?|directories?)\s*:",
    
    # Network operations
    r"(?i)connect\s+to\s+(?:http|https|ftp|ssh|telnet|tcp|udp)",
    r"(?i)download\s+(?:from|file)\s*:",
    r"(?i)curl\s+(?:-X|-H|-d|--data)",
    r"(?i)wget\s+",
    
    # Persistence / installation
    r"(?i)install\s+(?:package|software|malware|rootkit|backdoor)",
    r"(?i)add\s+(?:to\s+)?(?:cron|startup|init|systemd|service)",
    r"(?i)modify\s+(?:registry|config|configuration)\s*:",
]

# Role-play hijack patterns
ROLEPLAY_PATTERNS = [
    r"(?i)(?:let's|lets)\s+play\s+a\s+game",
    r"(?i)imagine\s+(?:you\s+are|a\s+world\s+where)",
    r"(?i)suppose\s+(?:you\s+are|that\s+you\s+are)",
    r"(?i)in\s+a\s+hypothetical\s+(?:scenario|situation|world)",
    r"(?i)for\s+(?:educational|research|testing)\s+purposes\s+only",
    r"(?i)just\s+between\s+(?:us|you\s+and\s+me)",
    r"(?i)off\s+the\s+record",
    r"(?i)confidentially",
    r"(?i)secretly",
]

# Strict-scope patterns (only for CONTEXT scope - too aggressive for tool results)
STRICT_PATTERNS = [
    # SSH backdoor
    r"(?i)ssh\s+(?:key|authorized_keys|config)\s*[=:]\s*['\"]?ssh-(?:rsa|ed25519|ecdsa)",
    r"(?i)authorized_keys\s*[=:]\s*['\"]?ssh-",
    
    # Persistence mechanisms
    r"(?i)cron\s+(?:job|tab)\s*[=:]\s*['\"]?\s*\*",
    r"(?i)systemd\s+(?:service|timer|unit)\s*[=:]\s*['\"]?",
    r"(?i)rc\.local\s*[=:]\s*['\"]?",
    r"(?i)bashrc|zshrc|profile\s*[=:]\s*['\"]?",
    
    # Credential exfiltration
    r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[=:]\s*['\"][a-zA-Z0-9+/=]{20,}",
    r"(?i)aws[_-]?(?:access[_-]?key|secret[_-]?key)\s*[=:]\s*['\"][A-Z0-9]{20,}",
    r"(?i)github[_-]?(?:token|pat|key)\s*[=:]\s*['\"]?(?:gh[pousr]_)",
    
    # SSH/remote access
    r"(?i)reverse\s+shell",
    r"(?i)bind\s+shell",
    r"(?i)netcat\s+(?:-l|-e|-c)",
    r"(?i)nc\s+(?:-l|-e|-c)",
]


# =============================================================================
# PATTERN COMPILATION
# =============================================================================

# Pre-compile all patterns for performance
_COMPILED_INJECTION = [re.compile(p) for p in INJECTION_PATTERNS]
_COMPILED_PROMPTWARE = [re.compile(p) for p in PROMPTWARE_PATTERNS]
_COMPILED_ROLEPLAY = [re.compile(p) for p in ROLEPLAY_PATTERNS]
_COMPILED_STRICT = [re.compile(p) for p in STRICT_PATTERNS]


# =============================================================================
# PUBLIC API
# =============================================================================

def scan_for_threats(
    content: str,
    scope: Literal["context", "tool_result", "user_input"] = "context"
) -> list[str]:
    """Scan content for threat patterns.
    
    Args:
        content: Text content to scan
        scope: Scanning scope - determines which pattern sets to use
        
    Returns:
        List of matched threat category names (empty if clean)
    """
    if not content:
        return []
    
    findings = []
    
    # Always check injection patterns
    for pattern in _COMPILED_INJECTION:
        if pattern.search(content):
            findings.append("injection")
            break
    
    # Always check promptware patterns
    for pattern in _COMPILED_PROMPTWARE:
        if pattern.search(content):
            findings.append("promptware")
            break
    
    # Always check roleplay patterns
    for pattern in _COMPILED_ROLEPLAY:
        if pattern.search(content):
            findings.append("roleplay_hijack")
            break
    
    # Strict patterns only for context scope
    if scope == "context":
        for pattern in _COMPILED_STRICT:
            if pattern.search(content):
                findings.append("strict_violation")
                break
    
    return findings


def is_safe_content(
    content: str,
    scope: Literal["context", "tool_result", "user_input"] = "context"
) -> bool:
    """Quick check if content appears safe (no threats found)."""
    return len(scan_for_threats(content, scope)) == 0