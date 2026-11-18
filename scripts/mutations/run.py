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
    return " | ".join(lines[-2:]) if lines else "sin salida"


def killed(returncode: int) -> bool:
    return returncode != 0


def run_suite(name: str, only: str | None = None) -> list[tuple[str, str, str]]:
    suite = SUITES[name]
    env = {**suite.get("env", {}), **os.environ}  # the caller's environment wins
    cwd = ROOT / suite.get("cwd", ".")
    results = []
    print(f"\n### {name}: {suite['description']}", flush=True)
    for path, old, new, label in suite["mutations"]:
        if only and only not in label:
            continue
        target = ROOT / path
        if dirty(path):
            sys.exit(f"{path} has uncommitted changes; commit or stash them first")
        original = target.read_text()
        if original.count(old) != 1:
            print(f"== {label}: PATRÓN NO ENCONTRADO", flush=True)
            results.append((name, label, "pattern"))
            continue
        try:
            target.write_text(original.replace(old, new))
            time.sleep(suite.get("settle_seconds", 0))  # e.g. let Vite reload the change
            proc = subprocess.run(suite["command"], cwd=cwd, env=env, capture_output=True,
                                  text=True)
            outcome = "killed" if killed(proc.returncode) else "survived"
            print(f"== {label}: {'DETECTADA' if outcome == 'killed' else 'SOBREVIVE'} "
                  f"({summary_line(proc.stdout + proc.stderr)})", flush=True)
            results.append((name, label, outcome))
        finally:
            target.write_text(original)
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
            print(f"{name}: {suite['description']} ({len(suite['mutations'])} mutaciones)")
            for *_, label in suite["mutations"]:
                print(f"  - {label}")
        return
    names = args.suites or [n for n in SUITES if n != "ui-e2e"]
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        sys.exit(f"unknown suites: {unknown}; use --list")
    results = [r for name in names for r in run_suite(name, args.only)]
    survived = [r for r in results if r[2] != "killed"]
    print(f"\nTotal: {len(results)} mutaciones, {len(results) - len(survived)} detectadas, "
          f"{len(survived)} sin detectar o sin aplicar.")
    for name, label, outcome in survived:
        reason = "sobrevive" if outcome == "survived" else "patrón no encontrado"
        print(f"  - [{name}] {label}: {reason}")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
