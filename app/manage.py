from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Tuple

import yaml

from lib.auth.bootstrap import AdminBootstrapper
from lib.config import AppConfig
from lib.data.db import get_session
from lib.setup import initialize_environment


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_config(path: Path) -> Tuple[AppConfig, dict]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config_data = yaml.safe_load(handle)
    return initialize_environment(config_data, base_dir=path.parent)


def cmd_bootstrap_admin(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser().resolve()
    _app_config, _resources = _load_config(config_path)
    session = get_session()
    try:
        bootstrapper = AdminBootstrapper(session)
        password = args.password
        if not password:
            password = getpass.getpass(prompt="Temporary admin password: ")
        user_id = bootstrapper.ensure_admin(args.email, password=password)
        print(f"Admin user ready (id={user_id}, email={args.email})")
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="BirdSong management utility")
    subparsers = parser.add_subparsers(dest="command")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-admin",
        help="Create or update the initial admin user",
    )
    bootstrap_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config.yaml (default: backend/app/config.yaml)",
    )
    bootstrap_parser.add_argument(
        "--email",
        required=True,
        help="Admin email address",
    )
    bootstrap_parser.add_argument(
        "--password",
        help="Optional temporary password (prompted if omitted)",
    )
    bootstrap_parser.set_defaults(func=cmd_bootstrap_admin)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        parser.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
