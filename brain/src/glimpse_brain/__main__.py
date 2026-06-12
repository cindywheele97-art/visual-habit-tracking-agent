"""CLI entry: python -m glimpse_brain [--config path/to/glimpse.toml]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from glimpse_brain.config import load_config
from glimpse_brain.server import GlimpseServer


def main() -> None:
    parser = argparse.ArgumentParser(prog="glimpse-brain")
    parser.add_argument("--config", type=Path, default=None, help="path to glimpse.toml")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set — refusing to start.")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    cfg = load_config(args.config)
    try:
        asyncio.run(GlimpseServer(cfg).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
