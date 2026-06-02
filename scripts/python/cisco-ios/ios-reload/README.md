# IOS Reload Script

## Overview

`ios-reload.py` checks the boot configuration on Cisco C800 and C900 routers.

The script reads device IP addresses from `hosts.txt`, connects to each device, checks the current IOS image, verifies the boot order, and confirms that the expected new image exists in flash with the correct file size.

The expected boot order is:

1. The newest expected IOS image.
2. The current running IOS image as the backup image.

If all checks pass, the script logs `PASS` and saves the configuration with `write memory`.

There are two ways to use this script:

- Check the boot configuration only.
- Check the boot configuration and reload the device.

## Option 1: Check Boot Configuration Only

Use this option when you only want to verify the boot order and image file.

In `ios-reload.py`, keep:

```python
RELOAD_ENABLED = False
```

Then run:

```bash
python3 ios-reload.py
```


## Option 2: Check Boot Configuration and Reload

Use this option only when you want the script to reload devices after all checks pass.

In `ios-reload.py`, set:

```python
RELOAD_ENABLED = True
```

Then run:

```bash
python3 ios-reload.py
```

The script will check each device in `hosts.txt`. If all checks pass, it will save the configuration and reload the device.

Important: with `RELOAD_ENABLED = True`, the script sends the `reload` command automatically after a successful check!

