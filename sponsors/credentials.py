"""Read explicitly configured private files without executing or logging their contents."""
import re
import shlex
from pathlib import Path


def read_key(path, name):
    raw = Path(path).expanduser().read_text().strip()
    assignments = []
    for line in raw.splitlines():
        match = re.match(r'(?:export\s+)?' + re.escape(name) + r'\s*=\s*(.*)', line.strip())
        if match:
            parts = shlex.split(match.group(1), comments=True)
            if len(parts) != 1:
                raise ValueError('Expected one credential value')
            assignments.append(parts[0])
    if assignments:
        if len(assignments) != 1:
            raise ValueError('Duplicate credential assignment')
        return assignments[0]
    prefix = {'XAI_API_KEY': 'xai-', 'FACTORY_API_KEY': 'fk-'}[name]
    matches = []
    for line in raw.splitlines():
        value = line.strip().strip('\"\'')
        if re.fullmatch(re.escape(prefix) + r'[A-Za-z0-9_-]+', value):
            matches.append(value)
    if len(matches) > 1:
        raise ValueError('Multiple credentials for the same provider')
    if matches:
        return matches[0]
    # Preserve support for a single raw key without a recognized prefix.
    if '\n' not in raw and '=' not in raw and raw and not raw.startswith(('xai-', 'fk-')):
        return raw.strip('\"\'')
    return None
