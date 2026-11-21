"""Template syntax contract (owner A, spec §3).

Templates use `{{path}}` placeholders. They never execute code: no Python `format`,
no `eval`, no attribute access on arbitrary objects. `libs/conditions/template.py`
implements rendering; this module defines the syntax and the placeholder regex that
B and C use for their own validation.

Syntax:

    {{path}}          substitute the value at `path`
    {{ $live }}       reserved `$`-prefixed fields (see `contracts/letter.py`)

Path syntax: see `contracts/letter.md`.
Missing path -> empty string. `None` -> empty string. Lists -> comma-separated.
Anything else -> `str(value)`.

Live fields (`{{$count}}`, `{{$state}}`, `{{$alert_url}}`) are rendered at display or
send time; everything else is rendered once from the first event when the alert is
created. Which template is rendered when is a delivery concern (`contracts/delivery.md`),
not a processor one.
"""

from __future__ import annotations

import re

# Matches `{{ ... }}` with optional surrounding whitespace.
# Captures the path between the braces, trimmed. Does not validate the path itself -
# path validation is the evaluator's job.
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# Maximum length of a single rendered template. Prevents memory blow-up from a rule
# with a pathological `{{payload.x}}` on a huge raw payload.
MAX_RENDERED_LENGTH = 64 * 1024
