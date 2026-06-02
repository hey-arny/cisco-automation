#!/usr/bin/env python3

from concurrent.futures import ThreadPoolExecutor, as_completed
from getpass import getpass
import os
import re
import sys
import time

try:
    from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException
except ImportError:
    from netmiko import ConnectHandler
    try:
        from netmiko.ssh_exception import NetMikoTimeoutException as NetmikoTimeoutException
        from netmiko.ssh_exception import NetMikoAuthenticationException as NetmikoAuthenticationException
    except ImportError:
        NetmikoTimeoutException = Exception
        NetmikoAuthenticationException = Exception


# ============================================================
# USER VARIABLES
# ============================================================

EXPECTED_IMAGES = {
    "800": "c800-universalk9-mz.SPA.159-3.M13.bin",
    "900": "c900-universalk9-mz.SPA.159-3.M13.bin",
}

DEVICE_TYPE = "cisco_ios"

HOSTS_FILE = "hosts.txt"
RESULTS_FILE = "boot_sequence_results.txt"
SESSION_LOG_DIR = "session_logs"

DEFAULT_WORKERS = 3
MAX_WORKERS_LIMIT = 10

SSH_TIMEOUT = 20
COMMAND_DELAY = 2


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def safe_filename(text):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def clean_host_line(line):
    """
    Allows IPs pasted from Excel, separated by new lines, spaces, commas or semicolons.
    """
    line = line.strip()

    if not line or line.startswith("#"):
        return []

    parts = re.split(r"[,\s;]+", line)
    return [p.strip() for p in parts if p.strip()]


def load_hosts():
    """
    If hosts.txt exists, devices are loaded from it.
    If hosts.txt does not exist or is empty, user can paste IPs manually.
    """
    hosts = []

    if os.path.exists(HOSTS_FILE):
        with open(HOSTS_FILE, "r") as f:
            for line in f:
                hosts.extend(clean_host_line(line))

    if not hosts:
        print("Paste device IPs/hostnames, one per line.")
        print("Press ENTER on an empty line to start.\n")

        while True:
            try:
                line = input()
            except EOFError:
                break

            if not line.strip():
                break

            hosts.extend(clean_host_line(line))

    unique_hosts = []
    seen = set()

    for host in hosts:
        if host not in seen:
            unique_hosts.append(host)
            seen.add(host)

    return unique_hosts


def get_worker_count():
    try:
        workers_input = input(
            "Enter number of parallel SSH sessions [{}]: ".format(DEFAULT_WORKERS)
        ).strip()

        if workers_input:
            workers = int(workers_input)
        else:
            workers = DEFAULT_WORKERS

        workers = max(1, min(workers, MAX_WORKERS_LIMIT))
        return workers

    except ValueError:
        return DEFAULT_WORKERS


def run_show(connection, command, strip_command=False, strip_prompt=False):
    return connection.send_command_timing(
        command,
        delay_factor=COMMAND_DELAY,
        max_loops=300,
        strip_prompt=strip_prompt,
        strip_command=strip_command
    )


def validate_expected_image_name(expected_image):
    """
    Prevent dangerous or broken image values.

    Good:
      c800-universalk9-mz.SPA.159-3.M13.bin

    Bad:
      .bin
      flash:.bin
      flash:
      /image.bin
      image with spaces.bin
    """
    if not expected_image:
        return False

    if not expected_image.endswith(".bin"):
        return False

    if expected_image.startswith("."):
        return False

    if expected_image.startswith("/"):
        return False

    if ":" in expected_image:
        return False

    if " " in expected_image:
        return False

    if expected_image.count(".bin") != 1:
        return False

    return True


def detect_pid_from_inventory(output):
    """
    Finds PID from show inventory output.

    Example:
      PID: C892FSP-K9        , VID: V02, SN: FCZ2137E218
    """
    pids = re.findall(r"PID:\s*([A-Za-z0-9\-]+)", output)

    for pid in pids:
        pid = pid.strip().upper()

        if pid.startswith("C8") or pid.startswith("C9"):
            return pid

    if pids:
        return pids[0].strip().upper()

    return None


def detect_family_from_pid(pid):
    """
    Simple model family detection.

    Examples:
      C892FSP-K9 -> 800
      C888-K9    -> 800
      C921-4P    -> 900
      C931-4P    -> 900
    """
    if not pid:
        return None

    pid = pid.upper().strip()

    if re.match(r"^C8\d+", pid):
        return "800"

    if re.match(r"^C9\d+", pid):
        return "900"

    return None


def parse_system_image(output):
    """
    Parses:
      System image file is "flash:c800-universalk9-mz.SPA.157-3.M7.bin"
      System image file is "flash:/c800-universalk9-mz.SPA.157-3.M7.bin"

    Returns:
      current_image_path
      storage
      current_image_filename
    """
    match = re.search(r'System image file is\s+"([^"]+)"', output)

    if not match:
        return None, None, None

    current_image_path = match.group(1).strip()

    storage_match = re.match(r"^([^:]+:)(.*)$", current_image_path)

    if not storage_match:
        return current_image_path, "flash:", current_image_path

    storage = storage_match.group(1)
    filename = storage_match.group(2).lstrip("/")

    return current_image_path, storage, filename


def build_expected_boot_path(storage, current_image_path, expected_image):
    """
    Keeps the same flash path format as the currently booted image.

    If current path is:
      flash:c800.bin

    expected path will be:
      flash:c800-new.bin

    If current path is:
      flash:/c800.bin

    expected path will be:
      flash:/c800-new.bin
    """
    if current_image_path and re.match(r"^[^:]+:/", current_image_path):
        return "{}{}".format(storage, "/" + expected_image)

    return "{}{}".format(storage, expected_image)


def image_exists_in_directory(connection, expected_boot_path, expected_image):
    """
    Safely checks if expected image exists.

    IMPORTANT FIX:
    The old logic used:
      dir flash: | include expected-image.bin

    Because strip_command=False, Netmiko output could include the command itself.
    That caused a false positive because the expected image name was present
    in the command, even when the file was not present on the device.

    This function checks the exact file path directly:
      dir flash:c800-universalk9-mz.SPA.159-3.M13.bin
      dir flash:/c800-universalk9-mz.SPA.159-3.M13.bin

    And uses strip_command=True, so the command itself cannot be matched.
    """
    command = "dir {}".format(expected_boot_path)

    output = run_show(
        connection,
        command,
        strip_command=True,
        strip_prompt=True
    )

    lower_output = output.lower()

    error_patterns = [
        "%error",
        "% error",
        "no such file",
        "not found",
        "invalid input",
        "permission denied",
        "unable to stat",
        "error opening",
    ]

    for error in error_patterns:
        if error in lower_output:
            return False, "\n### COMMAND: {}\n{}".format(command, output)

    if expected_image.lower() in lower_output:
        return True, "\n### COMMAND: {}\n{}".format(command, output)

    return False, "\n### COMMAND: {}\n{}".format(command, output)


def ensure_enable_mode(connection, enable_password):
    prompt = connection.find_prompt()

    if prompt.endswith("#"):
        return True, "Already in enable mode."

    if not enable_password:
        return False, "Device is not in enable mode."

    try:
        connection.enable()
        prompt = connection.find_prompt()

        if prompt.endswith("#"):
            return True, "Entered enable mode."

        return False, "Failed to enter enable mode."

    except Exception as error:
        return False, "Enable failed: {}".format(str(error))


def save_config(connection):
    output = connection.send_command_timing(
        "write memory",
        delay_factor=COMMAND_DELAY,
        max_loops=300,
        strip_prompt=False,
        strip_command=False
    )

    if "confirm" in output.lower() or "[confirm]" in output.lower():
        output += connection.send_command_timing(
            "\n",
            delay_factor=COMMAND_DELAY,
            max_loops=300,
            strip_prompt=False,
            strip_command=False
        )

    return output


def verify_final_boot_config(verify_output, expected_lines):
    """
    Verifies that the final running-config contains the boot lines
    that the script wanted to configure.
    """
    missing_lines = []

    for line in expected_lines:
        if line not in verify_output:
            missing_lines.append(line)

    if missing_lines:
        return False, missing_lines

    return True, []


# ============================================================
# DEVICE PROCESSING
# ============================================================

def process_device(host, username, password, enable_password):
    device_result = {
        "host": host,
        "status": None,
        "pid": None,
        "family": None,
        "expected_image": None,
        "current_image": None,
        "message": "",
        "details": "",
        "final_boot": "",
    }

    session_log_file = os.path.join(
        SESSION_LOG_DIR,
        "netmiko_{}.log".format(safe_filename(host))
    )

    device = {
        "device_type": DEVICE_TYPE,
        "host": host,
        "username": username,
        "password": password,
        "secret": enable_password,
        "port": 22,
        "timeout": SSH_TIMEOUT,
        "fast_cli": False,
        "global_delay_factor": 2,
        "session_log": session_log_file,
    }

    connection = None

    try:
        connection = ConnectHandler(**device)

        run_show(connection, "terminal length 0")

        ok, enable_msg = ensure_enable_mode(connection, enable_password)
        device_result["details"] += "\nENABLE CHECK:\n{}\n".format(enable_msg)

        if not ok:
            device_result["status"] = "FAILED"
            device_result["message"] = enable_msg
            return device_result

        # 1) Detect PID/model
        inventory_output = run_show(connection, "show inventory")

        pid = detect_pid_from_inventory(inventory_output)
        family = detect_family_from_pid(pid)

        device_result["pid"] = pid
        device_result["family"] = family
        device_result["details"] += "\nSHOW INVENTORY:\n{}\n".format(inventory_output)

        if not pid:
            device_result["status"] = "SKIPPED"
            device_result["message"] = "Could not detect PID from show inventory."
            return device_result

        if not family:
            device_result["status"] = "SKIPPED"
            device_result["message"] = "Unsupported model/PID: {}".format(pid)
            return device_result

        # 2) Select expected image
        expected_image = EXPECTED_IMAGES.get(family)
        device_result["expected_image"] = expected_image

        if not expected_image:
            device_result["status"] = "SKIPPED"
            device_result["message"] = "No expected image defined for family {}.".format(family)
            return device_result

        if not validate_expected_image_name(expected_image):
            device_result["status"] = "SKIPPED"
            device_result["message"] = "Unsafe expected image name: {}".format(expected_image)
            return device_result

        # 3) Detect currently booted image
        system_image_output = run_show(connection, "show version | include System image")

        current_image_path, storage, current_image_file = parse_system_image(system_image_output)

        device_result["current_image"] = current_image_path
        device_result["details"] += "\nSHOW VERSION SYSTEM IMAGE:\n{}\n".format(system_image_output)

        if not current_image_path:
            device_result["status"] = "SKIPPED"
            device_result["message"] = "Could not detect current system image."
            return device_result

        if not current_image_path.lower().endswith(".bin"):
            device_result["status"] = "SKIPPED"
            device_result["message"] = "Current system image is not a .bin file: {}".format(current_image_path)
            return device_result

        # 4) Build expected image path in the same style as the current image path
        expected_boot_path = build_expected_boot_path(
            storage,
            current_image_path,
            expected_image
        )

        # 5) SAFELY check if expected newest image really exists
        exists, dir_output = image_exists_in_directory(
            connection,
            expected_boot_path,
            expected_image
        )

        device_result["details"] += "\nDIRECTORY CHECK:\n{}\n".format(dir_output)

        if not exists:
            device_result["status"] = "WITHOUT_IMAGE"
            device_result["message"] = "Expected image is missing: {}".format(expected_image)
            return device_result

        # 6) Configure boot sequence
        config_commands = [
            "no boot system",
            "boot system {}".format(expected_boot_path),
        ]

        # Add currently booted image as backup only if it is different from expected image
        if current_image_path.lower() != expected_boot_path.lower():
            config_commands.append("boot system {}".format(current_image_path))

        config_output = connection.send_config_set(
            config_commands,
            delay_factor=COMMAND_DELAY
        )

        save_output = save_config(connection)

        verify_output = run_show(
            connection,
            "show running-config | include ^boot system"
        )

        expected_lines = []
        for command in config_commands:
            if command.startswith("boot system "):
                expected_lines.append(command)

        verify_ok, missing_lines = verify_final_boot_config(
            verify_output,
            expected_lines
        )

        device_result["details"] += "\nCONFIG COMMANDS:\n{}\n".format("\n".join(config_commands))
        device_result["details"] += "\nCONFIG OUTPUT:\n{}\n".format(config_output)
        device_result["details"] += "\nSAVE OUTPUT:\n{}\n".format(save_output)
        device_result["details"] += "\nFINAL BOOT CONFIG:\n{}\n".format(verify_output)

        device_result["final_boot"] = verify_output

        if not verify_ok:
            device_result["status"] = "FAILED"
            device_result["message"] = "Final boot config does not match expected boot lines."
            device_result["details"] += "\nMISSING BOOT LINES:\n{}\n".format("\n".join(missing_lines))
            return device_result

        device_result["status"] = "BOOT_SET"
        device_result["message"] = "Boot sequence configured successfully."

        return device_result

    except NetmikoAuthenticationException:
        device_result["status"] = "FAILED"
        device_result["message"] = "Authentication failed"
        return device_result

    except NetmikoTimeoutException:
        device_result["status"] = "FAILED"
        device_result["message"] = "SSH timeout / device unreachable"
        return device_result

    except Exception as error:
        device_result["status"] = "FAILED"
        device_result["message"] = str(error)
        return device_result

    finally:
        if connection:
            try:
                connection.disconnect()
            except Exception:
                pass


# ============================================================
# OUTPUT FUNCTIONS
# ============================================================

def print_section(title, items):
    print("\n{}".format(title))
    print("-" * len(title))

    if not items:
        print("None")
        return

    for item in items:
        print(item)


def write_results_file(all_results, hosts, boot_set, without_image, skipped, failed):
    with open(RESULTS_FILE, "w") as f:
        f.write("====================\n")
        f.write("COPY/PASTE SUMMARY\n")
        f.write("====================\n\n")

        f.write("IP WITH .BIN FILES AND BOOT SEQUENCE SET:\n")
        for ip in [h for h in hosts if h in boot_set]:
            f.write("{}\n".format(ip))

        f.write("\nIP WITHOUT NEWEST .BIN:\n")
        for ip in [h for h in hosts if h in without_image]:
            f.write("{}\n".format(ip))

        f.write("\nIP SKIPPED / UNKNOWN MODEL:\n")
        for ip in [h for h in hosts if h in skipped]:
            f.write("{}\n".format(ip))

        f.write("\nIP FAILED / CONNECTION ERROR:\n")
        for ip in [h for h in hosts if h in failed]:
            f.write("{}\n".format(ip))

        f.write("\n\n====================\n")
        f.write("DETAILED RESULTS\n")
        f.write("====================\n")

        for result in all_results:
            f.write("\n\n--------------------\n")
            f.write("HOST: {}\n".format(result["host"]))
            f.write("STATUS: {}\n".format(result["status"]))
            f.write("PID: {}\n".format(result["pid"]))
            f.write("FAMILY: {}\n".format(result["family"]))
            f.write("EXPECTED IMAGE: {}\n".format(result["expected_image"]))
            f.write("CURRENT IMAGE: {}\n".format(result["current_image"]))
            f.write("MESSAGE: {}\n".format(result["message"]))

            if result.get("final_boot"):
                f.write("\nFINAL BOOT CONFIG:\n")
                f.write(result["final_boot"])
                f.write("\n")

            f.write("\nDETAILS:\n")
            f.write(result["details"])
            f.write("\n")


# ============================================================
# MAIN
# ============================================================

def main():
    hosts = load_hosts()

    if not hosts:
        print("No hosts provided. Exiting.")
        sys.exit(1)

    username = input("Username: ").strip()
    password = getpass("Password: ")
    enable_password = ""

    max_workers = get_worker_count()

    os.makedirs(SESSION_LOG_DIR, exist_ok=True)

    print("\nStarting boot sequence check...")
    print("Devices loaded: {}".format(len(hosts)))
    print("Parallel SSH sessions: {}".format(max_workers))
    print("Session logs directory: ./{}".format(SESSION_LOG_DIR))
    print("")

    boot_set = []
    without_image = []
    skipped = []
    failed = []
    all_results = []

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_host = {}

        for host in hosts:
            print("[QUEUED] {}".format(host))

            future = executor.submit(
                process_device,
                host,
                username,
                password,
                enable_password
            )

            future_to_host[future] = host

        for future in as_completed(future_to_host):
            host = future_to_host[future]

            try:
                result = future.result()
            except Exception as error:
                result = {
                    "host": host,
                    "status": "FAILED",
                    "pid": None,
                    "family": None,
                    "expected_image": None,
                    "current_image": None,
                    "message": str(error),
                    "details": "",
                    "final_boot": "",
                }

            all_results.append(result)

            status = result["status"]

            if status == "BOOT_SET":
                boot_set.append(host)
                print("\n[OK] {} - boot sequence set".format(host))
                print(result.get("final_boot", ""))

            elif status == "WITHOUT_IMAGE":
                without_image.append(host)
                print("\n[MISSING IMAGE] {} - {}".format(host, result["message"]))

            elif status == "SKIPPED":
                skipped.append(host)
                print("\n[SKIPPED] {} - {}".format(host, result["message"]))

            else:
                failed.append(host)
                print("\n[FAILED] {} - {}".format(host, result["message"]))

    # Keep final output ordered by original host list
    result_by_host = {}
    for result in all_results:
        result_by_host[result["host"]] = result

    ordered_results = []
    for host in hosts:
        if host in result_by_host:
            ordered_results.append(result_by_host[host])

    ordered_boot_set = [h for h in hosts if h in boot_set]
    ordered_without_image = [h for h in hosts if h in without_image]
    ordered_skipped = [h for h in hosts if h in skipped]
    ordered_failed = [h for h in hosts if h in failed]

    print("\n\n====================")
    print("COPY/PASTE SUMMARY")
    print("====================")

    print_section("IP WITH .BIN FILES AND BOOT SEQUENCE SET:", ordered_boot_set)
    print_section("IP WITHOUT NEWEST .BIN:", ordered_without_image)
    print_section("IP SKIPPED / UNKNOWN MODEL:", ordered_skipped)
    print_section("IP FAILED / CONNECTION ERROR:", ordered_failed)

    write_results_file(
        ordered_results,
        hosts,
        ordered_boot_set,
        ordered_without_image,
        ordered_skipped,
        ordered_failed
    )

    duration = int(time.time() - start_time)

    print("\nDetailed results saved to: {}".format(RESULTS_FILE))
    print("Session logs saved in: ./{}".format(SESSION_LOG_DIR))
    print("Finished in {} seconds.".format(duration))


if __name__ == "__main__":
    main()
