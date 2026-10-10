from typing import Optional

import base64
import getpass
import json
import os

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# Load from local .env if present
try:
    from config import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_URL = os.getenv("ELNET_BASE_URL", "").rstrip("/")

# Set these in .env or environment variables from the decompiled Utility.java.
# Don't commit them to Git or share them.
skey_env = os.getenv("ELNET_SKEY", "")
ivkey_env = os.getenv("ELNET_IVKEY", "")

if not skey_env or not ivkey_env:
    raise ValueError(
        "ELNET_SKEY and ELNET_IVKEY must be configured in your .env file or environment variables."
    )

SKEY = skey_env[:32].encode()
IVKEY = ivkey_env[:16].encode()

if len(SKEY) != 32 or len(IVKEY) != 16:
    raise ValueError("Invalid AES key length (must be 32 bytes) or IV length (must be 16 bytes)")


def encrypt_json(payload: dict) -> str:
    plaintext = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    cipher = AES.new(SKEY, AES.MODE_CBC, IVKEY)
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(ciphertext).decode("ascii")


def post_encrypted(path: str, payload: dict, token: Optional[str] = None):
    headers = {"Content-Type": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = {"InputType": encrypt_json(payload)}

    # Intentionally one request per call; no automatic retries.
    response = requests.post(
        f"{BASE_URL}{path}",
        json=body,
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def main():
    username = input("ELNet username: ").strip()
    password = getpass.getpass("ELNet password: ")
    fcm_token = os.getenv("ELNET_FCM_TOKEN", "")

    login_payload = {
        "Access": "Live",
        "FCMID": fcm_token,
        "Password": password,
        "UserName": username,
    }

    login = post_encrypted("/api/Dashboard/login", login_payload)

    data = login.get("Data") or {}
    token = data.get("AuthToken")

    if str(login.get("Status", "")).lower() != "success" or not token:
        # Avoid printing the complete response: it may contain sensitive data.
        print("Login failed. Check the status/message in the app or response.")
        print("Status:", login.get("Status"))
        print("Message:", login.get("Message"))
        return

    meter_id = int(input("Your MeterID (the app's stored flat ID): "))

    result = post_encrypted(
        "/api/Dashboard/HomeData",
        {"MeterID": meter_id},
        token=token,
    )

    # Show the returned home-data response locally.
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
