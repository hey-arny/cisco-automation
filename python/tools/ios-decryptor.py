#!/usr/bin/env python3
"""Offline Cisco IOS Type 7 password decryptor."""

XLAT = "dsfd;kfoA,.iyewrkldJKDHSUB"


def decrypt_type7(password: str) -> str:
    password = password.strip()

    if len(password) < 4:
        raise ValueError("Password is too short to be Cisco Type 7.")

    if not password[:2].isdigit():
        raise ValueError("Cisco Type 7 passwords must start with a two-digit seed.")

    seed = int(password[:2])
    encrypted = password[2:]

    if seed >= len(XLAT):
        raise ValueError("Invalid Cisco Type 7 seed.")

    if len(encrypted) % 2 != 0:
        raise ValueError("Encrypted data must contain an even number of hex digits.")

    decrypted = []
    for index in range(0, len(encrypted), 2):
        try:
            value = int(encrypted[index:index + 2], 16)
        except ValueError as exc:
            raise ValueError("Encrypted data contains non-hex characters.") from exc

        key = ord(XLAT[(seed + index // 2) % len(XLAT)])
        decrypted.append(chr(value ^ key))

    return "".join(decrypted)


def main() -> int:
    print("Cisco IOS Type 7 password decryptor")
    print()
    print("Paste one or more Type 7 passwords, one per line.")
    print("Press Enter on an empty line when done.")
    print()

    passwords = []
    while True:
        password = input("> ").strip()
        if not password:
            break
        passwords.append(password)

    if not passwords:
        print("No passwords entered.")
        return 1

    print()
    print("Decrypted passwords:")
    print()

    had_error = False
    for password in passwords:
        try:
            print(decrypt_type7(password))
        except ValueError as exc:
            had_error = True
            print(f"ERROR for {password}: {exc}")

    print()
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
