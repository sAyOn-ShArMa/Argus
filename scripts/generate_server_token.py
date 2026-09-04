"""Generate one high-entropy Argus server token without storing it."""

from __future__ import annotations

import argparse
import re
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an Argus bearer token.")
    parser.add_argument(
        "--env-name", default="ARGUS_SERVER_TOKEN", help="Environment variable name."
    )
    arguments = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", arguments.env_name):
        parser.error("--env-name must be an uppercase environment variable name.")
    token = secrets.token_urlsafe(32)
    print(f"Generated {arguments.env_name} (shown once; do not commit it):")
    print(token)
    print("\nFor this PowerShell window:")
    print(f"$env:{arguments.env_name} = '{token}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
