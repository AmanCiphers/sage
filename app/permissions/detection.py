"""Dangerous command/argument detection for SAGE.

Lightweight static pattern detection — no external dependencies.
Returns (is_dangerous, pattern_key, description).
"""

import re
from typing import Tuple


# Static dangerous patterns: (regex, pattern_key, description)
DANGEROUS_PATTERNS = [
    # Privilege escalation
    (r"\bsudo\b", "sudo", "sudo privilege escalation"),
    (r"\bdoas\b", "doas", "doas privilege escalation"),
    (r"\bsu\s", "su", "user switching"),
    
    # Destructive filesystem operations
    (r"\brm\s+.*-rf\b", "rm_rf", "recursive force delete"),
    (r"\brm\s+.*-r\s+.*-f\b", "rm_rf", "recursive force delete (alt)"),
    (r"\brm\s+.*-f\s+.*-r\b", "rm_rf", "recursive force delete (alt)"),
    (r"\bmv\s+/dev/null", "mv_to_null", "move to null device"),
    (r"\bdd\s+.*of=/dev/", "dd_to_device", "raw disk write"),
    (r"\bmkfs\b", "mkfs", "filesystem format"),
    (r"\bfdisk\b", "fdisk", "partition modification"),
    (r"\bparted\b", "parted", "partition modification"),
    
    # Credential/secret access
    (r"(cat|cp|mv|rsync)\s+.*\.env\b", "env_file", "environment file access"),
    (r"(cat|cp|mv|rsync)\s+.*\.ssh/", "ssh_keys", "SSH key access"),
    (r"(cat|cp|mv|rsync)\s+.*id_rsa\b", "private_key", "private SSH key access"),
    (r"(cat|cp|mv|rsync)\s+.*id_ed25519\b", "private_key", "private SSH key access"),
    (r"(cat|cp|mv|rsync)\s+.*\.pem\b", "pem_file", "PEM certificate/key access"),
    (r"(cat|cp|mv|rsync)\s+.*\.key\b", "key_file", "key file access"),
    (r"(cat|cp|mv|rsync)\s+.*\.crt\b", "cert_file", "certificate file access"),
    (r"(cat|cp|mv|rsync)\s+\.git-credentials\b", "git_credentials", "git credentials access"),
    (r"(cat|cp|mv|rsync)\s+\.npmrc\b", "npmrc", "npm registry credentials"),
    (r"(cat|cp|mv|rsync)\s+\.pypirc\b", "pypirc", "PyPI credentials"),
    (r"(cat|cp|mv|rsync)\s+\.docker/config\.json\b", "docker_config", "Docker registry credentials"),
    
    # Command injection / shell metacharacters in arguments
    (r";\s*(rm|mv|cp|dd|wget|curl|nc|ncat|bash|sh)\b", "injection_rm", "command injection attempt"),
    (r"\|\s*(bash|sh|sh -c)\b", "pipe_to_shell", "pipe to shell"),
    (r"`[^`]+`", "cmd_subst_backtick", "command substitution (backtick)"),
    (r"\$\([^)]+\)", "cmd_subst_dollar", "command substitution ($())"),
    (r">\s*/etc/(passwd|shadow|sudoers|hosts|ssh/sshd_config)", "write_system_config", "write to critical system config"),
    (r">\s*/boot/", "write_boot", "write to boot partition"),
    
    # Network exfiltration / remote execution
    (r"(curl|wget)\s+.*\|\s*(bash|sh|python|perl|node)\b", "curl_pipe_shell", "download and pipe to interpreter"),
    (r"(curl|wget)\s+.*-O\s+/tmp/", "download_to_tmp", "download to temp directory"),
    (r"(nc|ncat|netcat)\s+.*-e\s+/bin/", "netcat_exec", "netcat with command execution"),
    (r"(ssh|scp|rsync)\s+.*root@", "ssh_as_root", "SSH as root user"),
    
    # Permission changes
    (r"chmod\s+777\b", "chmod_777", "world-writable permission"),
    (r"chmod\s+\+x\s+/etc/", "chmod_etc", "executable system config"),
    (r"chown\s+root:", "chown_root", "change ownership to root"),
    
    # Process manipulation
    (r"kill\s+-9\s+1\b", "kill_init", "kill init process"),
    (r":\(\)\s*\{\s*:\|\:&\s*\}", "fork_bomb", "fork bomb detected"),
    
    # Package manager abuse
    (r"pip\s+install\s+.*--index-url\s+http://", "pip_insecure", "pip install from insecure index"),
    (r"npm\s+install\s+.*--registry\s+http://", "npm_insecure", "npm install from insecure registry"),
]


def detect_dangerous(command: str) -> Tuple[bool, str, str]:
    """
    Check if a command matches dangerous patterns.
    
    Returns:
        (is_dangerous, pattern_key, description)
    """
    if not command or not isinstance(command, str):
        return False, "", ""
    
    # Normalize whitespace for matching
    normalized = " ".join(command.split())
    
    for pattern, key, desc in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True, key, desc
    
    return False, "", ""


def detect_shell_metacharacters(command: str) -> bool:
    """Check if command contains shell metacharacters that could indicate injection."""
    if not command or not isinstance(command, str):
        return False
    
    # Shell metacharacters that indicate complex shell operations
    metachars = [';', '|', '&', '`', '$(', '>', '<', '||', '&&']
    return any(mc in command for mc in metachars)


def is_read_only_command(command: str) -> bool:
    """
    Check if a command is on the read-only allowlist.
    These are simple, safe commands that never need approval.
    """
    if not command or not isinstance(command, str):
        return False
    
    # Normalize
    parts = command.strip().split()
    if not parts:
        return False
    
    # Allowed read-only commands (first token only, no args with metacharacters)
    READ_ONLY_COMMANDS = {
        'ls', 'cat', 'head', 'tail', 'less', 'more', 'grep', 'egrep', 'fgrep',
        'find', 'stat', 'file', 'which', 'whereis', 'type',
        'pwd', 'whoami', 'id', 'date', 'uptime', 'df', 'du', 'free',
        'ps', 'top', 'htop', 'netstat', 'ss', 'lsof',
        'git', 'git status', 'git log', 'git diff', 'git show', 'git branch',
        'python', 'python3', 'python -c', 'node', 'node -e',
    }
    
    first_token = parts[0]
    
    # Check exact match or prefix (e.g., "git status" matches "git")
    for allowed in READ_ONLY_COMMANDS:
        if command.startswith(allowed):
            # Additional safety: no shell metacharacters anywhere in command
            if detect_shell_metacharacters(command):
                return False
            return True
    
    return False