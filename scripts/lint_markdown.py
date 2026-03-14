#!/usr/bin/env python3
import subprocess
from pathlib import Path


MARKDOWN_FILES = [
    Path("README.md"),
    Path("docs/AGENTS.md"),
    Path("docs/OPENBRAIN_NEXT_STEPS.md"),
    Path("docs/OPENBRAIN_ARCHITECTURE.md"),
]

RULESET_PATH = Path(".pymarkdownlnt.json")


def run_pymarkdown_lint() -> int:
    markdown_paths = [str(path) for path in MARKDOWN_FILES]
    commands = [
        ["python", "-m", "pymarkdownlnt", "scan", "--config", str(RULESET_PATH), *markdown_paths],
        [
            "python",
            "-m",
            "pymarkdown",
            "--config",
            str(RULESET_PATH),
            "--disable-rules",
            "MD025,MD022,MD029,MD013",
            "scan",
            *markdown_paths,
        ],
        [
            "pymarkdown",
            "--config",
            str(RULESET_PATH),
            "--disable-rules",
            "MD025,MD022,MD029,MD013",
            "scan",
            *markdown_paths,
        ],
    ]

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"pymarkdown command failed to execute: {command[0]} ({exc})")
            continue

        if result.returncode == 0:
            return 0

        if "No module named" in (result.stderr or ""):
            continue

        print(result.stdout, end="")
        print(result.stderr, end="")
        return result.returncode

    print("pymarkdownlnt not available. Falling back to project markdown checks.")
    return -1


def fallback_markdown_checks() -> int:
    issues = []

    for path in MARKDOWN_FILES:
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            issues.append((path.as_posix(), 0, "Missing trailing newline"))

        for idx, raw_line in enumerate(text.splitlines(True), 1):
            if raw_line.endswith(" \n") or raw_line.endswith("\t\n"):
                issues.append((path.as_posix(), idx, "Trailing whitespace"))

        headings = []
        for idx, line in enumerate(text.splitlines(), 1):
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                if 1 <= level <= 6:
                    headings.append((idx, level))

        for i in range(1, len(headings)):
            if headings[i][1] > headings[i - 1][1] + 1:
                issues.append(
                    (path.as_posix(), headings[i][0], "Heading level jump detected")
                )

    for path, line_no, message in issues:
        if line_no == 0:
            print(f"{path}: {message}")
        else:
            print(f"{path}:{line_no}: {message}")

    return 1 if issues else 0


def main() -> int:
    result = run_pymarkdown_lint()

    if result == 0:
        return 0

    if result > 0:
        return result

    return fallback_markdown_checks()


if __name__ == "__main__":
    raise SystemExit(main())
