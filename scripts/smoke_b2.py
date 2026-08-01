"""Real B2 round trip — put, head, get, re-hash, presign, delete.

Proves the storage plane against the live bucket. Never prints secrets.
Run:  .venv/bin/python scripts/smoke_b2.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from bside.b2env import load_dotenv, normalize_b2_env  # noqa: E402

load_dotenv()
summary = normalize_b2_env()
print("b2 env:", summary)

from genblaze_s3 import S3StorageBackend  # noqa: E402

bucket = os.environ.get("B2_BUCKET", "")
region = os.environ.get("B2_REGION", "")
print(f"bucket_len={len(bucket)} region={region!r}")

backend = S3StorageBackend.for_backblaze(bucket)

key = f"scratch/smoke/{int(time.time())}.txt"
payload = f"b-side round trip @ {time.time()}".encode()
expected = hashlib.sha256(payload).hexdigest()

backend.put(key, payload, content_type="text/plain")
print("PUT ok:", key)

meta = backend.head(key)
print("HEAD ok:", meta.size if meta else None, "bytes")

fetched = backend.get(key)
fetched_hash = hashlib.sha256(fetched).hexdigest()
print("GET ok — fetched-byte hash match:", fetched_hash == expected)

url = backend.presigned_get(key, expires_in=120)
print("PRESIGNED ok (redacted repr):", str(url)[:90], "...")

import urllib.request  # noqa: E402

with urllib.request.urlopen(url.url, timeout=30) as resp:  # noqa: S310
    via_http = resp.read()
print("HTTP fetch via presigned ok:", hashlib.sha256(via_http).hexdigest() == expected)

listing = backend.list(prefix="scratch/smoke/").entries
print("LIST ok:", len(listing), "object(s) under scratch/smoke/")

backend.delete(key)
print("DELETE ok — B2 round trip complete: put/head/get/presign/http/list/delete all real")
