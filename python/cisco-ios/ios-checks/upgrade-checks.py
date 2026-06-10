#!/usr/bin/env python3

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from getpass import getpass
import os

DEFAULT_WORKERS = 3
SEPARATOR = "=" * 74
SECTION_SEPARATOR = "_" * 74

COMMANDS = (
    ("Version", "show version | include uptime|reason|bin|IOS Software", False),
    ("Interface status", "show interface description | exclude down", False),
    ("Duplex settings", "sh int | i dupl|Dupl", False),
    ("BGPv4 status", "show ip bgp summary | begin Neighbor", True),
    ("BGP VPNv4 summary", "show ip bgp vpnv4 all summary | begin Neighbor", True),
    ("BGP IPv6 summary", "show ip bgp ipv6 unicast summary | begin Neighbor", True),
    ("BGP VPNv6 summary", "show ip bgp vpnv6 unicast all summary | begin Neighbor", True),
    ("Crypto session", "show crypto session brief | begin Peer", True),
    ("HSRP status", "show standby brief all", True),
    ("VRRP status", "show vrrp brief", True),
    ("Switch stack status", "show switch", True),
    ("IPv4 route summary", "show ip route summary", False),
    ("VRF route summary", "show ip route vrf * summary", True),
    ("IPv6 route summary", "show ipv6 route summary", True),
)


def parse_args(description, default_hosts_file, default_output_file):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--hosts",
        default=default_hosts_file,
        help="Path to hosts file. Default: %(default)s",
    )
    parser.add_argument(
        "--output",
        default=default_output_file,
        help="Path to results file. Default: %(default)s",
    )
    parser.add_argument(
        "--workers",
        default=DEFAULT_WORKERS,
        type=int,
        help="Number of devices to check at the same time. Default: %(default)s",
    )
    parser.add_argument(
        "--fast-cli",
        action="store_true",
        help="Enable Netmiko fast_cli. Test with a small batch first on slow devices.",
    )
    return parser.parse_args()


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


def collect_device(net_connect):
    results = []

    for title, command, skip_invalid in COMMANDS:
        output = run_command(net_connect, command, skip_invalid=skip_invalid)
        results.append((title, output))

    return results


def format_section(title, output):
    return "{}:\n\n{}\n{}\n".format(title, output.strip(), SECTION_SEPARATOR)


def format_success(host, results):
    output = [SEPARATOR, "", "Device: {}".format(host), ""]

    for title, command_output in results:
        output.append(format_section(title, command_output))

    output.append(SEPARATOR)
    output.append("")
    return "\n".join(output)


def format_error(message):
    return "{}\n{}\n".format(message, SEPARATOR)


def check_host(host, username, password, fast_cli):
    try:
        from netmiko import (
            ConnectHandler,
            NetmikoAuthenticationException,
            NetmikoTimeoutException,
        )
    except ImportError as error:
        msg = "Error on {}: {}".format(host, error)
        print(msg)
        return format_error(msg)

    device = {
        "device_type": "cisco_ios",
        "host": host,
        "username": username,
        "password": password,
        "port": 22,
        "fast_cli": fast_cli,
    }

    net_connect = None

    try:
        net_connect = ConnectHandler(**device)
        print("Connected to {}".format(host))

        results = collect_device(net_connect)
        print("Data collected for {}".format(host))
        return format_success(host, results)

    except NetmikoAuthenticationException:
        msg = "Authentication failed on {}, skipping.".format(host)
        print(msg)
        return format_error(msg)

    except NetmikoTimeoutException:
        msg = "SSH timeout on {}, skipping.".format(host)
        print(msg)
        return format_error(msg)

    except Exception as error:
        msg = "Error on {}: {}".format(host, error)
        print(msg)
        return format_error(msg)

    finally:
        if net_connect:
            net_connect.disconnect()


def run_check(description, hosts_file, output_file):
    args = parse_args(description, hosts_file, output_file)

    if not os.path.exists(args.hosts):
        print("{} does not exist.".format(args.hosts))
        return 1

    username = input("Enter username: ").strip()
    password = getpass("Password: ")
    hosts = read_hosts(args.hosts)

    if not hosts:
        print("No hosts found in {}.".format(args.hosts))
        return 1

    workers = max(1, min(args.workers, len(hosts)))
    print("Checking {} device(s) with {} worker(s).".format(len(hosts), workers))

    results_by_host = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_host = {
            executor.submit(check_host, host, username, password, args.fast_cli): host
            for host in hosts
        }

        for future in as_completed(future_to_host):
            host = future_to_host[future]
            try:
                results_by_host[host] = future.result()
            except Exception as error:
                msg = "Error on {}: {}".format(host, error)
                print(msg)
                results_by_host[host] = format_error(msg)

    with open(args.output, "w") as f_out:
        for host in hosts:
            f_out.write(results_by_host[host])

    print("Results saved to: {}".format(args.output))
    return 0
