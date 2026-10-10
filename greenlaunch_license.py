"""GreenLaunch license check for Python Mac apps (Verbatim, Vectorize, Aptus).

Copied unchanged from apps/license-service/clients/python except this line. Keep the copies in sync.

How the app uses it:

    from greenlaunch_license import LicenseManager

    lm = LicenseManager(app="aptus")
    if not lm.is_licensed():
        # Show the "Enter your license key" screen, then:
        result = lm.activate(key_the_user_typed)
        if not result.ok:
            show_error(result.message)

    # Optional, once in a while when online (for example weekly):
    lm.refresh_if_due()

Needs the `cryptography` package (pip install cryptography).

The license file is checked offline on every launch with the public key below.
Only activation, deactivation, and the occasional refresh go online.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Paste the PUBLIC KEY printed by scripts/generate-keys.mjs here. It's safe to
# ship inside the app. Never put the private key in an app.
PUBLIC_KEY_B64 = "REPLACE_WITH_PUBLIC_KEY"

# The URL Cloudflare gives your Worker, or a custom domain like
# https://license.greenlaunch.co
LICENSE_SERVER = "https://license.greenlaunch.co"

REFRESH_EVERY_SECONDS = 7 * 24 * 60 * 60  # check for refunds about once a week


@dataclass
class Result:
    ok: bool
    message: str = ""


MESSAGES = {
    "invalid_key": "That license key wasn't found. Check it against your purchase email.",
    "wrong_app": "That key is for a different GreenLaunch app.",
    "revoked": "That license was refunded, so it can't be activated.",
    "activation_limit": "That key is already active on its maximum number of Macs. Deactivate it on another Mac first.",
    "missing_fields": "Enter your license key.",
}


class LicenseManager:
    def __init__(self, app: str, public_key_b64: str = PUBLIC_KEY_B64,
                 server: str = LICENSE_SERVER, storage_dir: Path | None = None,
                 machine_id: str | None = None):
        self.app = app.lower()
        self.server = server.rstrip("/")
        self._public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        self.storage_dir = storage_dir or (
            Path.home() / "Library" / "Application Support" / "GreenLaunch" / self.app
        )
        self.machine_id = machine_id or _machine_id(self.app)

    # --- Offline check (every launch) -------------------------------------

    def is_licensed(self) -> bool:
        payload = self.current_license()
        return payload is not None

    def current_license(self) -> dict | None:
        """Returns the license details if a valid license file is stored for
        this app on this Mac, otherwise None. Works with no internet."""
        token = self._read("license.txt")
        if not token:
            return None
        payload = self.verify_token(token)
        if not payload:
            return None
        if payload.get("app") != self.app or payload.get("machine") != self.machine_id:
            return None
        if self._read("revoked.txt") == "1":
            return None
        return payload

    def verify_token(self, token: str) -> dict | None:
        try:
            payload_part, sig_part = token.strip().split(".")
            self._public_key.verify(_b64url_decode(sig_part), payload_part.encode("ascii"))
            return json.loads(_b64url_decode(payload_part))
        except (ValueError, InvalidSignature, json.JSONDecodeError):
            return None

    # --- Online actions ----------------------------------------------------

    def activate(self, key: str) -> Result:
        status, body = self._post("/activate", {
            "key": key.strip(), "app": self.app, "machine_id": self.machine_id,
        })
        if status is None:
            return Result(False, "Couldn't reach the license server. Check your internet connection and try again.")
        if status == 200 and body.get("license"):
            if not self.verify_token(body["license"]):
                return Result(False, "The license server sent a file this app couldn't verify.")
            self._write("license.txt", body["license"])
            self._write("key.txt", key.strip().upper())
            self._write("revoked.txt", "0")
            self._write("last_refresh.txt", str(int(time.time())))
            return Result(True, "Activated. Thanks for supporting GreenLaunch.")
        return Result(False, MESSAGES.get(body.get("error", ""), "Activation didn't work. Email go@greenlaunch.co and we'll sort it out."))

    def deactivate(self) -> Result:
        key = self._read("key.txt")
        if key:
            self._post("/deactivate", {"key": key, "machine_id": self.machine_id})
        for name in ("license.txt", "key.txt", "revoked.txt", "last_refresh.txt"):
            try:
                (self.storage_dir / name).unlink()
            except FileNotFoundError:
                pass
        return Result(True, "This Mac's license slot is free.")

    def refresh_if_due(self) -> None:
        """Checks once a week, when online, whether the license was refunded.
        Fails quietly when offline so the app keeps working."""
        last = self._read("last_refresh.txt")
        if last and time.time() - int(last) < REFRESH_EVERY_SECONDS:
            return
        key = self._read("key.txt")
        if not key:
            return
        status, body = self._post("/validate", {"key": key, "machine_id": self.machine_id})
        if status != 200:
            return
        if body.get("status") == "revoked":
            self._write("revoked.txt", "1")
        self._write("last_refresh.txt", str(int(time.time())))

    # --- Helpers -------------------------------------------------------------

    def _post(self, path: str, data: dict):
        req = urllib.request.Request(
            self.server + path,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except json.JSONDecodeError:
                return e.code, {}
        except (urllib.error.URLError, TimeoutError, OSError):
            return None, {}

    def _read(self, name: str) -> str | None:
        try:
            return (self.storage_dir / name).read_text().strip()
        except FileNotFoundError:
            return None

    def _write(self, name: str, value: str) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / name).write_text(value)


def _machine_id(app: str) -> str:
    """A stable id for this Mac, hashed with the app name so the server never
    sees the raw hardware id."""
    raw = ""
    try:
        out = subprocess.run(
            ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                raw = line.split("=")[-1].strip().strip('"')
                break
    except (OSError, subprocess.SubprocessError):
        pass
    if not raw:
        # Fallback outside macOS (tests) or if ioreg is unavailable.
        raw = os.environ.get("GREENLAUNCH_MACHINE_ID", "unknown-machine")
    return hashlib.sha256(f"greenlaunch:{app}:{raw}".encode()).hexdigest()[:32]


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
