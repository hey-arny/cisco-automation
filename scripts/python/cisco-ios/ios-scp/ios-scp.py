#!/usr/bin/env python3

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import List, Optional, Tuple

from netmiko import ConnectHandler, file_transfer
from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException


HOSTS_FILE = "hosts.txt"
IOS_FILES = "iosfiles.txt"
SCRIPT_DIR = Path(__file__).resolve().parent

SCP_SOCKET_TIMEOUT = 600
MD5_READ_TIMEOUT = 1800
DIR_READ_TIMEOUT = 120

PROGRESS_STATE = {}


ENABLE_SCP_CONFIG = [
    "ip scp server enable",
    "ip tcp window-size 256000",
    "ip ssh window-size 131072",
    "ip ssh bulk-mode 256000",
]

REVERT_SCP_CONFIG = [
    "no ip scp server enable",
    "ip tcp window-size 8192",
    "ip ssh window-size 8192",
    "ip ssh bulk-mode 131072",
]


@dataclass
class FileJob:
    source_path: str
    file_system: str
    dest_file: str
    expected_md5: str


@dataclass
class FileResult:
    source_path: str
    destination: str
    success: bool
    reason: str = ""


def read_hosts(filename: str) -> List[str]:
    hosts = []

    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            hosts.append(line)

    return hosts


def parse_ios_files(filename: str) -> List[FileJob]:
    """
    Format:

    /home/user/file.bin:store=flash:;md5=<md5>
    /home/user/file.bin:store=bootflash:;md5=<md5>
    /home/user/file.bin:store=bootflash:;dest=custom-name.bin;md5=<md5>
    """

    jobs = []

    with open(filename, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            original_line = line.rstrip("\n")
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if ":store=" not in line:
                raise ValueError(
                    f"{filename}:{line_number}: missing ':store=' in line: {original_line}"
                )

            source_path, options_text = line.split(":store=", 1)

            if ";" not in options_text:
                raise ValueError(
                    f"{filename}:{line_number}: missing ';md5=' in line: {original_line}"
                )

            file_system, remaining_options = options_text.split(";", 1)

            source_path = source_path.strip()
            file_system = file_system.strip()

            if not source_path:
                raise ValueError(f"{filename}:{line_number}: source path is empty")

            if not file_system:
                raise ValueError(f"{filename}:{line_number}: store/filesystem is empty")

            options = {}

            for option in remaining_options.split(";"):
                option = option.strip()

                if not option:
                    continue

                if "=" not in option:
                    raise ValueError(
                        f"{filename}:{line_number}: invalid option '{option}'"
                    )

                key, value = option.split("=", 1)
                options[key.strip().lower()] = value.strip()

            expected_md5 = options.get("md5")

            if not expected_md5:
                raise ValueError(f"{filename}:{line_number}: missing md5= value")

            if not re.fullmatch(r"[a-fA-F0-9]{32}", expected_md5):
                raise ValueError(
                    f"{filename}:{line_number}: invalid MD5 value: {expected_md5}"
                )

            if not Path(source_path).is_file():
                raise FileNotFoundError(
                    f"{filename}:{line_number}: local file does not exist: {source_path}"
                )

            dest_file = options.get("dest") or os.path.basename(source_path)

            jobs.append(
                FileJob(
                    source_path=source_path,
                    file_system=file_system,
                    dest_file=dest_file,
                    expected_md5=expected_md5.lower(),
                )
            )

    return jobs


def normalize_remote_path(file_system: str, filename: str) -> str:
    if file_system.endswith(":"):
        return f"{file_system}{filename}"

    if file_system.endswith("/"):
        return f"{file_system}{filename}"

    return f"{file_system}/{filename}"


def extract_md5(output: str) -> Optional[str]:
    match = re.search(r"\b[a-fA-F0-9]{32}\b", output)

    if not match:
        return None

    return match.group(0).lower()


def extract_free_bytes(output: str) -> Optional[int]:
    patterns = [
        r"\((\d+)\s+bytes\s+free\)",
        r"(\d+)\s+bytes\s+free",
        r"(\d+)\s+bytes\s+available",
    ]

    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)

        if match:
            return int(match.group(1))

    return None


def format_seconds(seconds: float) -> str:
    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    seconds = seconds % 60

    if minutes < 60:
        return f"{minutes}m {seconds}s"

    hours = minutes // 60
    minutes = minutes % 60

    return f"{hours}h {minutes}m {seconds}s"


def scp_upload_progress(filename, size, sent, peername=None):
    """
    Live SCP upload progress and upload speed.

    Shows:
    - percentage
    - uploaded MB / total MB
    - upload speed in MB/s
    - upload speed in Mbps
    """

    if isinstance(filename, bytes):
        filename = filename.decode(errors="ignore")

    filename = os.path.basename(str(filename))
    key = f"{filename}-{size}"

    now = time.monotonic()

    if key not in PROGRESS_STATE:
        PROGRESS_STATE[key] = {
            "start_time": now,
        }

    start_time = PROGRESS_STATE[key]["start_time"]
    elapsed = max(now - start_time, 0.001)

    if size == 0:
        percent = 100
    else:
        percent = int((sent / size) * 100)

    uploaded_mb = sent / 1024 / 1024
    total_mb = size / 1024 / 1024

    upload_mb_per_sec = uploaded_mb / elapsed
    upload_mbps = upload_mb_per_sec * 8

    bar_length = 30
    filled_length = int(bar_length * percent / 100)

    bar = "#" * filled_length + "-" * (bar_length - filled_length)

    print(
        f"\r    Upload: [{bar}] {percent:3d}% "
        f"{uploaded_mb:.1f}/{total_mb:.1f} MB "
        f"{upload_mb_per_sec:.2f} MB/s "
        f"({upload_mbps:.2f} Mbps)",
        end="",
        flush=True,
    )

    if sent >= size:
        print()
        PROGRESS_STATE.pop(key, None)


def verify_remote_md5(
    connection, file_system: str, dest_file: str, expected_md5: str
) -> Tuple[bool, str]:
    remote_path = normalize_remote_path(file_system, dest_file)
    command = f"verify /md5 {remote_path}"

    print(f"    Verifying MD5: {command}")

    try:
        output = connection.send_command(
            command,
            expect_string=r"#",
            read_timeout=MD5_READ_TIMEOUT,
        )

    except TypeError:
        output = connection.send_command(
            command,
            expect_string=r"#",
            delay_factor=60,
            max_loops=3000,
        )

    found_md5 = extract_md5(output)

    if not found_md5:
        print("    MD5 result: FAIL - could not find MD5 in device output")
        print(output)
        return False, "Could not find MD5 in device output"

    expected_md5 = expected_md5.lower()

    print(f"    Expected MD5: {expected_md5}")
    print(f"    Device MD5:   {found_md5}")

    if found_md5 == expected_md5:
        print("    MD5 result: PASS")
        return True, ""

    print("    MD5 result: FAIL")
    return False, f"MD5 mismatch: expected {expected_md5}, device returned {found_md5}"


def check_remote_free_space(connection, file_system: str, local_file_size: int) -> bool:
    command = f"dir {file_system}"

    print(f"  Checking free space: {command}")

    try:
        output = connection.send_command(
            command,
            expect_string=r"#",
            read_timeout=DIR_READ_TIMEOUT,
        )

    except TypeError:
        output = connection.send_command(
            command,
            expect_string=r"#",
            delay_factor=4,
            max_loops=300,
        )

    free_bytes = extract_free_bytes(output)

    if free_bytes is None:
        print("  WARNING: Could not parse remote free space. Continuing.")
        return True

    free_mb = free_bytes / 1024 / 1024
    required_mb = local_file_size / 1024 / 1024

    print(f"  Remote free:  {free_mb:.1f} MB")
    print(f"  Required:     {required_mb:.1f} MB")

    if free_bytes < local_file_size:
        print("  Free-space result: FAIL")
        return False

    print("  Free-space result: PASS")
    return True


def configure_scp(connection) -> None:
    print("  Enabling SCP...")
    output = connection.send_config_set(ENABLE_SCP_CONFIG)

    if "% Invalid input" in output:
        print("  WARNING: Some SCP tuning commands are not supported. Continuing.")


def revert_scp_config(connection) -> None:
    print("  Reverting SCP config...")
    output = connection.send_config_set(REVERT_SCP_CONFIG)

    if "% Invalid input" in output:
        print("  WARNING: Some revert commands are not supported. Continuing.")


def transfer_one_file(connection, job: FileJob) -> Tuple[bool, str]:
    local_file_size = os.path.getsize(job.source_path)
    local_file_size_mb = local_file_size / 1024 / 1024

    destination = normalize_remote_path(job.file_system, job.dest_file)

    print(f"  Uploading:    {job.source_path}")
    print(f"  Destination:  {destination}")
    print(f"  Size:         {local_file_size_mb:.1f} MB")

    if not check_remote_free_space(connection, job.file_system, local_file_size):
        return False, "Not enough remote free space"

    transfer_start = time.monotonic()

    try:
        result = file_transfer(
            ssh_conn=connection,
            source_file=job.source_path,
            dest_file=job.dest_file,
            file_system=job.file_system,
            direction="put",
            overwrite_file=True,
            disable_md5=True,
            verify_file=False,
            socket_timeout=SCP_SOCKET_TIMEOUT,
            progress=scp_upload_progress,
        )

    except TypeError:
        print("  WARNING: This Netmiko version does not support live upload speed callback.")
        print("  Uploading without live speed display...")

        result = file_transfer(
            ssh_conn=connection,
            source_file=job.source_path,
            dest_file=job.dest_file,
            file_system=job.file_system,
            direction="put",
            overwrite_file=True,
            disable_md5=True,
            verify_file=False,
            socket_timeout=SCP_SOCKET_TIMEOUT,
        )

    transfer_end = time.monotonic()
    transfer_seconds = max(transfer_end - transfer_start, 0.001)

    avg_upload_mb_per_sec = local_file_size_mb / transfer_seconds
    avg_upload_mbps = avg_upload_mb_per_sec * 8

    print(
        f"  Upload speed average: {avg_upload_mb_per_sec:.2f} MB/s "
        f"({avg_upload_mbps:.2f} Mbps)"
    )
    print(f"  Upload time:          {format_seconds(transfer_seconds)}")

    if not result.get("file_transferred") and not result.get("file_exists"):
        print(f"  SCP upload result: FAIL - {result}")
        return False, f"SCP upload failed: {result}"

    print("  SCP upload result: PASS")

    md5_ok, md5_reason = verify_remote_md5(
        connection=connection,
        file_system=job.file_system,
        dest_file=job.dest_file,
        expected_md5=job.expected_md5,
    )

    if md5_ok:
        print("  File result: PASS")
        return True, ""

    print("  File result: FAIL")
    return False, md5_reason or "MD5 verification failed"


def process_host(host: str, args, jobs: List[FileJob]) -> dict:
    print("=" * 80)
    print(f"Connecting to device: {host}")

    device = {
        "device_type": args.device_type,
        "host": host,
        "username": args.username,
        "password": args.password,
        "fast_cli": False,
    }

    connection = None
    host_success = True
    host_error = None
    file_results = []

    try:
        connection = ConnectHandler(**device)

        print(f"Connected to {host}")

        configure_scp(connection)

        for job in jobs:
            print("-" * 80)

            destination = normalize_remote_path(job.file_system, job.dest_file)

            try:
                ok, reason = transfer_one_file(connection, job)

            except Exception as exc:
                ok = False
                reason = str(exc)
                host_error = f"{destination}: {exc}"
                print(f"ERROR transferring file on {host}: {exc}")

            file_results.append(
                FileResult(
                    source_path=job.source_path,
                    destination=destination,
                    success=ok,
                    reason=reason,
                )
            )

            if not ok:
                host_success = False

                if not host_error:
                    host_error = f"{destination}: {reason or 'Upload or MD5 failed'}"

                if args.stop_on_error:
                    print("  stop-on-error enabled. Stopping file loop for this host.")
                    break

    except NetmikoAuthenticationException:
        host_success = False
        host_error = "Authentication failed"
        print(f"ERROR: Authentication failed for {host}")

    except NetmikoTimeoutException:
        host_success = False
        host_error = "Connection timeout"
        print(f"ERROR: Timeout connecting to {host}")

    except Exception as exc:
        host_success = False
        host_error = str(exc)
        print(f"ERROR on {host}: {exc}")

    finally:
        if connection:
            try:
                revert_scp_config(connection)

            except Exception as exc:
                host_success = False
                host_error = f"Could not revert config: {exc}"
                print(f"WARNING: {host_error}")

            connection.disconnect()
            print(f"Disconnected from {host}")

    return {
        "host": host,
        "success": host_success,
        "error": host_error,
        "files": file_results,
    }


def print_final_summary(results) -> None:
    total_hosts = len(results)
    failed_results = [result for result in results if not result["success"]]
    successful_results = [result for result in results if result["success"]]
    successful_hosts = total_hosts - len(failed_results)

    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"Successful hosts: {successful_hosts}/{total_hosts}")
    print(f"Failed hosts:     {len(failed_results)}/{total_hosts}")

    print()
    print("PASS Uploaded image and verified MD5:")

    if successful_results:
        for result in successful_results:
            print(result["host"])
    else:
        print("  none")

    if not failed_results:
        print()
        print("No failed devices.")
        print("=" * 80)
        return

    print()
    print("FAIL Upload or MD5 verification failed:")

    for result in failed_results:
        host = result["host"]
        reason = result.get("error") or "Unknown failure"
        print(f"  {host}: {reason}")

    print("=" * 80)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload files to Cisco IOS/IOS-XE devices using Netmiko SCP and verify MD5."
    )

    parser.add_argument(
        "-d",
        "--device-type",
        default="cisco_ios",
        help="Netmiko device_type. Default: cisco_ios",
    )

    parser.add_argument(
        "--hosts-file",
        default=str(SCRIPT_DIR / HOSTS_FILE),
        help=f"Hosts file. Default: {SCRIPT_DIR / HOSTS_FILE}",
    )

    parser.add_argument(
        "--files-file",
        default=str(SCRIPT_DIR / IOS_FILES),
        help=f"IOS files definition file. Default: {SCRIPT_DIR / IOS_FILES}",
    )

    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop processing more files on a host after first failed upload or MD5 check.",
    )

    args = parser.parse_args()

    args.username = input("Enter username: ").strip()
    args.password = getpass("Enter password: ")

    if not args.username:
        print("ERROR: Username cannot be empty.")
        return 1

    if not os.path.isfile(args.hosts_file):
        print(f"ERROR: Hosts file not found: {args.hosts_file}")
        return 1

    if not os.path.isfile(args.files_file):
        print(f"ERROR: File definition file not found: {args.files_file}")
        print("Create iosfiles.txt in the same directory, or use:")
        print("python3 script.py --files-file /path/to/iosfiles.txt")
        return 1

    try:
        hosts = read_hosts(args.hosts_file)
        jobs = parse_ios_files(args.files_file)

    except Exception as exc:
        print(f"ERROR while reading input files: {exc}")
        return 1

    if not hosts:
        print(f"No hosts found in {args.hosts_file}")
        return 1

    if not jobs:
        print(f"No file jobs found in {args.files_file}")
        return 1

    print(f"Loaded {len(hosts)} host(s).")
    print(f"Loaded {len(jobs)} file job(s).")

    results = []

    for host in hosts:
        result = process_host(host, args, jobs)
        results.append(result)

    print_final_summary(results)

    failed_hosts = [result["host"] for result in results if not result["success"]]

    if failed_hosts:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
