# Reload Monitor Script

## Overview

`ios-monitor.py` monitors Cisco routers during a reload window.

The script reads device IP addresses from `hosts.txt`, continuously pings each device, detects when a device goes down, waits for it to come back online, and then connects by SSH to verify the running IOS image.

This script does not reload devices. It only monitors devices after a reload has been started by another script or an engineer.

## How It Works

For each device, the script checks:

1. The device goes down.
2. The device comes back online.
3. SSH is available again.
4. The running IOS image matches the expected image for the detected device model.

If the running image is correct, the script logs `UPGRADED_OK`.

## Running the Script

Create or update `hosts.txt` with one device IP address per line.

Then run:

```bash
python3 ios-monitor.py
```

The script will ask for your username and password, then start monitoring all devices from `hosts.txt`.

Leave the script running during the reload window. Stop it with `CTRL+C` when monitoring is finished.

## Results

When you stop the script, it prints a summary with the final status for each device.

Common results are:

- `UPGRADED_OK`: the device is running the expected image.
- `WRONG_IMAGE`: the device is running a different image.
- `SSH_FAILED`: the device came back online, but SSH verification did not succeed.
- `NO_RELOAD_DETECTED`: the device did not go down while the script was running.
- `STILL_DOWN`: the device went down and did not come back online before the script was stopped.

## Logs

The script writes logs to:

```text
logs/verification.log
```

Review this log after the reload window to confirm the result for each device.
