# IOS SCP Upload

`ios-scp.py` uploads  any file IOS file you desire to Cisco IOS/IOS-XE devices using SCP.

The script reads device IP addresses from `hosts.txt`, verifies path from what to upload in  `iosfiles.txt`, copies each active file to each device, and verifies the uploaded file with MD5.

## What It Does

For each device, the script will:

1. Connect by SSH using Netmiko.
2. Enable SCP on the device.
3. Check available space on the target filesystem.
4. Upload each active file listed in `iosfiles.txt`.
5. Verify the remote file MD5.
6. Revert the SCP-related configuration.
7. Print a final pass/fail summary.

## Files

### hosts.txt

Create `hosts.txt` in the same directory as the script.

Add one device IP address or hostname per line:

```text
192.0.2.10
192.0.2.11
```


### iosfiles.txt

Add one upload job per active line:

```text
/local/path/to/image.bin:store=flash:;dest=image.bin;md5=expectedmd5value
```

Format:

```text
<local_file_path>:store=<device_filesystem>;dest=<remote_filename>;md5=<expected_md5>
```

`dest=` is optional. If omitted, the original local filename is used on the device.

Example:

```text
/home/user/ios/c800-universalk9-mz.SPA.159-3.M13.bin:store=flash:;dest=c800-universalk9-mz.SPA.159-3.M13.bin;md5=eed589c9309e724c101a0324ff2a5446
```

## Run

From this directory:

```bash
python3 ios-scp.py
```

The script will show an upload plan before making changes. Type `YES` to continue.

You will then be prompted for:

- SSH username
- SSH password
- Number of devices to process at the same time, maximum is 5 ! 

## Useful Options

```bash
python3 ios-scp.py --workers 2
```

Set the number of devices processed at the same time.

```bash
python3 ios-scp.py --yes
```


## Notes

- Every non commented out  file in `iosfiles.txt` is uploaded to every active device in `hosts.txt`! 
- A failed MD5 check marks the device as failed.

