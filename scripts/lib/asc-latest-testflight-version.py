"""Print "<version> <build>" of the newest TestFlight build for a bundle id.

Used by scripts/release-testflight.sh to refuse a same-version re-upload
(ADR 0029: /VERSION is hand-bumped per meaningful release — testers can only
tell releases apart by the marketing version, so it must actually change).

Reads ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH from the environment (the
release script has them loaded from .deploy.env). Needs the `cryptography`
package for the ES256 request token; the caller picks an interpreter that has
it and skips the check when none exists. Read-only: two GETs, no mutations.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token() -> str:
    key_path = os.path.expanduser(os.environ["ASC_KEY_PATH"])
    with open(key_path, "rb") as file:
        private_key = serialization.load_pem_private_key(file.read(), password=None)
    header = {"alg": "ES256", "kid": os.environ["ASC_KEY_ID"], "typ": "JWT"}
    now = int(time.time())
    payload = {
        "iss": os.environ["ASC_ISSUER_ID"],
        "iat": now,
        "exp": now + 300,
        "aud": "appstoreconnect-v1",
    }
    signing_input = _b64url(json.dumps(header).encode()) + "." + _b64url(
        json.dumps(payload).encode()
    )
    der = private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return signing_input + "." + _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: asc-latest-testflight-version.py <bundle-id>", file=sys.stderr)
        return 2
    token = _token()

    def get(path: str) -> dict:
        request = urllib.request.Request(
            "https://api.appstoreconnect.apple.com" + path,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    apps = get(f"/v1/apps?filter[bundleId]={sys.argv[1]}")["data"]
    if not apps:
        print("no app for bundle id", file=sys.stderr)
        return 1
    builds = get(
        f"/v1/builds?filter[app]={apps[0]['id']}&sort=-uploadedDate&limit=1"
        "&fields[builds]=version&include=preReleaseVersion"
        "&fields[preReleaseVersions]=version"
    )
    if not builds["data"]:
        return 1  # no builds yet: nothing to collide with
    marketing = builds.get("included", [{}])[0].get("attributes", {}).get("version", "?")
    print(marketing, builds["data"][0]["attributes"]["version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
