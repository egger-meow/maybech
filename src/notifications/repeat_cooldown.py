"""Escalating cooldown gate for repeated identical notification content.

The first few consecutive sends of an identical message use the plain
notification cooldown; once the same message keeps recurring past that,
the gap before the next send doubles (capped) each time instead of firing
on a flat interval, so a persistent underlying error (e.g. a stuck OKX
credential) doesn't page the operator every cooldown tick indefinitely.
"""

from __future__ import annotations

import time


class RepeatCooldownTracker:
    """Tracks per-fingerprint send times and escalates the gap on repeats."""

    def __init__(
        self,
        cooldown_seconds: float,
        *,
        escalate_after: int = 3,
        max_seconds: float = 3600,
    ) -> None:
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.escalate_after = max(1, escalate_after)
        self.max_seconds = max(self.cooldown_seconds, max_seconds)
        self._sent_at: dict[str, float] = {}
        self._sent_count: dict[str, int] = {}
        self._muted: set[str] = set()

    def ready(self, fingerprint: str) -> bool:
        """Return whether `fingerprint` may send now. Does not record a send."""
        if fingerprint in self._muted:
            return False
        last_sent = self._sent_at.get(fingerprint)
        if last_sent is None:
            return True
        prior_sends = self._sent_count.get(fingerprint, 1)
        return time.monotonic() - last_sent >= self._gap_seconds(prior_sends)

    def record_sent(self, fingerprint: str) -> None:
        """Record that `fingerprint` was actually delivered just now."""
        self._sent_at[fingerprint] = time.monotonic()
        self._sent_count[fingerprint] = self._sent_count.get(fingerprint, 0) + 1

    def mute(self, fingerprint: str) -> None:
        """Permanently stop `fingerprint` from sending again until process restart."""
        self._muted.add(fingerprint)

    def _gap_seconds(self, prior_sends: int) -> float:
        if prior_sends < self.escalate_after:
            return self.cooldown_seconds
        exponent = prior_sends - self.escalate_after + 1
        return min(self.max_seconds, self.cooldown_seconds * (2**exponent))
