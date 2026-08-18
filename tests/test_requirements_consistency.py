"""Guard: requirements-dev.txt must not drift from requirements.txt.

The dev file exists so CI can install a torch-free subset in seconds instead of
minutes. Duplicating pins is the price of that speed, and duplicated pins are
exactly what the comment at the top of requirements.txt warns about: a version
that moves in one place and not the other is a difference between what CI proves
and what anyone actually installs.

These tests make the duplication safe. A bump to one file without the other
fails here, loudly, in the same suite CI already runs.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "requirements.txt"
DEV = ROOT / "requirements-dev.txt"


def _pins(path: Path) -> dict[str, str]:
    """Map requirement name (extras included) -> pinned version.

    Tolerates inline comments and blank lines. Anything starting with '-' is a
    pip flag or an include, not a requirement, so it is skipped.
    """
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name, sep, version = line.partition("==")
        assert sep, f"{path.name}: requirement is not pinned with '==': {line!r}"
        pins[name.strip()] = version.strip()
    return pins


def test_dev_pins_match_base_pins() -> None:
    """Every shared requirement is pinned to the same version in both files."""
    base, dev = _pins(BASE), _pins(DEV)
    mismatched = {
        name: (dev[name], base[name])
        for name in dev
        if name in base and dev[name] != base[name]
    }
    assert not mismatched, (
        "requirements-dev.txt has drifted from requirements.txt "
        f"(name: dev != base): {mismatched}. Bump both together."
    )


def test_dev_is_a_subset_of_base() -> None:
    """The dev file adds nothing of its own; it only narrows the real set.

    A package here but not in requirements.txt means CI would be testing against
    something the deployed image never installs.
    """
    base, dev = _pins(BASE), _pins(DEV)
    unknown = sorted(set(dev) - set(base))
    assert not unknown, (
        f"requirements-dev.txt lists packages absent from requirements.txt: {unknown}. "
        "Add them to requirements.txt, or drop them here."
    )
