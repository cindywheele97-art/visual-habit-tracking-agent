"""Test configuration. Overrides tmp_path base to keep AF_UNIX paths short on macOS."""

from __future__ import annotations

import pytest


# macOS AF_UNIX socket paths are limited to 103 usable characters.
# pytest's default tmp_path under /private/var/folders/... often exceeds this.
# Redirecting to /tmp/gbt keeps paths well under the limit.
@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> pytest.Path:  # type: ignore[override]
    return tmp_path_factory.mktemp("t", numbered=True)


def pytest_configure(config: pytest.Config) -> None:  # type: ignore[name-defined]
    """Force a short basetemp so AF_UNIX socket paths stay under 104 bytes."""
    from pathlib import Path

    short_base = Path("/tmp/gbt")
    short_base.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(short_base)
