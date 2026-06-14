from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from glimpse_brain.config import Config, load_config


def test_defaults_when_no_file() -> None:
    cfg = load_config(None)
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.tracker.settle_ms == 800
    assert cfg.llm.max_calls_per_minute == 6
    # Paths must be expanded so the server never sees a literal "~".
    assert "~" not in cfg.brain.socket_path


def test_loads_and_overrides(tmp_path: Path) -> None:
    toml = tmp_path / "glimpse.toml"
    toml.write_text(
        '[tracker]\nsettle_ms = 100\n\n[llm]\nmodel = "claude-haiku-4-5-20251001"\n',
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.tracker.settle_ms == 100
    assert cfg.llm.model == "claude-haiku-4-5-20251001"
    # Untouched sections keep defaults.
    assert cfg.tracker.side_threshold == 0.5


def test_unknown_keys_fail_loud(tmp_path: Path) -> None:
    toml = tmp_path / "bad.toml"
    toml.write_text("[tracker]\nsettel_ms = 100\n", encoding="utf-8")  # typo
    with pytest.raises(ValidationError):
        load_config(toml)


def test_default_redaction_catches_cn_mobile() -> None:
    cfg = Config()
    import re

    assert any(re.search(p, "13812345678") for p in cfg.redaction.patterns)


def test_malformed_toml_names_the_file(tmp_path: Path) -> None:
    # Fail-loud rule: the operator must see WHICH file is broken.
    toml = tmp_path / "broken.toml"
    toml.write_text("[tracker\nsettle_ms = 1", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.toml"):
        load_config(toml)


def test_llm_cfg_has_max_iterations_default() -> None:
    from glimpse_brain.config import Config

    cfg = Config()
    assert cfg.llm.max_iterations == 4


def test_llm_cfg_max_iterations_overridable() -> None:
    from glimpse_brain.config import Config

    cfg = Config.model_validate({"llm": {"max_iterations": 6}})
    assert cfg.llm.max_iterations == 6
