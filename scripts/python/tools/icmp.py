#!/usr/bin/env python3
import subprocess
import socket
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


RESULTS_FILE = "icmp-check-results.txt"

JUMP_HOST = socket.gethostname()

PING_TIMEOUT = 1
MAX_WORKERS = 200


def get_hosts_from_input():
    print("Enter IPs you want to ping.")

    hosts = []

    while True:
        ip = input().strip()

        if not ip:
            break

        hosts.append(ip)

    # Remove duplicates but keep order
    hosts = list(dict.fromkeys(hosts))

    return hosts


def ping(ip):
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(PING_TIMEOUT), ip],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return ip, result.returncode == 0


def print_save(file, text=""):
    print(text)
    file.write(str(text) + "\n")
    file.flush()


hosts = get_hosts_from_input()

if not hosts:
    print("No IPs entered. Exiting.")
    exit()


reachable = []
non_reachable = []

start_time = datetime.now()

with open(RESULTS_FILE, "w") as results:
    print_save(results, "=" * 50)
    print_save(results, "ICMP CHECK STARTED")
    print_save(results, "=" * 50)
    print_save(results, f"Jump host: {JUMP_HOST}")
    print_save(results, f"Started:   {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print_save(results, f"Total IPs: {len(hosts)}")
    print_save(results, "-" * 50)

    workers = min(MAX_WORKERS, len(hosts))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(ping, ip) for ip in hosts]

        for future in as_completed(futures):
            ip, is_up = future.result()

            if is_up:
                reachable.append(ip)
                print_save(results, f"{ip} UP")
            else:
                non_reachable.append(ip)
                print_save(results, f"{ip} DOWN")

    print_save(results, "")
    print_save(results, "=" * 50)
    print_save(results, "FINAL SUMMARY")
    print_save(results, "=" * 50)
    print_save(results, f"Jump host: {JUMP_HOST}")
    print_save(results, "")
    print_save(results, f"Reachable count:     {len(reachable)}")
    print_save(results, f"Non-reachable count: {len(non_reachable)}")
    print_save(results, "")

    print_save(results, "Reachable IPs:")
    if reachable:
        for ip in reachable:
            print_save(results, f"  {ip}")
    else:
        print_save(results, "  None")

    print_save(results, "")

    print_save(results, "Non-reachable IPs:")
    if non_reachable:
        for ip in non_reachable:
            print_save(results, f"  {ip}")
    else:
        print_save(results, "  None")
