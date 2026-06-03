#!/usr/bin/env python3

from netmiko import ConnectHandler
from getpass import getpass
import os
import re
import time
import logging


# ============================================================
# SAFE IOS IMAGE CLEANUP SCRIPT
# ============================================================
#
# Purpose:
# - Connect to Cisco IOS devices.
# - Detect the currently booted system image.
# - List .bin files in the same storage, for example flash:.
# - Delete only extra .bin files.
#
# Safety rules:
# - NEVER delete the currently booted image.
# - NEVER delete images listed in PROTECTED_IMAGES.
# - Skip the device if the current image cannot be detected.
# - Skip the device if the current image is not visible in dir output.
# - By default DRY_RUN = True, so nothing is deleted until you change it.
#
# Tested logic target:
# - Cisco 800 / 900 classic IOS images like c800-...bin and c900-...bin
# - Python 3
# - Netmiko 3.4.0
# ============================================================


# -----------------------------
# USER SETTINGS
# -----------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOSTS_FILE = os.path.join(SCRIPT_DIR, "hosts.txt")
LOG_DIR = os.path.join(SCRIPT_DIR, "cleanup_logs")
SUMMARY_FILE = os.path.join(SCRIPT_DIR, "cleanup_summary.txt")

# IMPORTANT:
# First run with DRY_RUN = True.
# It will only show what WOULD be deleted.
# Change to False only after you verify the output.
DRY_RUN = False

# Extra safety:
# Only files starting with these prefixes are considered for deletion.
ALLOWED_IMAGE_PREFIXES = ("c800-", "c900-")

# Images that must NEVER be deleted, even if they are not currently booted.
# You can write only filename or full path. Both are accepted.
#
# Examples:
# "c800-universalk9-mz.SPA.159-3.M13.bin"
# "flash:c800-universalk9-mz.SPA.159-3.M13.bin"
#
PROTECTED_IMAGES = [
    "c800-universalk9-mz.SPA.159-3.M13.bin",
    "c900-universalk9-mz.SPA.159-3.M13.bin",
]


# -----------------------------
# LOGGING
# -----------------------------

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "cleanup_run.log")),
        logging.StreamHandler()
    ]
)

logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("netmiko").setLevel(logging.WARNING)


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------

def normalize_image_name(image):
    """
    Accepts either:
    c800-universalk9-mz.SPA.159-3.M13.bin
    or:
    flash:c800-universalk9-mz.SPA.159-3.M13.bin
    or:
    flash:/c800-universalk9-mz.SPA.159-3.M13.bin

    Returns only lowercase filename.
    """

    if not image:
        return ""

    image = image.strip().lower()

    if ":" in image:
        image = image.split(":", 1)[1]

    image = image.lstrip("/")

    return image


PROTECTED_IMAGES_NORMALIZED = set(
    normalize_image_name(image) for image in PROTECTED_IMAGES
)


def run_show(net_connect, command):
    return net_connect.send_command_timing(
        command,
        delay_factor=1,
        max_loops=300,
        strip_prompt=False,
        strip_command=False
    )


def get_current_image_path(net_connect):
    """
    Detect current system image path from show version.
    Example output:
    System image file is "flash:c800-universalk9-mz.SPA.157-3.M4a.bin"
    """

    output = run_show(net_connect, "show version | include System image file")

    match = re.search(r'System image file is "([^"]+)"', output)
    if not match:
        return None, output

    return match.group(1).strip(), output


def split_storage_and_filename(image_path):
    """
    Example:
    flash:c800-universalk9-mz.SPA.157-3.M4a.bin

    Returns:
    storage = flash:
    filename = c800-universalk9-mz.SPA.157-3.M4a.bin
    """

    if not image_path or ":" not in image_path:
        return None, None

    storage, filename = image_path.split(":", 1)
    storage = storage + ":"
    filename = filename.lstrip("/")

    if not filename:
        return None, None

    return storage, filename


def parse_bin_files_from_dir(dir_output):
    """
    Parses lines like:
    3  -rw-    95947248  May 19 2021 11:16:24 +00:00  c800-universalk9-mz.SPA.157-3.M4a.bin
    """

    bin_files = []

    for line in dir_output.splitlines():
        line = line.strip()

        if not line:
            continue

        match = re.search(r'(\S+\.bin)\s*$', line, re.IGNORECASE)
        if match:
            bin_files.append(match.group(1).strip())

    return bin_files


def is_allowed_image(filename):
    filename_lower = normalize_image_name(filename)
    return filename_lower.startswith(ALLOWED_IMAGE_PREFIXES)


def delete_file(net_connect, full_path):
    """
    Interactive delete.

    This handles common Cisco IOS prompts:
    delete flash:file.bin
    Delete filename [file.bin]?
    Delete flash:file.bin? [confirm]
    """

    output = ""

    output += net_connect.send_command_timing(
        "delete {}".format(full_path),
        delay_factor=1,
        max_loops=300,
        strip_prompt=False,
        strip_command=False
    )

    if "Delete filename" in output:
        output += net_connect.send_command_timing(
            "\n",
            delay_factor=1,
            max_loops=300,
            strip_prompt=False,
            strip_command=False
        )

    if "[confirm]" in output or "confirm" in output.lower():
        output += net_connect.send_command_timing(
            "\n",
            delay_factor=1,
            max_loops=300,
            strip_prompt=False,
            strip_command=False
        )

    return output


def add_summary_block(summary, lines):
    summary.append("\n".join(lines))


def read_hosts(path):
    hosts = []
    seen = set()

    with open(path, "r") as f:
        for line in f:
            host = line.strip()

            if not host or host.startswith("#") or host in seen:
                continue

            hosts.append(host)
            seen.add(host)

    return hosts


# -----------------------------
# MAIN SCRIPT
# -----------------------------

username = input("Enter username: ").strip()
password = getpass("Password: ")

summary = []

if not os.path.exists(HOSTS_FILE):
    print("{} does not exist.".format(HOSTS_FILE))
    raise SystemExit

logging.info("Starting IOS cleanup script")
logging.info("DRY_RUN mode: {}".format(DRY_RUN))
logging.info("Protected images: {}".format(", ".join(sorted(PROTECTED_IMAGES_NORMALIZED))))

hosts = read_hosts(HOSTS_FILE)

for ip in hosts:
    logging.info("Processing {}".format(ip))

    device = {
        "device_type": "cisco_ios",
        "host": ip,
        "username": username,
        "password": password,
        "port": 22,
        "fast_cli": False,
        "global_delay_factor": 2,
        "session_log": os.path.join(LOG_DIR, "netmiko_{}.log".format(ip)),
    }

    net_connect = None

    try:
        net_connect = ConnectHandler(**device)

        # ----------------------------------------------------
        # 1. Detect currently booted image
        # ----------------------------------------------------

        current_path, show_ver_output = get_current_image_path(net_connect)

        if not current_path:
            msg = "{} | SKIPPED | Could not detect current system image path".format(ip)
            logging.error(msg)
            summary.append(msg)
            continue

        storage, current_filename = split_storage_and_filename(current_path)

        if not storage or not current_filename:
            msg = "{} | SKIPPED | Invalid current image path: {}".format(ip, current_path)
            logging.error(msg)
            summary.append(msg)
            continue

        current_normalized = normalize_image_name(current_filename)

        if not current_normalized.endswith(".bin"):
            msg = "{} | SKIPPED | Current image is not .bin: {}".format(ip, current_path)
            logging.warning(msg)
            summary.append(msg)
            continue

        if not is_allowed_image(current_filename):
            msg = "{} | SKIPPED | Current image is not allowed c800/c900 image: {}".format(
                ip, current_filename
            )
            logging.warning(msg)
            summary.append(msg)
            continue

        logging.info("{} current system image path: {}".format(ip, current_path))
        logging.info("{} current image filename: {}".format(ip, current_filename))
        logging.info("{} storage: {}".format(ip, storage))

        # ----------------------------------------------------
        # 2. List .bin files from the same storage
        # ----------------------------------------------------

        dir_command = "dir {} | include .bin".format(storage)
        dir_output_before = run_show(net_connect, dir_command)
        bin_files = parse_bin_files_from_dir(dir_output_before)

        if not bin_files:
            msg = "{} | SKIPPED | No .bin files found in {}".format(ip, storage)
            logging.warning(msg)
            summary.append(msg)
            continue

        # ----------------------------------------------------
        # 3. Extra safety:
        #    current image must be visible in dir output
        # ----------------------------------------------------

        current_found = False

        for image in bin_files:
            if normalize_image_name(image) == current_normalized:
                current_found = True
                break

        if not current_found:
            msg = "{} | SKIPPED | Current image not found in dir output, refusing to delete anything".format(ip)
            logging.error(msg)

            add_summary_block(summary, [
                "Device {}".format(ip),
                "Status: SKIPPED",
                "Reason: Current image not found in dir output, refusing to delete anything",
                "Current system image path: {}".format(current_path),
                "",
                "Dir output:",
                dir_output_before,
                "-" * 80
            ])
            continue

        # ----------------------------------------------------
        # 4. Build delete list
        # ----------------------------------------------------

        keep_files = []
        delete_candidates = []

        for image in bin_files:
            image_normalized = normalize_image_name(image)

            # Safety 1: never delete currently booted image
            if image_normalized == current_normalized:
                keep_files.append("{}  <-- current booted image".format(image))
                logging.info("{} keeping current booted image: {}".format(ip, image))
                continue

            # Safety 2: never delete manually protected image
            if image_normalized in PROTECTED_IMAGES_NORMALIZED:
                keep_files.append("{}  <-- protected image".format(image))
                logging.info("{} keeping protected image: {}".format(ip, image))
                continue

            # Safety 3: only delete allowed Cisco 800/900 images
            if not is_allowed_image(image):
                keep_files.append("{}  <-- ignored, not allowed prefix".format(image))
                logging.warning("{} ignoring non c800/c900 .bin file: {}".format(ip, image))
                continue

            delete_candidates.append(image)

        if not delete_candidates:
            add_summary_block(summary, [
                "Device {}".format(ip),
                "Status: OK - nothing to delete",
                "Current system image path: {}".format(current_path),
                "",
                "Kept files:",
                "\n".join("- {}".format(item) for item in keep_files),
                "",
                "Dir output:",
                dir_output_before,
                "-" * 80
            ])

            logging.info("{} OK - no extra deletable c800/c900 .bin images found".format(ip))
            continue

        # ----------------------------------------------------
        # 5. Delete extra images or show what would be deleted
        # ----------------------------------------------------

        deleted_or_planned_files = []
        delete_outputs = []

        for image in delete_candidates:
            full_path = "{}{}".format(storage, image)

            if DRY_RUN:
                logging.info("{} DRY-RUN would delete: {}".format(ip, full_path))
                deleted_or_planned_files.append("DRY-RUN would delete {}".format(full_path))
                continue

            logging.info("{} deleting: {}".format(ip, full_path))
            delete_output = delete_file(net_connect, full_path)

            logging.info("{} delete output for {}:\n{}".format(ip, full_path, delete_output))

            deleted_or_planned_files.append("Deleted {}".format(full_path))
            delete_outputs.append("Delete output for {}:\n{}".format(full_path, delete_output))

            time.sleep(1)

        # ----------------------------------------------------
        # 6. Dir output after deletion / dry-run
        # ----------------------------------------------------

        dir_output_after = run_show(net_connect, dir_command)

        add_summary_block(summary, [
            "Device {}".format(ip),
            "Status: DRY-RUN completed" if DRY_RUN else "Status: cleanup completed",
            "Current system image path: {}".format(current_path),
            "",
            "Kept files:",
            "\n".join("- {}".format(item) for item in keep_files),
            "",
            "Deleted / planned files:",
            "\n".join("- {}".format(item) for item in deleted_or_planned_files),
            "",
            "Dir output before:",
            dir_output_before,
            "",
            "Dir output after:",
            dir_output_after,
            "",
            "\n".join(delete_outputs),
            "-" * 80
        ])

    except Exception as error:
        msg = "{} | FAILED | {}".format(ip, error)
        logging.error(msg)
        summary.append(msg)

    finally:
        if net_connect:
            net_connect.disconnect()


with open(SUMMARY_FILE, "w") as f:
    for item in summary:
        f.write(item)
        f.write("\n\n")

print("")
print("All done.")
print("Summary saved to: {}".format(SUMMARY_FILE))
print("Detailed logs saved in: {}".format(LOG_DIR))
print("DRY_RUN mode was: {}".format(DRY_RUN))
print("")
print("Important:")
print("- First check {}".format(SUMMARY_FILE))
print("- If the planned deletions are correct, change DRY_RUN = False and run again")
