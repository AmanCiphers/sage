"""Hardline blocklist patterns (from approval.py) - NEVER bypassable, even in YOLO mode."""

import re

# Command position anchor - matches start of command or after command separators
_CMDPOS = (
    r'(?:^|[\n`]|\$\()'            # start position
    r'\s*'                          # optional whitespace
    r'(?:sudo\s+(?:-[^\s]+\s+)*)?'  # optional sudo with flags
    r'(?:env\s+(?:\w+=\S*\s+)*)?'   # optional env with VAR=VAL pairs
    r'(?:(?:exec|nohup|setsid|time)\s+)*'  # optional wrapper commands
    r'\s*'
)

# Destructive-path argument matcher for the rm hardline rules.
def _hardline_rm_path(path_alt: str, tail: str = r'(?:\s|$|[)`;|&])') -> str:
    return rf'(?:["\'](?:{path_alt})["\']|(?:{path_alt}){tail})'

# Protected system roots whose recursive deletion has no recovery path.
_HARDLINE_SYSTEM_DIRS = (
    r'/home|/home/\*|/root|/root/\*|/etc|/etc/\*|/usr|/usr/\*|'
    r'/var|/var/\*|/bin|/bin/\*|/sbin|/sbin/\*|/boot|/boot/\*|/lib|/lib/\*'
)

# ``rm`` matched anywhere rather than only at command position. A recursion
# that is path-prefixed (``/bin/rm``), chained (``cd / && rm -rf /``), or
# nested (``bash -c 'rm -rf /'``) is still a recursive delete of a system
# root; anchoring to command position let those degrade from ``deny`` to
# ``ask``. The basename boundary keeps ``charm``/``firm`` from matching.
_RM_FLAG_PREFIX = r'(?:^|[\s;&|(`\'"\[])(?:[^\s;&|]*/)?rm\s+(-[^\s]*\s+)*'

# Pre-compiled hardline patterns - NEVER bypassable
# Boundary ensures `/tmp/x` is not mistaken for the root `/`.
_BOUNDARY = r'(?=\s|$|[)"\'`;|&])'
HARDLINE_PATTERNS = [
    # rm recursive targeting root filesystem or protected roots.
    (
        _RM_FLAG_PREFIX
        + r'(?:/\*?|/(?:home|root|etc|usr|var|bin|sbin|boot|lib)(?:/[^\s]*)?)'
        + _BOUNDARY,
        "recursive delete of system directory",
    ),
    (
        _RM_FLAG_PREFIX
        + r'(?:~|\$\{?HOME\}?)(?:/?\*?|[^\s]*)?'
        + _BOUNDARY,
        "recursive delete of home directory",
    ),
    # Filesystem format
    (r'\bmkfs\b', "format filesystem"),
    # Raw block device overwrites (dd + redirection)
    (r'\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*', "dd to raw block device"),
    (r'>\s*/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*\b', "redirect to raw block device"),
    # Fork bomb (classic shell form)
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb"),
    # Kill every process on the system
    (r'\bkill\s+(-[^\s]*\s+)*-1\b', "kill all processes"),
    (r'\bpkill\s+-9\b', "force kill processes"),
    (r'\bkillall\s+-9\b', "killall -9"),
    # System shutdown / reboot — anchor to command position
    (_CMDPOS + r'(shutdown|reboot|halt|poweroff)\b', "system shutdown/reboot"),
    (_CMDPOS + r'init\s+[06]\b', "init 0/6 (shutdown/reboot)"),
    (_CMDPOS + r'systemctl\s+(poweroff|reboot|halt|kexec)\b', "systemctl poweroff/reboot"),
    (_CMDPOS + r'telinit\s+[06]\b', "telinit 0/6 (shutdown/reboot)"),
    # SQL DROP
    (r'\bDROP\s+(TABLE|DATABASE)\b', "SQL DROP"),
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),
    # System config overwrite
    (r'>\s*/etc/(passwd|shadow|sudoers|ssh/sshd_config|fstab)\b', "overwrite system config"),
    # Process kills
    (r'\bkill\s+-9\s+-1\b', "kill all processes"),
    (r'\bpkill\s+-9\b', "force kill processes"),
    # SQL destructive
    (r'\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)', "SQL DELETE without WHERE"),
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),
]

# Pre-compile all patterns
_RE_FLAGS = re.IGNORECASE | re.DOTALL
HARDLINE_PATTERNS_COMPILED = [
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), description)
    for pattern, description in HARDLINE_PATTERNS
]
