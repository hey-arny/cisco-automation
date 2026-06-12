#!/usr/bin/env python3

import importlib.util
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOSTS_FILE = os.path.join(SCRIPT_DIR, "hosts.txt")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "results-post-upgrade-check.txt")
SHARED_SCRIPT = os.path.join(SCRIPT_DIR, "upgrade-checks.py")
COMMANDS_SCRIPT = os.path.join(SCRIPT_DIR, "upgrade-checks-commands.py")


def load_script(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_upgrade_checks():
    spec = importlib.util.spec_from_file_location("upgrade_checks", SHARED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    upgrade_checks = load_upgrade_checks()
    command_profile = load_script("default_checks", COMMANDS_SCRIPT)
    return upgrade_checks.run_check(
        "Run IOS post-upgrade checks.",
        HOSTS_FILE,
        OUTPUT_FILE,
        command_profile.COMMANDS,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(130)
