#!/usr/bin/env python3

from getpass import getpass
import os
import sys

from netmiko import (
    ConnectHandler,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOSTS_FILE = os.path.join(SCRIPT_DIR, "hosts.txt")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "results-post-upgrade-check.txt")


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


def write_section(f_out, title, output):
    f_out.write("{}:\n\n{}\n".format(title, output.strip()))
    f_out.write("_" * 74 + "\n")


def collect_device(net_connect):
    return {
        "Version": run_command(net_connect, "show version | include uptime|reason|bin|IOS Software"),
        "Interface status": run_command(net_connect, "show interface description | exclude down"),
        "Duplex settings": run_command(net_connect, "sh int | i dupl|Dupl"),
        "BGPv4 status": run_command(net_connect, "show ip bgp summary | begin Neighbor", skip_invalid=True),
        "BGP VPNv4 summary": run_command(net_connect, "show ip bgp vpnv4 all summary | begin Neighbor", skip_invalid=True),
        "BGP IPv6 summary": run_command(net_connect, "show ip bgp ipv6 unicast summary | begin Neighbor", skip_invalid=True),
        "BGP VPNv6 summary": run_command(net_connect, "show ip bgp vpnv6 unicast all summary | begin Neighbor", skip_invalid=True),
        "Crypto session": run_command(net_connect, "show crypto session brief | begin Peer", skip_invalid=True),
        "HSRP status": run_command(net_connect, "show standby brief all", skip_invalid=True),
        "VRRP status": run_command(net_connect, "show vrrp brief", skip_invalid=True),
        "Switch stack status": run_command(net_connect, "show switch", skip_invalid=True),
        "IPv4 route summary": run_command(net_connect, "show ip route summary"),
        "VRF route summary": run_command(net_connect, "show ip route vrf * summary", skip_invalid=True),
        "IPv6 route summary": run_command(net_connect, "show ipv6 route summary", skip_invalid=True),
    }


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

                results = collect_device(net_connect)

                f_out.write("=" * 74 + "\n\n")
                f_out.write("Device: {}\n\n".format(device["host"]))

                for title, output in results.items():
                    write_section(f_out, title, output)

                f_out.write("=" * 74 + "\n\n")
                print("Data collected for {}".format(device["host"]))

            except NetmikoAuthenticationException:
                msg = "Authentication failed on {}, skipping.".format(device["host"])
                print(msg)
                f_out.write("{}\n{}\n".format(msg, "=" * 74))

            except NetmikoTimeoutException:
                msg = "SSH timeout on {}, skipping.".format(device["host"])
                print(msg)
                f_out.write("{}\n{}\n".format(msg, "=" * 74))

            except Exception as error:
                msg = "Error on {}: {}".format(device["host"], error)
                print(msg)
                f_out.write("{}\n{}\n".format(msg, "=" * 74))

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
