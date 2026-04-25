"""Load/save chat transcripts: local files or S3 (same pattern as production/twin)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

log = logging.getLogger(__name__)


def _key(session_id: str) -> str:
    if not session_id or ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError("Invalid session_id")
    return f"{session_id}.json"


def load_messages(
    session_id: str,
    *,
    use_s3: bool,
    s3_bucket: str,
    memory_dir: str,
) -> list[dict[str, Any]]:
    if use_s3:
        import boto3

        s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION") or None)
        try:
            o = s3.get_object(Bucket=s3_bucket, Key=_key(session_id))
            raw = o["Body"].read().decode("utf-8")
            return json.loads(raw)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                return []
            log.exception("S3 get_object failed for %s", session_id)
            raise
    else:
        path = os.path.join(memory_dir, _key(session_id))
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return []


def save_messages(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    use_s3: bool,
    s3_bucket: str,
    memory_dir: str,
) -> None:
    body = json.dumps(messages, indent=2)
    if use_s3:
        import boto3

        s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION") or None)
        s3.put_object(
            Bucket=s3_bucket,
            Key=_key(session_id),
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    else:
        os.makedirs(memory_dir, exist_ok=True)
        path = os.path.join(memory_dir, _key(session_id))
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)


def to_llm_history(stored: list[dict[str, Any]], *, max_messages: int) -> list[dict[str, str]]:
    """Last N user/assistant turns for the model."""
    out: list[dict[str, str]] = []
    for m in stored[-max_messages:]:
        r = m.get("role")
        c = (m.get("content") or "").strip()
        if r in ("user", "assistant") and c:
            out.append({"role": r, "content": c})
    return out


def append_turn(
    stored: list[dict[str, Any]],
    user_text: str,
    assistant_text: str,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        *stored,
        {"role": "user", "content": user_text, "timestamp": now},
        {"role": "assistant", "content": assistant_text, "timestamp": now},
    ]
