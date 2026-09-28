#!/usr/bin/env python3
"""One-time Codespaces helper to authorize Gmail API and print a refresh token.

Never commit credentials.json or the printed refresh token.
Required scope: gmail.send
"""
from __future__ import annotations

import argparse
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from google_auth_oauthlib.flow import Flow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", default="credentials.json")
    parser.add_argument(
        "--redirect-uri",
        default=os.environ.get("GMAIL_REDIRECT_URI", ""),
        help="Public Codespaces callback URL, e.g. https://...app.github.dev/",
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    credentials_path = Path(args.credentials).expanduser().resolve()
    if not credentials_path.exists():
        raise SystemExit(f"Credentials file not found: {credentials_path}")
    if not args.redirect_uri:
        raise SystemExit("Missing --redirect-uri (or GMAIL_REDIRECT_URI).")

    redirect_uri = args.redirect_uri.rstrip("/") + "/"

    flow = Flow.from_client_secrets_file(
        str(credentials_path),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    result = {}
    done = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            if params.get("state", [""])[0] != state:
                result["error"] = "OAuth state mismatch."
            elif "error" in params:
                result["error"] = params["error"][0]
            elif "code" not in params:
                result["error"] = "No authorization code received."
            else:
                result["code"] = params["code"][0]

            body = (
                "<html><body><h2>Google authorization received.</h2>"
                "<p>You can close this tab and return to Codespaces.</p>"
                "</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            done.set()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("0.0.0.0", args.port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("\nOpen this URL in your browser:\n")
    print(authorization_url)
    print("\nWaiting for Google to redirect to Codespaces...\n")

    try:
        if not done.wait(timeout=600):
            raise SystemExit("Timed out waiting for Google OAuth callback.")
    finally:
        server.shutdown()
        server.server_close()

    if "error" in result:
        raise SystemExit(f"OAuth failed: {result['error']}")

    flow.fetch_token(code=result["code"])
    creds = flow.credentials

    print("\n=== GITHUB SECRET VALUES ===")
    print(f"GMAIL_CLIENT_ID={flow.client_config['client_id']}")
    print(f"GMAIL_CLIENT_SECRET={flow.client_config['client_secret']}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("\nCopy these three values into GitHub Secrets. Do not commit them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
