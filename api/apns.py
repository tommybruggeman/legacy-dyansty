"""Apple Push Notification service (APNs), straight from our code: no Expo push service in between,
because Expo's service can't update Live Activities.

Needs an APNs auth key from the Apple developer account (Certificates, IDs & Profiles > Keys, with
Apple Push Notifications service turned on). It's free and never expires. Environment:

  APNS_KEY_P8   the .p8 file's text (BEGIN/END lines included)
  APNS_KEY_ID   the key's 10-character id
  APNS_TEAM_ID  the Apple team id (defaults to the app's team)
  APNS_HOST     api.push.apple.com (TestFlight and App Store builds); api.sandbox.push.apple.com for Xcode dev builds
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

BUNDLE_ID = "com.tommybruggeman.legacydynasty"
TEAM_ID = "UXB5ZFZLP9"
TOKEN_MINUTES = 50  # Apple wants a fresh signed token at least every hour, and not more than every 20 minutes
GONE = {"BadDeviceToken", "Unregistered", "ExpiredToken", "DeviceTokenNotForTopic"}


def configured() -> bool:
    return bool(os.getenv("APNS_KEY_P8", "").strip() and os.getenv("APNS_KEY_ID", "").strip())


@dataclass
class Result:
    status: int
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def gone(self) -> bool:
        """The activity or phone is gone for good: stop sending to this token."""
        return self.status == 410 or self.reason in GONE


@dataclass
class Client:
    key_p8: str
    key_id: str
    team_id: str = TEAM_ID
    host: str = "api.push.apple.com"
    _token: str = ""
    _token_at: float = 0.0
    _http: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "Client":
        return cls(
            key_p8=os.environ["APNS_KEY_P8"].replace("\\n", "\n").strip(),
            key_id=os.environ["APNS_KEY_ID"].strip(),
            team_id=os.getenv("APNS_TEAM_ID", "").strip() or TEAM_ID,
            host=os.getenv("APNS_HOST", "").strip() or "api.push.apple.com",
        )

    def bearer(self, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        if not self._token or now - self._token_at > TOKEN_MINUTES * 60:
            self._token = jwt.encode({"iss": self.team_id, "iat": int(now)}, self.key_p8, algorithm="ES256",
                                     headers={"kid": self.key_id})
            self._token_at = now
        return self._token

    def http(self) -> Any:
        if self._http is None:
            self._http = httpx.Client(http2=True, timeout=10.0)
        return self._http

    def send_live_activity(self, push_token: str, payload: dict[str, Any], priority: int = 10) -> Result:
        """One Live Activity push (update / end) to one activity's push token."""
        headers = {
            "authorization": f"bearer {self.bearer()}",
            "apns-push-type": "liveactivity",
            "apns-topic": f"{BUNDLE_ID}.push-type.liveactivity",
            "apns-priority": str(priority),
        }
        try:
            resp = self.http().post(f"https://{self.host}/3/device/{push_token}", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return Result(0, type(exc).__name__)
        reason = ""
        if resp.status_code != 200:
            try:
                reason = str((resp.json() or {}).get("reason") or "")
            except ValueError:
                reason = ""
        return Result(resp.status_code, reason)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None
