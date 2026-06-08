#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

from netmiko import ConnectHandler
from netmiko import ConnectHandler, NetMikoTimeoutException, NetMikoAuthenticationException
from getpass import getpass
import os
import re
import time


# =========================
# SETTINGS
# =========================

HOSTS_FILE = "hosts.txt"
LOG_DIR = "logs"

# If True, and all conditions are passed --> script will reload the device! 
# with False boolean variable script works just as pre-check!  
RELOAD_ENABLED = False

# Supported boot maps
LATEST_BOOT = {
    # 800 models
    "C892FSP-K9": "boot system flash:c800-universalk9-mz.SPA.159-3.M13.bin",
    "C891FSP-K9": "boot system flash:c800-universalk9-mz.SPA.159-3.M13.bin",
    "C891F-K9":   "boot system flash:c800-universalk9-mz.SPA.159-3.M13.bin",
    "C891FJ-K9":  "boot system flash:c800-universalk9-mz.SPA.159-3.M13.bin",
    "C888EA-K9":  "boot system flash:c800-universalk9-mz.SPA.159-3.M13.bin",
    "C888-K9":    "boot system flash:c800-universalk9-mz.SPA.159-3.M13.bin",

    # 900 models
    "C921-4P":    "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C931-4P":    "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C927-4P":    "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C926-4P":    "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C927-4PM":   "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",

    # 900 LTE models
    "C921-4PLTEGB":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C921-4PLTEAU":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C921-4PLTEAS":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C927-4PLTENA":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C927-4PLTEGB":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C927-4PLTEAU":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C927-4PMLTEGB": "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
    "C926-4PLTEGB":  "boot system flash:c900-universalk9-mz.SPA.159-3.M13.bin",
}

# Expected byte size of each newest image in flash. 
LATEST_IMAGE_SIZE = {
    "c800-universalk9-mz.SPA.159-3.M13.bin": 97436536,
    "c900-universalk9-mz.SPA.159-3.M13.bin": 65946792,
}


# =========================
# SIMPLE HELPERS
# =========================

def make_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log_line(file_handle, text):
    line = "[%s] %s" % (timestamp(), text)
    print(line)
    file_handle.write(line + "\n")
    file_handle.flush()


def run_show(net_connect, command):
    return net_connect.send_command_timing(
        command,
        delay_factor=1,
        max_loops=300,
        strip_prompt=False,
        strip_command=False
    )


def get_pid(show_inventory):
    match = re.search(r"PID:\s*([^,\s]+)", show_inventory)
    if match:
        return match.group(1).strip().upper()
    return None


def get_current_image(show_version):
    match = re.search(r'System image file is "([^"]+)"', show_version)
    if match:
        return match.group(1).strip()
    return None


def get_boot_system_lines(show_boot):
    boot_lines = []

    for line in show_boot.splitlines():
        line = line.strip()

        if line.startswith("boot system "):
            boot_lines.append(line)

    return boot_lines


def get_boot_filename(boot_line):
    match = re.search(r"flash:(\S+)$", boot_line)
    if match:
        return match.group(1).strip()
    return None


def get_image_size(dir_output, image_filename):
    image_pattern = re.escape(image_filename)

    for line in dir_output.splitlines():
        if image_filename not in line:
            continue

        match = re.search(r"^\s*\d+\s+\S+\s+(\d+)\s+.*\s%s\s*$" % image_pattern, line)
        if match:
            return int(match.group(1))

    return None


def save_config(net_connect):
    # Stejne jako "do wr" z config modu, ale spoustime z enable modu.
    output = net_connect.send_command_timing("write memory", strip_prompt=False, strip_command=False)

    if "confirm" in output.lower() or "proceed" in output.lower():
        output += net_connect.send_command_timing("\n", strip_prompt=False, strip_command=False)

    return output


def reload_device(net_connect):
    output = net_connect.send_command_timing("reload", strip_prompt=False, strip_command=False)

    if "confirm" in output.lower() or "proceed" in output.lower():
        output += net_connect.send_command_timing("\n", strip_prompt=False, strip_command=False)
    elif "yes/no" in output.lower():
        output += net_connect.send_command_timing("yes", strip_prompt=False, strip_command=False)

    return output


def confirm_reload_enabled():
    if not RELOAD_ENABLED:
        return

    print("")
    print("WARNING: RELOAD_ENABLED is set to True.")
    print("Devices that pass all checks will be saved and reloaded automatically.")
    answer = input("Type YES to continue, or anything else to abort: ").strip()

    if answer.upper() != "YES":
        print("Aborted. No devices were connected.")
        raise SystemExit


# =========================
# MAIN SCRIPT
# =========================

confirm_reload_enabled()

username = input("Enter username: ").strip()
password = getpass("Password: ")

make_dir(LOG_DIR)
make_dir(os.path.join(LOG_DIR, "sessions"))

if not os.path.exists(HOSTS_FILE):
    open(HOSTS_FILE, "w").close()
    print("Created %s. Add device IP addresses and run the script again." % HOSTS_FILE)
    raise SystemExit

hosts = []

with open(HOSTS_FILE, "r") as f:
    for line in f:
        ip = line.strip()

        if ip and not ip.startswith("#"):
            hosts.append(ip)

if not hosts:
    print("ERROR: No hosts found in %s." % HOSTS_FILE)
    raise SystemExit


for ip in hosts:
    safe_ip = ip.replace("/", "_").replace(":", "_")
    device_log_path = os.path.join(LOG_DIR, "%s.log" % safe_ip)
    session_log_path = os.path.join(LOG_DIR, "sessions", "%s_session.log" % safe_ip)

    with open(device_log_path, "w") as log:
        log_line(log, "Starting device %s" % ip)
        log_line(log, "Reload enabled: %s" % RELOAD_ENABLED)

        device = {
            "device_type": "cisco_ios",
            "host": ip,
            "username": username,
            "password": password,
            "port": 22,
            "fast_cli": False,
            "global_delay_factor": 2,
            "session_log": session_log_path,
        }

        net_connect = None

        try:
            log_line(log, "Connecting to device...")
            net_connect = ConnectHandler(**device)
            log_line(log, "Connected successfully.")

            # 1) Detect PID
            show_inventory = run_show(net_connect, "show inventory")
            pid = get_pid(show_inventory)

            if not pid:
                log_line(log, "FAIL: Could not detect PID from show inventory.")
                log.write("\n--- show inventory ---\n%s\n" % show_inventory)
                continue

            log_line(log, "Detected PID: %s" % pid)

            if pid not in LATEST_BOOT:
                log_line(log, "FAIL: PID %s is not supported by this script." % pid)
                continue

            expected_new_boot = LATEST_BOOT[pid]
            log_line(log, "Expected newest boot line: %s" % expected_new_boot)

            # 2) Detect current running image
            show_version = run_show(net_connect, "show version | include System image file")
            current_image = get_current_image(show_version)

            if not current_image:
                log_line(log, "FAIL: Could not detect current image from show version.")
                log.write("\n--- show version output ---\n%s\n" % show_version)
                continue

            expected_current_boot = "boot system %s" % current_image
            log_line(log, "Current running image: %s" % current_image)
            log_line(log, "Expected second boot line: %s" % expected_current_boot)

            # 3) Read configured boot sequence
            show_boot = run_show(net_connect, "show running-config | section boot")
            boot_lines = get_boot_system_lines(show_boot)

            log.write("\n--- show running-config | section boot ---\n%s\n" % show_boot)
            log.write("\n--- detected boot system lines ---\n")
            for line in boot_lines:
                log.write(line + "\n")

            if len(boot_lines) < 2:
                log_line(log, "FAIL: Less than two boot system lines are configured.")
                continue

            # 4) Validate first boot line = newest image
            if boot_lines[0] != expected_new_boot:
                log_line(log, "FAIL: New image path is not correctly configured.")
                log_line(log, "Expected first boot line: %s" % expected_new_boot)
                log_line(log, "Actual first boot line:   %s" % boot_lines[0])
                continue

            # 5) Validate second boot line = current running image
            if boot_lines[1] != expected_current_boot:
                log_line(log, "FAIL: Current image path is not correctly configured as second boot line.")
                log_line(log, "Expected second boot line: %s" % expected_current_boot)
                log_line(log, "Actual second boot line:   %s" % boot_lines[1])
                continue

            # 6) Validate newest image exists in flash and has expected size
            expected_new_filename = get_boot_filename(expected_new_boot)

            if not expected_new_filename:
                log_line(log, "FAIL: Could not detect newest image filename from expected boot line.")
                continue

            expected_new_size = LATEST_IMAGE_SIZE.get(expected_new_filename)

            if expected_new_size is None:
                log_line(log, "FAIL: Expected image size is not configured for %s." % expected_new_filename)
                continue

            show_dir = run_show(net_connect, "dir flash: | include %s" % expected_new_filename)
            actual_new_size = get_image_size(show_dir, expected_new_filename)

            log.write("\n--- dir flash: | include %s ---\n%s\n" % (expected_new_filename, show_dir))

            if actual_new_size is None:
                log_line(log, "FAIL: New image file was not found in flash: %s" % expected_new_filename)
                continue

            log_line(log, "Expected newest image size: %s bytes" % expected_new_size)
            log_line(log, "Actual newest image size:   %s bytes" % actual_new_size)

            if actual_new_size != expected_new_size:
                log_line(log, "FAIL: New image file size does not match expected size.")
                continue

            # 7) Passed
            log_line(log, "PASS: Boot order is correct.")
            log_line(log, "PASS: New image file exists and size is correct.")
            log_line(log, "Saving config with write memory...")
            wr_output = save_config(net_connect)
            log.write("\n--- write memory output ---\n%s\n" % wr_output)

            # 8) Optional reload
            if RELOAD_ENABLED:
                log_line(log, "Reload is enabled. Sending reload command...")
                reload_output = reload_device(net_connect)
                log.write("\n--- reload output ---\n%s\n" % reload_output)
                log_line(log, "Reload command was sent.")
            else:
                log_line(log, "Reload is disabled. Skipping reload.")

        except NetMikoAuthenticationException as error:
            log_line(log, "FAIL: Authentication failed: %s" % error)

        except NetMikoTimeoutException as error:
            log_line(log, "FAIL: Connection timeout: %s" % error)

        except Exception as error:
            log_line(log, "FAIL: Unexpected error: %s" % error)

        finally:
            if net_connect:
                try:
                    net_connect.disconnect()
                    log_line(log, "Disconnected.")
                except Exception:
                    pass

        log_line(log, "Finished device %s" % ip)
        log_line(log, "-" * 60)

print("")
print("All done. Check logs in ./%s" % LOG_DIR)
