#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

def get_git_short_ref(repo_path):
    """Returns the short hash of the current HEAD in the given repo path."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown"

def run_profile():
    parser = argparse.ArgumentParser(description="Profiling helper for larnd-sim")
    parser.add_argument("config_yaml", help="Path to configuration yaml")
    parser.add_argument("-r", "--ref", default=None, help="Git reference to checkout")
    parser.add_argument("-t", "--tool", choices=["run", "nsys", "ncu"], default="run", help="Profiling tool")
    parser.add_argument("-d", "--dry_run", action="store_true", help="Pass dry-run to internal CLI")
    parser.add_argument("-o", "--output", help="Custom output basename")
    parser.add_argument("-p", "--path", default="./larnd-sim", help="Path to repository")
    parser.add_argument("-l", "--log", help="Path to log file")
    parser.add_argument("-f", "--force", action="store_true", help="Pass force to internal CLI")
    parser.add_argument("--reinstall", action="store_true", help="Run 'pip install -e .' after checkout")

    args = parser.parse_args()

    # Check if inside virtual environment
    if "VIRTUAL_ENV" not in os.environ:
        print("Error: virtual environment not active.", file=sys.stderr)
        sys.exit(1)

    repo_path = os.path.abspath(args.path)
    if not os.path.isdir(repo_path):
        print(f"Error: Repository path {repo_path} not found.", file=sys.stderr)
        sys.exit(1)

    # Switch Git reference (and save original ref)
    original_ref = None
    try:
        original_ref = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        ).stdout.strip()

        # Use --no-advice here to stop 'detached HEAD' messages
        if args.ref:
            subprocess.run(["git", "--no-advice", "-C", repo_path, "checkout", args.ref], check=True)

        # Optionally reinstall package via pip (actually necessary if executable scripts are changed
        # e.g. cli/simulate_pixels.py)
        if args.reinstall:
            print(f"Reinstalling package in editable mode from {repo_path}...", flush=True)
            subprocess.run([sys.executable, "-m", "pip", "install", "-e", repo_path], check=True)

    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # Default naming scheme
        # {config}_{tool}_{git_ref_short}_{timestamp}
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        config_name = Path(args.config_yaml).stem
        git_ref_short = get_git_short_ref(repo_path)

        basename = args.output if args.output else f"{config_name}_{args.tool}_{git_ref_short}_{timestamp}"

        # Default log path
        log_path = args.log if args.log else f"{basename}.log"

        # Build lar_runner.py command
        cmd = ["lar_runner.py"]
        if args.dry_run:
            cmd.append("-d")
        if args.force:
            cmd.append("-f")
        cmd.extend(["-y", args.config_yaml])

        subcommand = "run" if args.tool == "run" else args.tool # keep if the name changes later
        cmd.append(subcommand)
        cmd.extend(["-o", basename])

        print(f"Executing: {' '.join(cmd)}", flush=True)

        with open(log_path, "w") as log_file:
            result = subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)
            if result.returncode != 0:
                print(f"Command failed with return code {result.returncode}. See {log_path} for details.", file=sys.stderr)

    finally:
        # Return to original Git reference
        if original_ref:
            subprocess.run(["git", "-C", repo_path, "checkout", original_ref], check=False)

if __name__ == "__main__":
    run_profile()
