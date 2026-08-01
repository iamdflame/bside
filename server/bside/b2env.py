"""B2 environment bootstrap — forgiving input, exact output.

Real deployments hand us messy config: bucket *IDs* where names belong,
inline comments, missing regions. Rather than failing cryptically inside
boto3, resolve the truth once via the B2 native API (`b2_authorize_account`
returns the allowed bucket for a scoped key) and normalize os.environ so
`genblaze_s3` sees exactly what it expects.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.request
from pathlib import Path

log = logging.getLogger("bside.b2env")

_HEX_ID = re.compile(r"^[0-9a-f]{24}$")


def _clean(v: str) -> str:
    """Strip quotes and inline comments from an env value."""
    v = v.strip().strip("'\"")
    if " #" in v:
        v = v.split(" #", 1)[0].strip()
    if "\t#" in v:
        v = v.split("\t#", 1)[0].strip()
    return v


def load_dotenv(path: Path | None = None) -> None:
    env = path or Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), _clean(v)
        if k and v and not os.environ.get(k):
            os.environ[k] = v


def b2_authorize(key_id: str, app_key: str) -> dict:
    """Call B2 native b2_authorize_account. Returns the auth document."""
    token = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    req = urllib.request.Request(  # noqa: S310
        "https://api.backblazeb2.com/b2api/v3/b2_authorize_account",
        headers={"Authorization": f"Basic {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def b2_bucket_name_from_id(auth: dict, bucket_id: str) -> str | None:
    """Resolve a bucket name from its id via native b2_list_buckets."""
    storage = auth.get("apiInfo", {}).get("storageApi", {})
    api_url = storage.get("apiUrl") or auth.get("apiUrl", "")
    account_id = auth.get("accountId", "")
    token = auth.get("authorizationToken", "")
    if not (api_url and account_id and token):
        return None
    body = json.dumps({"accountId": account_id, "bucketId": bucket_id}).encode()
    req = urllib.request.Request(  # noqa: S310
        f"{api_url}/b2api/v3/b2_list_buckets",
        data=body,
        headers={"Authorization": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        doc = json.loads(resp.read().decode())
    buckets = doc.get("buckets", [])
    return buckets[0]["bucketName"] if buckets else None


def normalize_b2_env() -> dict[str, str]:
    """Ensure B2_BUCKET is a *name* and B2_REGION is exact.

    Accepts B2_BUCKET or B2_BUCKET_ID (name or 24-hex id). When given an id
    (or nothing but a scoped key), asks the B2 API for the allowed bucket.
    Returns a redacted summary for logs/health.
    """
    key_id = _clean(os.environ.get("B2_KEY_ID", ""))
    app_key = _clean(os.environ.get("B2_APP_KEY", ""))
    bucket = _clean(os.environ.get("B2_BUCKET", "") or os.environ.get("B2_BUCKET_ID", ""))
    region = _clean(os.environ.get("B2_REGION", ""))

    if key_id:
        os.environ["B2_KEY_ID"] = key_id
    if app_key:
        os.environ["B2_APP_KEY"] = app_key

    needs_lookup = bool(key_id and app_key) and (not bucket or _HEX_ID.fullmatch(bucket) or not region)
    if needs_lookup:
        try:
            auth = b2_authorize(key_id, app_key)
            storage = auth.get("apiInfo", {}).get("storageApi", {})
            allowed = storage.get("allowed", {}) or auth.get("allowed", {})
            allowed_name = allowed.get("bucketName")
            allowed_id = allowed.get("bucketId")
            # apiUrl like https://api005.backblazeb2.com → region us-east-005 is NOT
            # derivable from the number alone; downloadUrl/s3ApiUrl carries it.
            s3_url = storage.get("s3ApiUrl") or auth.get("s3ApiUrl", "")
            m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", s3_url)
            if allowed_name and (not bucket or (allowed_id and bucket == allowed_id)):
                bucket = allowed_name
                log.info("resolved bucket name from scoped key")
            elif bucket and _HEX_ID.fullmatch(bucket):
                name = b2_bucket_name_from_id(auth, bucket)
                if name:
                    log.info("resolved bucket name from id via b2_list_buckets")
                    bucket = name
            if m and (not region or not re.fullmatch(r"[a-z]+-[a-z]+-\d{3}", region)):
                region = m.group(1)
                log.info("resolved region from s3ApiUrl: %s", region)
        except Exception as e:  # pragma: no cover - network
            log.warning("b2_authorize_account lookup failed: %s", e)

    if bucket:
        os.environ["B2_BUCKET"] = bucket
    if region:
        os.environ["B2_REGION"] = region
    return {
        "bucket": bucket or "(unset)",
        "region": region or "(unset)",
        "key_id_set": bool(key_id),
        "app_key_set": bool(app_key),
    }
