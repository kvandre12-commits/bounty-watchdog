"""termux-notification wrapper -- the "Android advantage" seam.

Falls back to stdout when off-Android (CI, tests, a laptop) so the rest of
the codebase never has to special-case the environment. A missing/failing
notifier must never break the watch loop -- notifications are a courtesy,
not a dependency.
"""

from __future__ import annotations

import shutil
import subprocess


def notify(title: str, content: str, *, url: str | None = None) -> bool:
    """Fire a phone notification. Returns True if termux-notification ran.

    If ``url`` is given, tapping the notification opens it via termux-open-url.
    """
    if shutil.which("termux-notification") is None:
        print(f"[notify] {title}: {content}" + (f" ({url})" if url else ""))
        return False
    args = ["termux-notification", "--title", title, "--content", content]
    if url:
        args += ["--action", f"termux-open-url '{url}'"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
