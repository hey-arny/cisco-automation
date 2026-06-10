#!/usr/bin/env python3

import importlib.util
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOSTS_FILE = os.path.join(SCRIPT_DIR, "hosts.txt")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "results-post-upgrade-check.txt")
SHARED_SCRIPT = os.path.join(SCRIPT_DIR, "upgrade-checks.py")


def load_run_check():
    spec = importlib.util.spec_from_file_location("upgrade_checks", SHARED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_check


def main():
    run_check = load_run_check()
    return run_check("Run IOS post-upgrade checks.", HOSTS_FILE, OUTPUT_FILE)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(130)
