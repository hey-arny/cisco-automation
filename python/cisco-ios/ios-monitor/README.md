# Reload Monitor Script

## Overview

`ios-monitor.py` monitors Cisco routers during a reload window.

The script reads device IP addresses from `hosts.txt`, continuously pings each device, detects when a device goes down, waits for it to come back online, and then connects by SSH to verify the running IOS image.

The script currently supports 800, 900 (bundle mode), and 1100 family models in install mode. It can be easily updated for any model.

Just add your desired model and expected .bin/packages.conf version to `FAMILY_IMAGE_MAP`, and add the model variations to `MODEL_FAMILY_MAP`.


## Running the Script

Create or update `hosts.txt` with one device IP address per line. 

Then run:

```bash
python3 ios-monitor.py
```

Stop it with `CTRL+C`.

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
