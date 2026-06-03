#!/usr/bin/env python3
from __future__ import print_function

import os
import re
import sys
import time
import queue
import datetime
import threading
import subprocess

from getpass import getpass
from netmiko import ConnectHandler


# =========================
# BASIC SETTINGS
# =========================

HOSTS_FILE = "hosts.txt"
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "verification.log")

PING_WORKERS = 30
VERIFY_WORKERS = 5

PING_TIMEOUT = 1
PING_INTERVAL = 5

DOWN_THRESHOLD = 3
UP_THRESHOLD = 3

SSH_WAIT_AFTER_UP = 30


# =========================
# EXPECTED IMAGES
# =========================

FAMILY_IMAGE_MAP = {
    "800": "c800-universalk9-mz.SPA.159-3.M13.bin",
    "900": "c900-universalk9-mz.SPA.159-3.M13.bin",
    "1100": "packages.conf"

}

MODEL_FAMILY_MAP = {
    # 800 models
    "C892FSP-K9": "800",
    "C891FSP-K9": "800",
    "C891F-K9": "800",
    "C888EA-K9": "800",
    "C888-K9": "800",

    # 900 models
    "C921-4P": "900",
    "C931-4P": "900",
    "C927-4P": "900",
    "C926-4P": "900",
    "C927-4PM": "900",

    # 900 LTE models
    "C921-4PLTEGB": "900",
    "C921-4PLTEAU": "900",
    "C921-4PLTEAS": "900",
    "C927-4PLTENA": "900",
    "C927-4PLTEGB": "900",
    "C927-4PLTEAU": "900",
    "C927-4PMLTEGB": "900",
    "C926-4PLTEGB": "900",

    # ISR1100 models
    
    "C1101": "1100",
    "C1109": "1100",

    "C1111": "1100",
    "C1112": "1100",
    "C1113": "1100",
    "C1116": "1100",
    "C1117": "1100",
    "C1118": "1100",

    "C1121": "1100",
    "C1126": "1100",
    "C1127": "1100",
    "C1128": "1100",

    "C1131": "1100",
    "C1161": "1100",


}


# =========================
# GLOBALS
# =========================

username = None
password = None

DEVICE_STATE = {}

PING_QUEUE = queue.Queue()
VERIFY_QUEUE = queue.Queue()

STATE_LOCK = threading.Lock()
LOG_LOCK = threading.Lock()

STOP_EVENT = threading.Event()


# =========================
# HELPERS
# =========================

def now():
    return datetime.datetime.now()


def format_time(dt):
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def seconds_between(start, end):
    if not start or not end:
        return None

    delta = end - start
    return delta.days * 86400 + delta.seconds


def format_duration(seconds):
    if seconds is None:
        return "N/A"

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return "%dh %dm %ds" % (hours, minutes, secs)

    if minutes > 0:
        return "%dm %ds" % (minutes, secs)

    return "%ds" % secs


def log(message):
    line = "[%s] %s" % (format_time(now()), message)

    LOG_LOCK.acquire()
    try:
        print(line)
        sys.stdout.flush()

        f = open(LOG_FILE, "a")
        try:
            f.write(line + "\n")
        finally:
            f.close()
    finally:
        LOG_LOCK.release()


def read_hosts(filename):
    hosts = []
    seen = set()

    f = open(filename, "r")
    try:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if line in seen:
                continue

            hosts.append(line)
            seen.add(line)
    finally:
        f.close()

    return hosts


def make_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def ensure_hosts_file():
    if not os.path.exists(HOSTS_FILE):
        open(HOSTS_FILE, "w").close()
        print("Created %s. Add device IP addresses and run the script again." % HOSTS_FILE)
        sys.exit(1)


def ping_host(ip):
    devnull = open(os.devnull, "w")
    try:
        cmd = ["ping", "-c", "1", "-W", str(PING_TIMEOUT), ip]
        result = subprocess.call(cmd, stdout=devnull, stderr=devnull)
        return result == 0
    finally:
        devnull.close()


def detect_pid(show_version_output):
    output_upper = show_version_output.upper()

    all_models = MODEL_FAMILY_MAP.keys()
    all_models = sorted(all_models, key=len, reverse=True)

    for model in all_models:
        if model.upper() in output_upper:
            return model

    match = re.search(r"[Mm]odel\s+[Nn]umber\s*:\s*(\S+)", show_version_output)
    if match:
        return match.group(1).strip()

    match = re.search(r"[Cc]isco\s+([A-Za-z0-9\-]+)\s+\(", show_version_output)
    if match:
        return match.group(1).strip()

    return None


def detect_running_image(show_version_output):
    match = re.search(
        r'System image file is\s+"?([^"\r\n]+)"?',
        show_version_output
    )

    if not match:
        return None

    image = match.group(1).strip()

    if ":" in image:
        image = image.split(":")[-1]

    if "/" in image:
        image = image.split("/")[-1]

    return image


def detect_uptime(show_version_output):
    match = re.search(r"(?m)^(.+?) uptime is (.+)$", show_version_output)

    if not match:
        return None

    return match.group(2).strip()

def get_expected_image_for_pid(pid):
    if not pid:
        return None

    family = MODEL_FAMILY_MAP.get(pid)

    if not family:
        return None

    return FAMILY_IMAGE_MAP.get(family)


# =========================
# STATE HANDLING
# =========================

def init_device_state(hosts):
    STATE_LOCK.acquire()
    try:
        for ip in hosts:
            DEVICE_STATE[ip] = {
                "ip": ip,
                "state": "WAITING_FOR_RELOAD",
                "seen_reachable": False,
                "fail_count": 0,
                "success_count": 0,
                "down_time": None,
                "up_time": None,
                "current_event": None,
                "events": [],
                "last_result": None,
                "last_error": None,
            }
    finally:
        STATE_LOCK.release()


def handle_ping_result(ip, is_up):
    messages = []
    verify_job = None

    STATE_LOCK.acquire()
    try:
        state = DEVICE_STATE[ip]

        if is_up:
            state["seen_reachable"] = True
            state["fail_count"] = 0

            if state["state"] in ["DOWN", "INITIALLY_DOWN"]:
                state["success_count"] += 1

                if state["success_count"] >= UP_THRESHOLD:
                    up_time = now()
                    down_time = state["down_time"]
                    downtime_seconds = seconds_between(down_time, up_time)

                    state["up_time"] = up_time
                    state["state"] = "VERIFY_QUEUED"
                    state["success_count"] = 0

                    event_index = state["current_event"]

                    if event_index is not None:
                        state["events"][event_index]["up"] = up_time
                        state["events"][event_index]["downtime"] = downtime_seconds

                    messages.append(
                        "%s UP detected, downtime %s, SSH verification queued"
                        % (ip, format_duration(downtime_seconds))
                    )

                    verify_job = (ip, event_index)

            else:
                state["success_count"] = 0

        else:
            state["success_count"] = 0
            state["fail_count"] += 1

            if state["state"] not in ["DOWN", "INITIALLY_DOWN"]:
                if state["fail_count"] >= DOWN_THRESHOLD:
                    down_time = now()

                    if state["seen_reachable"]:
                        new_state = "DOWN"
                        messages.append("%s DOWN detected" % ip)
                    else:
                        new_state = "INITIALLY_DOWN"
                        messages.append("%s is initially unreachable/DOWN" % ip)

                    event = {
                        "down": down_time,
                        "up": None,
                        "downtime": None,
                        "result": None,
                        "pid": None,
                        "expected_image": None,
                        "running_image": None,
                        "uptime": None,
                        "error": None,
                    }

                    state["state"] = new_state
                    state["down_time"] = down_time
                    state["up_time"] = None
                    state["events"].append(event)
                    state["current_event"] = len(state["events"]) - 1

    finally:
        STATE_LOCK.release()

    for message in messages:
        log(message)

    if verify_job:
        VERIFY_QUEUE.put(verify_job)


# =========================
# WORKERS
# =========================

def ping_worker():
    while not STOP_EVENT.is_set():
        try:
            ip = PING_QUEUE.get(timeout=1)
        except queue.Empty:
            continue

        try:
            is_up = ping_host(ip)
            handle_ping_result(ip, is_up)
        except Exception as e:
            log("%s ping worker error: %s" % (ip, str(e)))
        finally:
            PING_QUEUE.task_done()


def verify_worker():
    while not STOP_EVENT.is_set():
        try:
            job = VERIFY_QUEUE.get(timeout=1)
        except queue.Empty:
            continue

        ip = job[0]
        event_index = job[1]

        try:
            verify_device(ip, event_index)
        except Exception as e:
            log("%s verification worker error: %s" % (ip, str(e)))
        finally:
            VERIFY_QUEUE.task_done()


# =========================
# SSH VERIFICATION
# =========================

def verify_device(ip, event_index):
    STATE_LOCK.acquire()
    try:
        if ip in DEVICE_STATE:
            DEVICE_STATE[ip]["state"] = "VERIFYING"
    finally:
        STATE_LOCK.release()

    log("%s waiting %s seconds before SSH verification" % (ip, SSH_WAIT_AFTER_UP))
    time.sleep(SSH_WAIT_AFTER_UP)

    log("%s SSH verification started" % ip)

    pid = None
    expected_image = None
    running_image = None
    uptime = None
    result = None
    error = None

    net_connect = None

    try:
        device = {
            "device_type": "cisco_ios",
            "host": ip,
            "username": username,
            "password": password,
            "port": 22,
            "fast_cli": False,
            "timeout": 20,
            "banner_timeout": 15,
            "auth_timeout": 15,
        }

        net_connect = ConnectHandler(**device)

        show_version = net_connect.send_command_timing(
            "show version",
            delay_factor=2,
            max_loops=1000
        )

        pid = detect_pid(show_version)
        running_image = detect_running_image(show_version)
        uptime = detect_uptime(show_version)

        if pid:
            expected_image = get_expected_image_for_pid(pid)

        if not pid:
            result = "UNKNOWN_PID"

        elif not expected_image:
            result = "NO_EXPECTED_IMAGE_FOR_PID"

        elif not running_image:
            result = "RUNNING_IMAGE_NOT_FOUND"

        elif expected_image in running_image:
            result = "UPGRADED_OK"

        else:
            result = "WRONG_IMAGE"

    except Exception as e:
        result = "SSH_FAILED"
        error = str(e)

    finally:
        if net_connect:
            try:
                net_connect.disconnect()
            except Exception:
                pass

    log("%s PID: %s" % (ip, pid))
    log("%s Expected image: %s" % (ip, expected_image))
    log("%s Running image: %s" % (ip, running_image))
    log("%s Uptime: %s" % (ip, uptime))
    log("%s Result: %s" % (ip, result))

    if error:
        log("%s Error: %s" % (ip, error))

    STATE_LOCK.acquire()
    try:
        state = DEVICE_STATE[ip]

        state["last_result"] = result
        state["last_error"] = error

        if state["state"] not in ["DOWN", "INITIALLY_DOWN"]:
            state["state"] = result

        if event_index is not None:
            if event_index < len(state["events"]):
                state["events"][event_index]["result"] = result
                state["events"][event_index]["pid"] = pid
                state["events"][event_index]["expected_image"] = expected_image
                state["events"][event_index]["running_image"] = running_image
                state["events"][event_index]["uptime"] = uptime
                state["events"][event_index]["error"] = error

    finally:
        STATE_LOCK.release()


# =========================
# SUMMARY
# =========================

def print_summary():
    summary = {
        "UPGRADED_OK": [],
        "WRONG_IMAGE": [],
        "SSH_FAILED": [],
        "UNKNOWN_PID": [],
        "RUNNING_IMAGE_NOT_FOUND": [],
        "NO_EXPECTED_IMAGE_FOR_PID": [],
        "NO_RELOAD_DETECTED": [],
        "STILL_DOWN": [],
        "VERIFY_PENDING": [],
        "OTHER": [],
    }

    STATE_LOCK.acquire()
    try:
        for ip in sorted(DEVICE_STATE.keys()):
            state = DEVICE_STATE[ip]
            current_state = state["state"]
            last_result = state["last_result"]

            if len(state["events"]) == 0:
                summary["NO_RELOAD_DETECTED"].append(ip)

            elif current_state in ["DOWN", "INITIALLY_DOWN"]:
                summary["STILL_DOWN"].append(ip)

            elif current_state in ["VERIFY_QUEUED", "VERIFYING"]:
                summary["VERIFY_PENDING"].append(ip)

            elif last_result in summary:
                summary[last_result].append(ip)

            else:
                summary["OTHER"].append("%s - %s" % (ip, current_state))

    finally:
        STATE_LOCK.release()

    log("")
    log("================ SUMMARY ================")

    for key in [
        "UPGRADED_OK",
        "WRONG_IMAGE",
        "SSH_FAILED",
        "UNKNOWN_PID",
        "RUNNING_IMAGE_NOT_FOUND",
        "NO_EXPECTED_IMAGE_FOR_PID",
        "NO_RELOAD_DETECTED",
        "STILL_DOWN",
        "VERIFY_PENDING",
        "OTHER",
    ]:
        devices = summary[key]

        log("")
        log("%s: %s" % (key, len(devices)))

        for item in devices:
            log("  %s" % item)

    log("")
    log("=========================================")


# =========================
# MAIN
# =========================

def main():
    global username
    global password

    ensure_hosts_file()
    
    username = input("Enter username: ").strip()
    password = getpass("Password: ")

    hosts = read_hosts(HOSTS_FILE)

    if not hosts:
        print("No hosts found in %s" % HOSTS_FILE)
        sys.exit(1)

    make_dir(LOG_DIR)

    f = open(LOG_FILE, "w")
    try:
        f.write("")
    finally:
        f.close()

    init_device_state(hosts)

    log("Starting verification monitor")
    log("Loaded hosts: %s" % len(hosts))
    log("Ping workers: %s" % PING_WORKERS)
    log("SSH verification workers: %s" % VERIFY_WORKERS)
    log("Down threshold: %s failed pings" % DOWN_THRESHOLD)
    log("Up threshold: %s successful pings" % UP_THRESHOLD)
    log("SSH wait after UP: %s seconds" % SSH_WAIT_AFTER_UP)

    for i in range(PING_WORKERS):
        t = threading.Thread(target=ping_worker)
        t.setDaemon(True)
        t.start()

    for i in range(VERIFY_WORKERS):
        t = threading.Thread(target=verify_worker)
        t.setDaemon(True)
        t.start()

    try:
        while not STOP_EVENT.is_set():
            for ip in hosts:
                PING_QUEUE.put(ip)

            PING_QUEUE.join()

            time.sleep(PING_INTERVAL)

    except KeyboardInterrupt:
        log("CTRL+C detected, stopping monitor")

    finally:
        STOP_EVENT.set()
        print_summary()


if __name__ == "__main__":
    main()
