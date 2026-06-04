#!/usr/bin/env python3

from getpass import getpass
import os
import re
import sys

from netmiko import (
    ConnectHandler,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOSTS_FILE = os.path.join(SCRIPT_DIR, "hosts.txt")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "results-version-check.txt")


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


def run_command(net_connect, command, skip_invalid=False):
    try:
        output = net_connect.send_command(command)
        output_lower = output.lower()

        invalid_markers = (
            "% invalid input",
            "% incomplete command",
            "% ambiguous command",
        )

        if skip_invalid and any(marker in output_lower for marker in invalid_markers):
            return "Not supported on this device"

        return output

    except Exception as error:
        return "Command failed: {}".format(error)


def first_match(patterns, text, flags=re.IGNORECASE | re.MULTILINE):
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            for group in match.groups():
                if group:
                    return group.strip()
            return match.group(0).strip()

    return None


def parse_version(show_version):
    return first_match(
        [
            r"Cisco IOS(?: XE)? Software.*?,\s+Version\s+([^,\s]+)",
            r"^\s*Version\s+([^,\s]+)",
            r"\bVersion\s+([^,\s]+)",
        ],
        show_version,
    )


def parse_uptime(show_version):
    return first_match(
        [
            r"^.* uptime is (.+)$",
            r"^uptime is (.+)$",
        ],
        show_version,
    )


def parse_model(show_inventory, show_version):
    model = first_match(
        [
            r"\bPID:\s*([^,\s]+)",
            r"\bPID\s*:\s*([^,\s]+)",
        ],
        show_inventory,
    )

    if model:
        return model

    return first_match(
        [
            r"\bcisco\s+(\S+)\s+\([^)]*\)\s+processor",
            r"\bCisco\s+(\S+)\s+\([^)]*\)\s+processor",
            r"^\s*Model number\s*:\s*(\S+)",
        ],
        show_version,
    )


def parse_rommon(*outputs):
    combined_output = "\n".join(output for output in outputs if output)

    return first_match(
        [
            r"BOOTLDR:\s*.*?\bVersion\s+([^,\s]+)",
            r"ROM:\s*System Bootstrap,\s*Version\s+([^,\s]+)",
            r"ROM:\s*IOS-XE ROMMON,\s*Version\s+([^,\s]+)",
            r"ROMMON\s+Version\s*[:=]?\s*([^,\s]+)",
            r"System Bootstrap,\s*Version\s+([^,\s]+)",
            r"Bootstrap\s+program.*?\bVersion\s+([^,\s]+)",
            r"\bR0\s+ROMMON\s+([^,\s]+)",
            r"\bROMMON:\s*([^,\s]+)",
        ],
        combined_output,
    )


def parse_image_path(show_version):
    return first_match(
        [
            r'System image file is "([^"]+)"',
            r"System image file is\s+(\S+)",
        ],
        show_version,
    )


def split_storage(image_path):
    if not image_path or ":" not in image_path:
        return "flash:"

    storage = image_path.split(":", 1)[0].strip()
    if not storage:
        return "flash:"

    return "{}:".format(storage)


def write_device_result(
    f_out,
    host,
    model,
    sw_version,
    rom_version,
    uptime,
    image_file,
    storage,
    dir_output,
    raw_rommon_output,
):
    f_out.write("Host    : {}\n".format(host))
    f_out.write("Model   : {}\n".format(model or "Unknown"))
    f_out.write("Version : {}\n".format(sw_version or "Unknown"))
    f_out.write("Rommon  : {}\n".format(rom_version or "Unknown"))
    f_out.write("Uptime  : {}\n".format(uptime or "Unknown"))
    f_out.write("Image   : {}\n".format(image_file or "Unknown"))
    f_out.write("Storage : {}\n".format(storage or "Unknown"))
    f_out.write("\nDir output:\n{}\n".format(dir_output.strip()))

    if not rom_version:
        f_out.write("\nROMMON parsing returned Unknown. Raw ROM/boot output:\n")
        f_out.write("{}\n".format(raw_rommon_output.strip()))

    f_out.write("-" * 60 + "\n")


def main():
    if not os.path.exists(HOSTS_FILE):
        print("{} does not exist.".format(HOSTS_FILE))
        return 1

    username = input("Enter username: ").strip()
    password = getpass("Password: ")
    hosts = read_hosts(HOSTS_FILE)

    if not hosts:
        print("No hosts found in {}.".format(HOSTS_FILE))
        return 1

    with open(OUTPUT_FILE, "w") as f_out:
        for ip in hosts:
            device = {
                "device_type": "cisco_ios",
                "host": ip,
                "username": username,
                "password": password,
                "port": 22,
                "fast_cli": False,
            }

            net_connect = None

            try:
                net_connect = ConnectHandler(**device)
                print("Connected to {}".format(device["host"]))

                show_version = run_command(net_connect, "show version")
                show_inventory = run_command(net_connect, "show inventory", skip_invalid=True)
                show_platform = run_command(net_connect, "show platform", skip_invalid=True)
                show_rom = run_command(
                    net_connect,
                    "show version | include ROM:|BOOTLDR:|System Bootstrap|ROMMON",
                    skip_invalid=True,
                )

                sw_version = parse_version(show_version)
                uptime = parse_uptime(show_version)
                model = parse_model(show_inventory, show_version)
                rom_version = parse_rommon(show_rom, show_platform, show_version)
                image_file = parse_image_path(show_version)
                storage = split_storage(image_file)

                dir_command = "dir {} | include .bin".format(storage)
                dir_output = run_command(net_connect, dir_command)

                write_device_result(
                    f_out,
                    device["host"],
                    model,
                    sw_version,
                    rom_version,
                    uptime,
                    image_file,
                    storage,
                    dir_output,
                    "\n\n".join([show_rom, show_platform]),
                )

                print("Data collected for {}".format(device["host"]))

            except NetmikoAuthenticationException:
                msg = "Authentication failed on {}, skipping.".format(device["host"])
                print(msg)
                f_out.write("{}\n{}\n".format(msg, "-" * 60))

            except NetmikoTimeoutException:
                msg = "SSH timeout on {}, skipping.".format(device["host"])
                print(msg)
                f_out.write("{}\n{}\n".format(msg, "-" * 60))

            except Exception as error:
                msg = "Error on {}: {}".format(device["host"], error)
                print(msg)
                f_out.write("{}\n{}\n".format(msg, "-" * 60))

            finally:
                if net_connect:
                    net_connect.disconnect()

    print("Results saved to: {}".format(OUTPUT_FILE))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(130)
