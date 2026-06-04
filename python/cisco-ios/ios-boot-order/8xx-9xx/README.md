# IOS Boot Order Script for 8xx/9xx

## Overview

`ios-boot-order_8xx-9xx.py` configures the boot order on Cisco 8xx and 9xx routers.

The script reads device IP addresses from `hosts.txt`, connects to each device by SSH, detects the router model, checks the currently running IOS image, and verifies that the expected new image exists in flash.

If the expected image is present, the script configures the boot order and saves the configuration.

## Boot Order

The script sets the boot order to:

1. The expected new IOS image.
2. The current running IOS image as the backup image.


## Adding a New Software Release

When a new IOS software release is approved, do these steps before running the script.

1. Copy the new `.bin` image to flash on the devices.

2. Check the image name and file size on one device:

```text
dir flash:new-c900-image.bin
```

3. Edit `ios-boot-order_8xx-9xx.py`.

4. Find this section near the top of the file:

```python
EXPECTED_IMAGES = {
    "800": "c800-universalk9-mz.SPA.159-3.M13.bin",
    "900": "c900-universalk9-mz.SPA.159-3.M13.bin",
}

EXPECTED_IMAGE_SIZE = {
    "c800-universalk9-mz.SPA.159-3.M13.bin": 97436536,
    "c900-universalk9-mz.SPA.159-3.M13.bin": 65946792,
}
```

5. Replace the old image names and sizes with the new approved image names and exact byte sizes:

```python
EXPECTED_IMAGES = {
    "800": "new-c800-image.bin",
    "900": "new-c900-image.bin",
}

EXPECTED_IMAGE_SIZE = {
    "new-c800-image.bin": 12345678,
    "new-c900-image.bin": 87654321,
}
```

6. Run the script only after the image exists in flash!

File size note: the size must be the exact byte count. Check it with `dir | i bin` before upload.

## Running the Script

Create or update `hosts.txt` with one device IP address per line.

Then run:

```bash
python3 ios-boot-order_8xx-9xx.py
```

If `hosts.txt` is missing or empty, the script will let you paste device IP addresses manually.

## Results

The script prints a summary after all devices are processed.

Common results are:

- `BOOT_SET`: the boot order was configured successfully.
- `WITHOUT_IMAGE`: the expected new image was not found in flash or the file size did not match.
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
