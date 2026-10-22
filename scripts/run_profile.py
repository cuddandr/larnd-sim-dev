#!/usr/bin/env python3
import argparse
import os
import random
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

def count_visible_gpus():
    """Number of GPUs usable by this process (respects CUDA_VISIBLE_DEVICES)."""
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cvd is not None:
        # Empty string means no GPUs visible.
        ids = [d for d in cvd.split(",") if d.strip()]
        return len(ids)
    # No CUDA_VISIBLE_DEVICES: count all host GPUs.
    try:
        out = subprocess.run(["nvidia-smi", "--list-gpus"],
                             capture_output=True, text=True, check=True).stdout
        return len([l for l in out.splitlines() if l.strip()])
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: could not determine GPU count (no CUDA_VISIBLE_DEVICES, nvidia-smi failed).", file=sys.stderr)
        sys.exit(1)

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
    parser.add_argument("-n", "--num", type=int, default=1,
                        help="Number of concurrent simulation copies to run")
    parser.add_argument("-s", "--seed", default=None, type=int,
                        help="Random seed. Use -s or -s 0 to auto-generate.")
    parser.add_argument("--reinstall", action="store_true", help="Run 'pip install -e .' after checkout")



    args = parser.parse_args()

    if args.num < 1:
        print("Error: --num must be >= 1.", file=sys.stderr)
        sys.exit(1)

    base_seed = None
    if args.seed is not None:
        base_seed = args.seed if args.seed > 0 else random.randint(0, 2**31 -1)

    num_gpus = count_visible_gpus()
    if args.num > num_gpus:
        print(f"Error: requested {args.num} copies but only {num_gpus} GPU(s) visible.", file=sys.stderr)
        sys.exit(1)

    # Ensure code can only run inside venv
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

        basename_prefix = args.output if args.output else f"{config_name}_{args.tool}_{git_ref_short}_{timestamp}"

        # Build lar_runner.py command
        cmd_prefix = ["lar_runner.py"]
        if args.dry_run:
            cmd_prefix.append("-d")
        if args.force:
            cmd_prefix.append("-f")
        cmd_prefix.extend(["-y", args.config_yaml])

        subcommand = "run" if args.tool == "run" else args.tool # keep if the name changes later
        cmd_prefix.append(subcommand)

        cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
        procs = []
        log_files = []
        for i in range(args.num):
            # Suffix each copy's name only when running more than one copy,
            basename = basename_prefix if args.num == 1 else f"{basename_prefix}_{i+1:02d}"
            log_path = args.log if args.log else f"{basename}.log"
            if args.log and args.num > 1:
                log_path = f"{Path(args.log).stem}_{i+1:02d}{Path(args.log).suffix}"

            # Pin each copy to its own GPU
            env = None
            if args.num > 1:
                env = os.environ.copy()
                if cvd is not None:
                    # Parent already restricted devices: pick the i-th of the visible ones.
                    visible = [d.strip() for d in cvd.split(",") if d.strip()]
                    env["CUDA_VISIBLE_DEVICES"] = visible[i]
                else:
                    env["CUDA_VISIBLE_DEVICES"] = str(i)

            cmd = cmd_prefix + []
            if base_seed is not None:
                cmd.extend(["-s", str(base_seed + i)])
            cmd.extend(["-o", basename])

            print(f"Executing: {' '.join(cmd)}", flush=True)
            lf = open(log_path, "w")
            log_files.append(lf)
            procs.append(subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, text=True, env=env))

        failed = []
        for (p, lf) in zip(procs, log_files):
            rc = p.wait()
            lf.close()
            if rc != 0:
                failed.append(rc)

        for rc in failed:
            print(f"A command copy failed with return code {rc}. See its log for details.", file=sys.stderr)

    finally:
        # Return to original Git reference if changed
        if original_ref and args.ref:
            subprocess.run(["git", "-C", repo_path, "checkout", original_ref], check=False)

if __name__ == "__main__":
    run_profile()
