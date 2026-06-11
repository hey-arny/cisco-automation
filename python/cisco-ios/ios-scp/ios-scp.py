#!/usr/bin/env python3

import argparse
import hashlib
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import List, Optional, Tuple

from netmiko import ConnectHandler, file_transfer
from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException


HOSTS_FILE = "hosts.txt"
IOS_FILES = "iosfiles.txt"
SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_WORKERS = 2
MAX_WORKERS = 8
SCP_SOCKET_TIMEOUT = 600
MD5_READ_TIMEOUT = 1800
DIR_READ_TIMEOUT = 120

PROGRESS_STATE = {}
PROGRESS_LOCK = threading.Lock()
OUTPUT_LOCK = threading.Lock()


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


def create_hosts_file(filename: str) -> None:
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def log_line(message: str = "", host: Optional[str] = None) -> None:
    with OUTPUT_LOCK:
        if host:
            print(f"[{host}] {message}", flush=True)

        else:
            print(message, flush=True)


def worker_count(value: str) -> int:
    try:
        number = int(value)

    except ValueError:
        raise argparse.ArgumentTypeError("must be a number from 1 to 5") from None

    if number < 1 or number > MAX_WORKERS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_WORKERS}")

    return number


def prompt_worker_count() -> int:
    while True:
        value = input(
            f"How many devices at the same time? "
            f"[1-{MAX_WORKERS}, default {DEFAULT_WORKERS}]: "
        ).strip()

        if not value:
            return DEFAULT_WORKERS

        try:
            return worker_count(value)

        except argparse.ArgumentTypeError as exc:
            print(f"ERROR: Worker count {exc}.")


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


def format_bytes(size: int) -> str:
    size_float = float(size)

    for unit in ["B", "KB", "MB", "GB"]:
        if size_float < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size_float)} {unit}"

            return f"{size_float:.1f} {unit}"

        size_float /= 1024

    return f"{size} B"


def calculate_file_md5(filename: str) -> str:
    md5_hash = hashlib.md5()

    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5_hash.update(chunk)

    return md5_hash.hexdigest()


def verify_local_md5(jobs: List[FileJob]) -> dict:
    local_md5_by_source = {}

    print("Checking local file MD5 values from iosfiles.txt...")

    for job in jobs:
        if job.source_path not in local_md5_by_source:
            local_md5_by_source[job.source_path] = calculate_file_md5(job.source_path)

        local_md5 = local_md5_by_source[job.source_path]

        if local_md5 != job.expected_md5:
            raise ValueError(
                f"Local MD5 mismatch for {job.source_path}: "
                f"iosfiles.txt has {job.expected_md5}, local file is {local_md5}"
            )

    print("Local MD5 check: PASS")
    print()
    return local_md5_by_source


def print_upload_plan(
    hosts: List[str], jobs: List[FileJob], args, local_md5_by_source: dict
) -> None:
    total_size = sum(os.path.getsize(job.source_path) for job in jobs)
    total_transfer_size = total_size * len(hosts)

    print("=" * 80)
    print("UPLOAD PLAN")
    print("=" * 80)
    print(f"Hosts file:       {args.hosts_file}")
    print(f"IOS files file:   {args.files_file}")
    print(f"Device type:      {args.device_type}")
    print(f"Devices:          {len(hosts)}")
    print(f"Files per device: {len(jobs)}")
    print(f"Data per device:  {format_bytes(total_size)}")
    print(f"Total transfer:   {format_bytes(total_transfer_size)}")
    print()
    print("IMPORTANT: every device listed below will receive every active file below.")
    print()
    print("Devices:")

    for index, host in enumerate(hosts, start=1):
        print(f"  {index}. {host}")

    print()
    print("Files:")

    for index, job in enumerate(jobs, start=1):
        source_size = os.path.getsize(job.source_path)
        destination = normalize_remote_path(job.file_system, job.dest_file)

        print(f"  {index}. Source:      {job.source_path}")
        print(f"     Destination: {destination}")
        print(f"     Size:        {format_bytes(source_size)}")
        print(f"     Local MD5:   {local_md5_by_source[job.source_path]}")
        print(f"     Expected:    {job.expected_md5}")

    print("=" * 80)


def confirm_upload_plan(hosts: List[str], jobs: List[FileJob], args) -> bool:
    local_md5_by_source = verify_local_md5(jobs)
    print_upload_plan(hosts, jobs, args, local_md5_by_source)

    if args.yes:
        print("Upload plan confirmation skipped by --yes.")
        return True

    answer = input("Type YES to continue with this upload plan: ").strip()
    return answer.upper() == "YES"


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


def make_scp_log_progress(host: str):
    def scp_log_progress(filename, size, sent, peername=None):
        if isinstance(filename, bytes):
            filename_text = filename.decode(errors="ignore")
        else:
            filename_text = str(filename)

        filename_text = os.path.basename(filename_text)
        key = f"{host}-{filename_text}-{size}"
        now = time.monotonic()

        if size == 0:
            percent = 100
        else:
            percent = int((sent / size) * 100)

        with PROGRESS_LOCK:
            if key not in PROGRESS_STATE:
                PROGRESS_STATE[key] = {
                    "start_time": now,
                    "last_print_time": 0,
                    "last_print_percent": -10,
                }

            state = PROGRESS_STATE[key]

            should_print = (
                sent >= size
                or percent >= state["last_print_percent"] + 10
                or now - state["last_print_time"] >= 10
            )

            if not should_print:
                return

            elapsed = max(now - state["start_time"], 0.001)
            uploaded_mb = sent / 1024 / 1024
            total_mb = size / 1024 / 1024
            upload_mb_per_sec = uploaded_mb / elapsed
            upload_mbps = upload_mb_per_sec * 8

            log_line(
                f"Upload {filename_text}: {percent:3d}% "
                f"{uploaded_mb:.1f}/{total_mb:.1f} MB "
                f"{upload_mb_per_sec:.2f} MB/s ({upload_mbps:.2f} Mbps)",
                host,
            )

            if sent >= size:
                PROGRESS_STATE.pop(key, None)

            else:
                state["last_print_time"] = now
                state["last_print_percent"] = percent

    return scp_log_progress


def verify_remote_md5(
    connection, file_system: str, dest_file: str, expected_md5: str, host: str
) -> Tuple[bool, str]:
    remote_path = normalize_remote_path(file_system, dest_file)
    command = f"verify /md5 {remote_path}"

    log_line(f"Verifying MD5: {command}", host)

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
        log_line("MD5 result: FAIL - could not find MD5 in device output", host)
        log_line(output, host)
        return False, "Could not find MD5 in device output"

    expected_md5 = expected_md5.lower()

    log_line(f"Expected MD5: {expected_md5}", host)
    log_line(f"Device MD5:   {found_md5}", host)

    if found_md5 == expected_md5:
        log_line("MD5 result: PASS", host)
        return True, ""

    log_line("MD5 result: FAIL", host)
    return False, f"MD5 mismatch: expected {expected_md5}, device returned {found_md5}"


def check_remote_free_space(
    connection, file_system: str, local_file_size: int, host: str
) -> bool:
    command = f"dir {file_system}"

    log_line(f"Checking free space: {command}", host)

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
        log_line("WARNING: Could not parse remote free space. Continuing.", host)
        return True

    free_mb = free_bytes / 1024 / 1024
    required_mb = local_file_size / 1024 / 1024

    log_line(f"Remote free:  {free_mb:.1f} MB", host)
    log_line(f"Required:     {required_mb:.1f} MB", host)

    if free_bytes < local_file_size:
        log_line("Free-space result: FAIL", host)
        return False

    log_line("Free-space result: PASS", host)
    return True


def configure_scp(connection, host: str) -> None:
    log_line("Enabling SCP...", host)
    output = connection.send_config_set(ENABLE_SCP_CONFIG)

    if "% Invalid input" in output:
        log_line("WARNING: Some SCP tuning commands are not supported. Continuing.", host)


def revert_scp_config(connection, host: str) -> None:
    log_line("Reverting SCP config...", host)
    output = connection.send_config_set(REVERT_SCP_CONFIG)

    if "% Invalid input" in output:
        log_line("WARNING: Some revert commands are not supported. Continuing.", host)


def transfer_one_file(
    connection, job: FileJob, host: str, progress_callback=None
) -> Tuple[bool, str]:
    local_file_size = os.path.getsize(job.source_path)
    local_file_size_mb = local_file_size / 1024 / 1024

    destination = normalize_remote_path(job.file_system, job.dest_file)

    log_line(f"Uploading:    {job.source_path}", host)
    log_line(f"Destination:  {destination}", host)
    log_line(f"Size:         {local_file_size_mb:.1f} MB", host)

    if not check_remote_free_space(connection, job.file_system, local_file_size, host):
        return False, "Not enough remote free space"

    transfer_start = time.monotonic()

    file_transfer_kwargs = {
        "ssh_conn": connection,
        "source_file": job.source_path,
        "dest_file": job.dest_file,
        "file_system": job.file_system,
        "direction": "put",
        "overwrite_file": True,
        "disable_md5": True,
        "verify_file": False,
        "socket_timeout": SCP_SOCKET_TIMEOUT,
    }

    if progress_callback:
        file_transfer_kwargs["progress"] = progress_callback

    try:
        result = file_transfer(**file_transfer_kwargs)

    except TypeError:
        if not progress_callback:
            raise

        log_line(
            "WARNING: This Netmiko version does not support live upload speed callback.",
            host,
        )
        log_line("Uploading without live speed display...", host)

        file_transfer_kwargs.pop("progress", None)
        result = file_transfer(**file_transfer_kwargs)

    transfer_end = time.monotonic()
    transfer_seconds = max(transfer_end - transfer_start, 0.001)

    avg_upload_mb_per_sec = local_file_size_mb / transfer_seconds
    avg_upload_mbps = avg_upload_mb_per_sec * 8

    log_line(
        f"Upload speed average: {avg_upload_mb_per_sec:.2f} MB/s "
        f"({avg_upload_mbps:.2f} Mbps)",
        host,
    )
    log_line(f"Upload time:          {format_seconds(transfer_seconds)}", host)

    if not result.get("file_transferred") and not result.get("file_exists"):
        log_line(f"SCP upload result: FAIL - {result}", host)
        return False, f"SCP upload failed: {result}"

    log_line("SCP upload result: PASS", host)

    md5_ok, md5_reason = verify_remote_md5(
        connection=connection,
        file_system=job.file_system,
        dest_file=job.dest_file,
        expected_md5=job.expected_md5,
        host=host,
    )

    if md5_ok:
        log_line("File result: PASS", host)
        return True, ""

    log_line("File result: FAIL", host)
    return False, md5_reason or "MD5 verification failed"


def process_host(host: str, args, jobs: List[FileJob]) -> dict:
    log_line("Starting device workflow", host)
    log_line("Connecting", host)

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

        log_line("Connected", host)

        configure_scp(connection, host)

        for job in jobs:
            destination = normalize_remote_path(job.file_system, job.dest_file)

            try:
                ok, reason = transfer_one_file(
                    connection=connection,
                    job=job,
                    host=host,
                    progress_callback=(
                        scp_upload_progress
                        if args.workers == 1
                        else make_scp_log_progress(host)
                    ),
                )

            except Exception as exc:
                ok = False
                reason = str(exc)
                host_error = f"{destination}: {exc}"
                log_line(f"ERROR transferring file: {exc}", host)

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
                    log_line("stop-on-error enabled. Stopping file loop for this host.", host)
                    break

    except NetmikoAuthenticationException:
        host_success = False
        host_error = "Authentication failed"
        log_line("ERROR: Authentication failed", host)

    except NetmikoTimeoutException:
        host_success = False
        host_error = "Connection timeout"
        log_line("ERROR: Connection timeout", host)

    except Exception as exc:
        host_success = False
        host_error = str(exc)
        log_line(f"ERROR: {exc}", host)

    finally:
        if connection:
            try:
                revert_scp_config(connection, host)

            except Exception as exc:
                host_success = False
                host_error = f"Could not revert config: {exc}"
                log_line(f"WARNING: {host_error}", host)

            connection.disconnect()
            log_line("Disconnected", host)

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

    failed_by_reason = {}

    for result in failed_results:
        reason = result.get("error") or "Unknown failure"

        if reason == "Authentication failed":
            group = "Authentication failed"
        elif "MD5" in reason.upper():
            group = "MD5 verification failed"
        else:
            group = "Upload failed"

        failed_by_reason.setdefault(group, []).append(result["host"])

    for group in ["Upload failed", "MD5 verification failed", "Authentication failed"]:
        hosts = failed_by_reason.get(group)

        if not hosts:
            continue

        print()
        print(f"FAIL {group}:")
        print()

        for host in hosts:
            print(host)

    print("=" * 80)


def process_hosts(hosts: List[str], args, jobs: List[FileJob]) -> List[dict]:
    if args.workers == 1:
        results = []

        for host in hosts:
            result = process_host(host, args, jobs)
            results.append(result)

        return results

    results_by_host = {}
    max_workers = min(args.workers, len(hosts))

    print(f"Processing up to {max_workers} host(s) at the same time.")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_host = {
            executor.submit(process_host, host, args, jobs): host for host in hosts
        }

        for future in as_completed(future_to_host):
            host = future_to_host[future]

            try:
                results_by_host[host] = future.result()

            except Exception as exc:
                print(f"ERROR processing {host}: {exc}")
                results_by_host[host] = {
                    "host": host,
                    "success": False,
                    "error": str(exc),
                    "files": [],
                }

    return [results_by_host[host] for host in hosts]


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

    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the upload plan confirmation prompt.",
    )

    parser.add_argument(
        "-w",
        "--workers",
        type=worker_count,
        default=None,
        help=(
            f"Number of devices to process at the same time, 1-{MAX_WORKERS}. "
            f"If omitted, the script asks. Default answer: {DEFAULT_WORKERS}"
        ),
    )

    args = parser.parse_args()

    if not os.path.isfile(args.hosts_file):
        create_hosts_file(args.hosts_file)
        print(f"Hosts.txt file not found, so I created one for you :) {args.hosts_file}")
        print("Add one device IP address or hostname per line, then run the script again.")
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

    try:
        upload_confirmed = confirm_upload_plan(hosts, jobs, args)

    except Exception as exc:
        print(f"ERROR during upload preflight: {exc}")
        return 1

    if not upload_confirmed:
        print("Upload cancelled. No devices were changed.")
        return 1

    args.username = input("Enter username: ").strip()
    args.password = getpass("Enter password: ")
    args.workers = args.workers or prompt_worker_count()

    if not args.username:
        print("ERROR: Username cannot be empty.")
        return 1

    print(f"Loaded {len(hosts)} host(s).")
    print(f"Loaded {len(jobs)} file job(s).")

    results = process_hosts(hosts, args, jobs)

    print_final_summary(results)

    failed_hosts = [result["host"] for result in results if not result["success"]]

    if failed_hosts:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
