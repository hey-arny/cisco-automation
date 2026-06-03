import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from getpass import getpass
from pathlib import Path

from netmiko import ConnectHandler


USERNAME = "arnold.rusu"
HOSTS_FILE = Path("hosts.txt")
RESULTS_FILE = Path("results.txt")

CONF_COMMANDS = [
    "no ip access-list standard X-SNMP",
    "ip access-list standard X-SNMP",
    "permit host XXX.XXX.XXX.XXX",
    "exit",
]

SHOW_COMMANDS = [
    "show ip access-lists X-SNMP",
    "show run | include sKLYc3d8Ak",
]


def read_hosts(path):
    hosts = []
    seen = set()

    with path.open() as f:
        for line in f:
            host = line.strip()

            if not host or host.startswith("#") or host in seen:
                continue

            hosts.append(host)
            seen.add(host)

    return hosts


def process_device(ip, password):
    lines = [f"\nConnecting to {ip}..."]
    net_connect = None

    device = {
        "device_type": "cisco_ios",
        "host": ip,
        "username": USERNAME,
        "password": password,
        "port": 22,
        "fast_cli": False,
        "conn_timeout": 15,
        "auth_timeout": 15,
        "banner_timeout": 15,
    }

    try:
        net_connect = ConnectHandler(**device)

        lines.append("Applying config...")
        config_output = net_connect.send_config_set(CONF_COMMANDS, cmd_verify=False)
        if config_output.strip():
            lines.append(config_output)

        lines.append("Saving config...")
        save_output = net_connect.save_config()
        if save_output.strip():
            lines.append(save_output)

        lines.extend(["", "=" * 80, f"Device: {ip}", "=" * 80])

        for cmd in SHOW_COMMANDS:
            lines.append(f"\n# {cmd}")
            try:
                output = net_connect.send_command(
                    cmd,
                    expect_string=r"#",
                    delay_factor=2,
                )
                lines.append(output)
            except Exception as exc:
                lines.append(f"SHOW COMMAND FAILED: {exc}")

        lines.append(f"\nSUCCESS on {ip}")
        return True, "\n".join(lines)

    except Exception as exc:
        lines.append(f"FAILED on {ip}: {exc}")
        return False, "\n".join(lines)

    finally:
        if net_connect is not None:
            net_connect.disconnect()


def print_save(results_file, text=""):
    print(text)
    results_file.write(str(text) + "\n")
    results_file.flush()


def main():
    parser = argparse.ArgumentParser(
        description="Apply X-SNMP ACL to Cisco IOS devices and save config."
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=5,
        help="number of devices to process in parallel (default: 5)",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be 1 or higher")

    if not HOSTS_FILE.exists():
        print(f"Missing host file: {HOSTS_FILE.resolve()}")
        return 1

    hosts = read_hosts(HOSTS_FILE)
    if not hosts:
        print(f"No hosts found in {HOSTS_FILE.resolve()}")
        return 1

    password = getpass("Password: ")
    workers = min(args.workers, len(hosts))
    successes = 0

    try:
        with RESULTS_FILE.open("w") as results_file:
            print_save(results_file, f"Loaded {len(hosts)} host(s). Workers: {workers}")

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(process_device, ip, password): ip
                    for ip in hosts
                }

                for future in as_completed(futures):
                    ok, output = future.result()
                    successes += int(ok)
                    print_save(results_file, output)

            failures = len(hosts) - successes
            print_save(
                results_file,
                f"\nDone. Success: {successes}, Failed: {failures}, Total: {len(hosts)}",
            )

    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 130

    return 0 if successes == len(hosts) else 1


if __name__ == "__main__":
    sys.exit(main())
