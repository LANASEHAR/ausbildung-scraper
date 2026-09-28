#!/usr/bin/env python3
"""One-time local helper to authorize Gmail API and print the refresh token.

Never commit credentials.json or the printed refresh token.
Required scope: gmail.send
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", default="credentials.json")
    args = parser.parse_args()

    credentials_path = Path(args.credentials).expanduser().resolve()
    if not credentials_path.exists():
        raise SystemExit(f"Credentials file not found: {credentials_path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

    print("\n=== GITHUB SECRET VALUES ===")
    print(f"GMAIL_CLIENT_ID={flow.client_config['client_id']}")
    print(f"GMAIL_CLIENT_SECRET={flow.client_config['client_secret']}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("\nCopy these three values into GitHub Secrets. Do not commit them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
