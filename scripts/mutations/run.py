"""Error injection (manual mutation testing): applies one mutation at a time, runs the
tests that should catch it, and restores the file. A mutation "survives" when every test
still passes: either a test is missing or the mutated code is defended by another layer.

Usage, from the repository root (see scripts/mutations/README.md):

    python3 scripts/mutations/run.py --list
    python3 scripts/mutations/run.py bookings documents mcp secrets ui-unit
    python3 scripts/mutations/run.py ui-e2e     # needs a running stack and a Vite dev server

Safety: refuses to touch a file with uncommitted changes, and always restores the original
content (also on Ctrl+C). Exit code 1 if any mutation survived or no longer applies.
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from suites import SUITES  # noqa: E402


def dirty(path: str) -> bool:
    result = subprocess.run(["git", "diff", "--quiet", "--", path], cwd=ROOT)
    return result.returncode != 0


def summary_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines()
             if ("passed" in line or "failed" in line) and "Duration" not in line]
    return " | ".join(lines[-2:]) if lines else "no output"


def edits_of(mutation: tuple) -> tuple[list[tuple[str, str, str]], str]:
    """A mutation is (path, old, new, label), or (((path, old, new), ...), label) when it
    must change several places at once (e.g. removing two layers of the same guarantee)."""
    if len(mutation) == 2:
        edits, label = mutation
        return list(edits), label
    path, old, new, label = mutation
    return [(path, old, new)], label


def killed(returncode: int) -> bool:
    return returncode != 0


def run_suite(name: str, only: str | None = None) -> list[tuple[str, str, str]]:
    suite = SUITES[name]
    env = {**suite.get("env", {}), **os.environ}  # the caller's environment wins
    cwd = ROOT / suite.get("cwd", ".")
    results = []
    print(f"\n### {name}: {suite['description']}", flush=True)
    for mutation in suite["mutations"]:
        edits, label = edits_of(mutation)
        if only and only not in label:
            continue
        for path, _, _ in edits:
            if dirty(path):
                sys.exit(f"{path} has uncommitted changes; commit or stash them first")
        originals = {path: (ROOT / path).read_text() for path, _, _ in edits}
        if any(originals[path].count(old) != 1 for path, old, _ in edits):
            print(f"== {label}: PATTERN NOT FOUND", flush=True)
            results.append((name, label, "pattern"))
            continue
        try:
            for path, old, new in edits:  # every edit of one mutation applies together
                target = ROOT / path
                target.write_text(target.read_text().replace(old, new))
            time.sleep(suite.get("settle_seconds", 0))  # e.g. let Vite reload the change
            proc = subprocess.run(suite["command"], cwd=cwd, env=env, capture_output=True,
                                  text=True)
            outcome = "killed" if killed(proc.returncode) else "survived"
            print(f"== {label}: {'KILLED' if outcome == 'killed' else 'SURVIVED'} "
                  f"({summary_line(proc.stdout + proc.stderr)})", flush=True)
            results.append((name, label, outcome))
        finally:
            for path, original in originals.items():
                (ROOT / path).write_text(original)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suites", nargs="*", help="suites to run (default: all except ui-e2e)")
    parser.add_argument("--list", action="store_true", help="list suites and mutations")
    parser.add_argument("--only", help="run only mutations whose label contains this text")
    args = parser.parse_args()
    if args.list:
        for name, suite in SUITES.items():
            print(f"{name}: {suite['description']} ({len(suite['mutations'])} mutations)")
            for *_, label in suite["mutations"]:
                print(f"  - {label}")
        return
    names = args.suites or [n for n in SUITES if n != "ui-e2e"]
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        sys.exit(f"unknown suites: {unknown}; use --list")
    results = [r for name in names for r in run_suite(name, args.only)]
    survived = [r for r in results if r[2] != "killed"]
    print(f"\nTotal: {len(results)} mutations, {len(results) - len(survived)} killed, "
          f"{len(survived)} survived or not applied.")
    for name, label, outcome in survived:
        reason = "survived" if outcome == "survived" else "pattern not found"
        print(f"  - [{name}] {label}: {reason}")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
