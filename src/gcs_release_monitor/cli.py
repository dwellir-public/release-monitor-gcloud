from __future__ import annotations

import argparse
import logging

from .config import ConfigError, load_config
from .monitor import MonitorService


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor GCS bucket releases and mirror to Nextcloud")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument("--once", action="store_true", help="Run exactly one polling cycle")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover, download, and select artifacts without uploading, webhooking, or writing state",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="[%(levelname)s] %(asctime)s %(name)s - %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logging.error("Config validation failed: %s", exc)
        return 2
    if args.dry_run and not args.once:
        logging.error("--dry-run requires --once to avoid infinite no-op loops")
        return 2

    service = MonitorService(config)
    try:
        if args.once:
            service.run_once(dry_run=args.dry_run)
        else:
            service.run_forever(dry_run=False)
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
