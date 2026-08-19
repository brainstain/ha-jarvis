"""Provider registry — returns the configured CalendarProvider.

CALENDAR_PROVIDER env var selects the backend:
  "ha"  (default) — Home Assistant calendar REST API

Adding a new provider:
  1. Implement the CalendarProvider protocol in a new module here.
  2. Add it to the ``_PROVIDERS`` dict below.
  3. Set CALENDAR_PROVIDER=<key> in the environment.
"""

from __future__ import annotations

import os
from typing import Any

from .base import CalendarProvider  # noqa: F401 — re-exported for type hints
from .ha import HACalendarProvider

_PROVIDERS: dict[str, Any] = {
    "ha": HACalendarProvider,
}

_DEFAULT = "ha"


def get_provider() -> CalendarProvider:
    key = os.environ.get("CALENDAR_PROVIDER", _DEFAULT).lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown CALENDAR_PROVIDER={key!r}. Available: {sorted(_PROVIDERS)}"
        )
    return cls()
