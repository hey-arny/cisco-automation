# IOS Boot Order Script for 8xx/9xx

## Overview

`ios-boot-order_8xx-9xx.py` configures the boot order on Cisco 8xx and 9xx routers.

The script reads device IP addresses from `hosts.txt`, connects to each device by SSH, detects the router model, checks the currently running IOS image, and verifies that the expected new image exists in flash.

If the expected image is present, the script configures the boot order and saves the configuration.

## Boot Order

The script sets the boot order to:

1. The expected new IOS image.
2. The current running IOS image as the backup image.

Important: this script changes the device configuration and runs `write memory`.

## Running the Script

Create or update `hosts.txt` with one device IP address per line.

Then run:

```bash
python3 ios-boot-order_8xx-9xx.py
```

The script will ask for your username, password, and the number of parallel SSH sessions.

If `hosts.txt` is missing or empty, the script will let you paste device IP addresses manually.

## Results

The script prints a summary after all devices are processed.

Common results are:

- `BOOT_SET`: the boot order was configured successfully.
- `WITHOUT_IMAGE`: the expected new image was not found in flash.
- `SKIPPED`: the device model was not supported or could not be detected.
- `FAILED`: the device failed because of SSH, authentication, or another error.

## Logs

Detailed results are saved to:

```text
boot_sequence_results.txt
```

Netmiko session logs are saved to:

```text
session_logs/
```
