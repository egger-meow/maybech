from unittest.mock import patch

from src.notifications.repeat_cooldown import RepeatCooldownTracker


def _at(tracker: RepeatCooldownTracker, fingerprint: str, monotonic_time: float) -> bool:
    """Check readiness at a given monotonic time and record the send if allowed."""
    with patch("src.notifications.repeat_cooldown.time.monotonic", return_value=monotonic_time):
        ready = tracker.ready(fingerprint)
        if ready:
            tracker.record_sent(fingerprint)
    return ready


def test_first_three_repeats_use_the_plain_cooldown():
    tracker = RepeatCooldownTracker(100)

    assert _at(tracker, "x", 0) is True
    assert _at(tracker, "x", 99) is False
    assert _at(tracker, "x", 100) is True
    assert _at(tracker, "x", 199) is False
    assert _at(tracker, "x", 200) is True


def test_gap_formula_escalates_from_the_fourth_repeat_and_caps():
    tracker = RepeatCooldownTracker(100, max_seconds=1000)

    assert tracker._gap_seconds(0) == 100
    assert tracker._gap_seconds(1) == 100
    assert tracker._gap_seconds(2) == 100
    assert tracker._gap_seconds(3) == 200
    assert tracker._gap_seconds(4) == 400
    assert tracker._gap_seconds(5) == 800
    assert tracker._gap_seconds(6) == 1000  # would be 1600 uncapped
    assert tracker._gap_seconds(20) == 1000


def test_fourth_repeat_requires_the_escalated_gap_not_the_plain_cooldown():
    tracker = RepeatCooldownTracker(100, max_seconds=1000)
    assert _at(tracker, "x", 0) is True
    assert _at(tracker, "x", 100) is True
    assert _at(tracker, "x", 200) is True  # 3 sends in a row at the plain cooldown

    # A 4th send needs a 200s gap (100 * 2) since the last one, not 100s.
    assert _at(tracker, "x", 200 + 100) is False
    assert _at(tracker, "x", 200 + 200) is True


def test_unrelated_fingerprints_do_not_affect_each_others_cooldown():
    tracker = RepeatCooldownTracker(300)
    assert _at(tracker, "a", 0) is True
    assert _at(tracker, "b", 0) is True
    assert _at(tracker, "b", 1) is False
    assert _at(tracker, "a", 1) is False
