"""The review store: human approvals of theorem statements, committed to the repo.

Keyed by function hash + property ID + statement hash, so changing the function (or anything it
calls) or rewording the statement lapses the approval automatically. This is what makes `proven`
reproducible in CI without a human in the loop.

Approvals are only ever recorded through `apex review --approve`, which demands an interactive
terminal. No MCP tool and no agent-facing verb can write here.
"""

import getpass
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from apex.core.hashing import review_key
from apex.core.report import Review

SCHEMA_VERSION = "1"


class Approval(BaseModel):
    qualname: str
    property_id: str
    statement: str
    function_sha256: str
    statement_sha256: str
    reviewer: str
    approved_at: datetime


class _StoreFile(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    approvals: dict[str, Approval] = Field(default_factory=dict)


def current_reviewer() -> str:
    try:
        out = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return getpass.getuser()


class ReviewStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = (
            _StoreFile.model_validate_json(path.read_text()) if path.is_file() else _StoreFile()
        )

    def status(
        self, qualname: str, function_sha256: str, property_id: str, statement_sha256: str
    ) -> Review:
        key = review_key(function_sha256, property_id, statement_sha256)
        if approval := self._data.approvals.get(key):
            return Review(
                status="approved", reviewer=approval.reviewer, approved_at=approval.approved_at
            )
        lapsed = any(
            a.qualname == qualname and a.property_id == property_id
            for a in self._data.approvals.values()
        )
        return Review(status="lapsed" if lapsed else "pending")

    def approve(
        self,
        *,
        qualname: str,
        property_id: str,
        statement: str,
        function_sha256: str,
        statement_sha256: str,
        reviewer: str,
    ) -> Approval:
        approval = Approval(
            qualname=qualname,
            property_id=property_id,
            statement=statement,
            function_sha256=function_sha256,
            statement_sha256=statement_sha256,
            reviewer=reviewer,
            approved_at=datetime.now(UTC),
        )
        # Superseded approvals for the same property are dropped; the key change already lapsed
        # them, and keeping the file small keeps review diffs readable.
        self._data.approvals = {
            k: a
            for k, a in self._data.approvals.items()
            if not (a.qualname == qualname and a.property_id == property_id)
        }
        key = review_key(function_sha256, property_id, statement_sha256)
        self._data.approvals[key] = approval
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        return approval
