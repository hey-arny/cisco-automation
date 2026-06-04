# Cisco IOS Image Cleanup

This script connects to Cisco IOS devices and removes old IOS `.bin` images from the same storage where the currently booted image is located.

It is intended for classic Cisco IOS devices such as Cisco 800 and 900 series routers.

## What It Does

For each device listed in `hosts.txt`, `cleanup.py` will:

1. Connect to the device by SSH using Netmiko.
2. Run `show version | include System image file`.
3. Detect the currently booted IOS image.
4. Detect the storage location, for example `flash:`.
5. Run `dir flash: | include .bin`.
6. Build a list of extra `.bin` files.
7. Keep the currently booted image.
8. Keep any image listed in `PROTECTED_IMAGES`.
9. Delete only allowed extra images when `DRY_RUN = False`.
10. Write logs and a summary beside the script.

The script refuses to delete anything if it cannot detect the current image or if the current image is not visible in the `dir` output.


## Safety Settings

### DRY_RUN

`DRY_RUN` controls whether files are actually deleted.

```python
DRY_RUN = True
```

Shows what would be deleted. No files are removed.

```python
DRY_RUN = False
```

Deletes the extra images.

### PROTECTED_IMAGES

Use `PROTECTED_IMAGES` to keep specific IOS images even if they are not currently booted.

Example:

```python
PROTECTED_IMAGES = [
    "c800-universalk9-mz.SPA.159-3.M13.bin",
    "flash:c900-universalk9-mz.SPA.159-3.M13.bin",
]
```

You can use just the filename or the full storage path. Matching is case-insensitive.

## Amend For Other IOS Releases

To preserve a new target release, add it to `PROTECTED_IMAGES`.

Example for a new Cisco 800 release:

```python
PROTECTED_IMAGES = [
    "c800-universalk9-mz.SPA.159-3.M13.bin",
    "c800-universalk9-mz.SPA.159-3.M14.bin",
]
```

The currently booted image is always protected automatically, even if it is not listed in `PROTECTED_IMAGES`.

## Amend For Other Device Families

The script only considers images whose filenames start with prefixes in `ALLOWED_IMAGE_PREFIXES`.

Current setting:

```python
ALLOWED_IMAGE_PREFIXES = ("c800-", "c900-")
```

For another platform, add the correct image filename prefix.

Example for ISR 4000 images:

```python
ALLOWED_IMAGE_PREFIXES = ("c800-", "c900-", "isr4400-")
```

Only add prefixes you actually want the script to clean up. Any `.bin` file with a prefix not listed here will be kept.

