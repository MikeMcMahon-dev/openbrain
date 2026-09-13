#!/usr/bin/env python3
"""oauth_passphrase.py — mint the OPENBRAIN_OAUTH_PASSPHRASES entry for an owner.

The OAuth login (api/oauth.py) checks the passphrase against a PBKDF2 hash held in the
Vercel env var OPENBRAIN_OAUTH_PASSPHRASES, a JSON object {owner: hash}. This prints the
hash; the passphrase itself is read from a hidden prompt and never written anywhere.

    .venv/bin/python scripts/oauth_passphrase.py mike.mcmahon67
    .venv/bin/python scripts/oauth_passphrase.py anneliesepaige --merge-into '<current JSON>'

Paste the printed JSON into the Vercel env var (Production) and redeploy. To rotate a
passphrase, mint a new hash and replace that owner's entry. To lock an owner out of OAuth,
delete the entry — tokens already issued keep working until they expire (90 days) or the
signing secret is rotated.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.oauth_tokens import check_passphrase, hash_passphrase  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("owner", help="owner login, e.g. mike.mcmahon67 (must match the vault owner)")
    ap.add_argument("--merge-into", metavar="JSON",
                    help="existing OPENBRAIN_OAUTH_PASSPHRASES value to add/replace this owner in")
    args = ap.parse_args()

    first = getpass.getpass(f"Passphrase for {args.owner}: ")
    second = getpass.getpass("Again: ")
    if first != second:
        print("passphrases differ — nothing generated", file=sys.stderr)
        return 1
    if len(first) < 12:
        print("use at least 12 characters — nothing generated", file=sys.stderr)
        return 1

    digest = hash_passphrase(first)
    assert check_passphrase(first, digest), "self-check failed"

    current: dict[str, str] = {}
    if args.merge_into:
        try:
            current = json.loads(args.merge_into)
        except json.JSONDecodeError as exc:
            print(f"--merge-into is not valid JSON: {exc}", file=sys.stderr)
            return 1
    current[args.owner] = digest

    print("\nOPENBRAIN_OAUTH_PASSPHRASES=")
    print(json.dumps(current, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
