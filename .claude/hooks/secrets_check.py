#!/usr/bin/env python3
"""PreToolUse hook: block any write containing a hardcoded API key pattern."""
import json
import re
import sys

PATTERNS = [
    r'AIza[0-9A-Za-z_-]{35}',
    r'sk-[a-zA-Z0-9]{48}',
    r'gsk_[a-zA-Z0-9]{52}',
]

input_data = json.loads(sys.stdin.read())
content = input_data.get("content", "") or input_data.get("new_string", "")

for pattern in PATTERNS:
    if re.search(pattern, content):
        print(
            json.dumps({
                "block": True,
                "message": "Hardcoded API key detected. Use os.getenv() instead."
            }),
            file=sys.stderr
        )
        sys.exit(2)

sys.exit(0)
