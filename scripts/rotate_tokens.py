#!/usr/bin/env python3
"""Bearer token rotation script for OpenBrain.

Generates new per-user bearer tokens, updates Vercel env vars via API,
and updates the local .env.local file. Prints manual steps for ChatGPT
Custom GPT config updates (no API available for those).

Rotation schedule: January 1st and July 4th annually.
Follow-up confirmation: one week after rotation date.

Future: deploy as a scheduled agent on K8s/Linux node so rotation
runs automatically and only the ChatGPT manual step remains.

Prerequisites — add to .env.local:
    VERCEL_API_TOKEN=<personal access token from vercel.com/account/tokens>
    VERCEL_PROJECT_ID=<from Vercel project Settings → General>
    VERCEL_TEAM_ID=<team slug or ID if using a Vercel team, omit if personal>

Usage:
    .venv/bin/python scripts/rotate_tokens.py           # dry run (default)
    .venv/bin/python scripts/rotate_tokens.py --apply   # actually rotate
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
except ImportError:
    pass

import os

ENV_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"
VERCEL_API = "https://api.vercel.com"
TOKEN_PREFIX = "opbr_"
TOKEN_BYTES = 32

# Owner strings — must match OPENBRAIN_TOKEN_OWNER_MAP values in use
OWNERS = [
    "mike.mcmahon67",
    "snapple01",
    "anneliesepaige",
]

# Friendly labels for the manual ChatGPT update instructions
GPT_LABELS = {
    "mike.mcmahon67": "Mike's Custom GPT",
    "snapple01":       "Beth's Custom GPT",
    "anneliesepaige":  "Annie's Custom GPT",
}


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES).replace("-", "").replace("_", "")[:TOKEN_BYTES]


def vercel_request(method: str, path: str, body: dict | None = None) -> dict:
    api_token = os.getenv("VERCEL_API_TOKEN")
    team_id   = os.getenv("VERCEL_TEAM_ID", "")

    if not api_token:
        print("ERROR: VERCEL_API_TOKEN is not set in .env.local")
        print("  Get one at: https://vercel.com/account/tokens")
        raise SystemExit(1)

    url = f"{VERCEL_API}{path}"
    if team_id:
        url += f"{'&' if '?' in url else '?'}teamId={team_id}"

    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type":  "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        print(f"ERROR: Vercel API {method} {path} → HTTP {exc.code}: {detail}")
        raise SystemExit(1)


def get_env_var_id(project_id: str, key: str) -> str | None:
    data = vercel_request("GET", f"/v9/projects/{project_id}/env")
    for env in data.get("envs", []):
        if env.get("key") == key:
            return env["id"]
    return None


def update_vercel_env(project_id: str, env_id: str, new_value: str) -> None:
    vercel_request(
        "PATCH",
        f"/v9/projects/{project_id}/env/{env_id}",
        {"value": new_value, "target": ["production", "preview", "development"]},
    )


def update_env_local(new_map: dict[str, str]) -> None:
    content = ENV_LOCAL.read_text()
    new_value = json.dumps(new_map)
    pattern = r"^OPENBRAIN_TOKEN_OWNER_MAP=.*$"
    new_line = f"OPENBRAIN_TOKEN_OWNER_MAP={new_value}"

    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\n{new_line}\n"

    ENV_LOCAL.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate OpenBrain bearer tokens.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rotate tokens (default is dry run)",
    )
    args = parser.parse_args()

    project_id = os.getenv("VERCEL_PROJECT_ID")
    if not project_id:
        print("ERROR: VERCEL_PROJECT_ID is not set in .env.local")
        print("  Find it at: Vercel dashboard → your project → Settings → General")
        raise SystemExit(1)

    # Generate new tokens
    new_map: dict[str, str] = {generate_token(): owner for owner in OWNERS}

    print("\nOpenBrain Token Rotation")
    print("=" * 60)
    print(f"Mode: {'APPLY (live rotation)' if args.apply else 'DRY RUN (no changes made)'}")
    print()

    print("New tokens to be set:")
    for token, owner in new_map.items():
        print(f"  {owner:<25}  {token}")
    print()

    if not args.apply:
        print("Run with --apply to execute the rotation.")
        print()
        return

    # Find the Vercel env var ID
    print("Fetching Vercel env var ID...")
    env_id = get_env_var_id(project_id, "OPENBRAIN_TOKEN_OWNER_MAP")
    if not env_id:
        print("ERROR: OPENBRAIN_TOKEN_OWNER_MAP not found in Vercel project env vars.")
        raise SystemExit(1)

    # Update Vercel
    print("Updating Vercel environment variable...")
    update_vercel_env(project_id, env_id, json.dumps(new_map))
    print("  Vercel updated.")

    # Update .env.local
    print("Updating .env.local...")
    update_env_local(new_map)
    print("  .env.local updated.")

    # Redeploy prompt
    print()
    print("Next step — trigger a Vercel redeploy to apply the new tokens:")
    print("  Option A: vercel --prod  (if Vercel CLI is installed)")
    print("  Option B: Vercel dashboard → your project → Deployments → Redeploy")
    print()

    # Manual ChatGPT steps
    print("Manual steps required — update each Custom GPT action config:")
    print("-" * 60)
    for token, owner in new_map.items():
        label = GPT_LABELS.get(owner, owner)
        print(f"\n  {label}")
        print(f"  ChatGPT → Configure → Actions → Authentication → Bearer token:")
        print(f"  {token}")
    print()
    print("Rotation complete. Confirm all three GPTs working within one week.")
    print()


if __name__ == "__main__":
    main()
