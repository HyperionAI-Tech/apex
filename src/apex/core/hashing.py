"""Content hashes used for function identity, statement identity and review-store keys."""

import hashlib


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_lean_statement(statement: str) -> str:
    """Collapse whitespace so reformatting a statement does not lapse its approval.

    Any change to tokens does lapse it, which is the point: a reworded statement needs new review.
    """
    return " ".join(statement.split())


def statement_hash(statement: str) -> str:
    return sha256_text(normalize_lean_statement(statement))


def review_key(function_sha256: str, property_id: str, statement_sha256: str) -> str:
    """Key into `.apex/reviews.json`. Changing the function or the statement lapses approval."""
    return f"{function_sha256}:{property_id}:{statement_sha256}"
