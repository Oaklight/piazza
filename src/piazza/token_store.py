"""SQLite-backed token store for agent API authentication.

Manages agent tokens (create, validate, rotate, delete) with:
- SHA-256 hashing — plaintext never stored, shown once at creation
- Constant-time comparison — prevents timing attacks
- Supertoken support — agent_id=NULL grants wildcard access
- last_used_at tracking — updated on every validated request

Token format: ``pzt-{48 hex chars}`` (piazza token).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

_TOKEN_PREFIX = "pzt-"
_TOKEN_HEX_LEN = 48  # 24 bytes = 48 hex chars
_DISPLAY_PREFIX_LEN = 8  # chars of token shown in listings

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tokens (
    id           TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    agent_id     TEXT,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_used_at TEXT
)
"""


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    """SHA-256 hash of a token string."""
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    """Generate a new token with ``pzt-`` prefix."""
    return f"{_TOKEN_PREFIX}{secrets.token_hex(_TOKEN_HEX_LEN // 2)}"


class TokenStore:
    """SQLite-backed agent token store.

    Uses the same database file as the message bus. Creates a
    ``tokens`` table if it does not exist.

    Args:
        db_path: Path to the SQLite database file.

    Example:
        >>> store = TokenStore("/data/piazza.db")
        >>> entry = store.create_token("agent-alice", "Alice's bot")
        >>> print(entry["token"])  # shown once
        >>> result = store.validate(entry["token"])
        >>> assert result == "agent-alice"
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        """Open a new connection with WAL mode and busy timeout."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        """Create the tokens table if it does not exist."""
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def list_tokens(self) -> list[dict[str, Any]]:
        """List all tokens with metadata (no secret values).

        Returns:
            List of token entries with id, token_prefix, agent_id,
            label, created_at, and last_used_at.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, token_prefix, agent_id, label, created_at, last_used_at "
                "FROM tokens ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_token(
        self,
        agent_id: str | None = None,
        label: str = "",
    ) -> dict[str, Any]:
        """Create a new agent token.

        Args:
            agent_id: Agent ID this token authenticates as.
                None creates a supertoken (wildcard).
            label: Human-readable description.

        Returns:
            Dict with all fields including the plaintext ``token``
            (shown this once only).
        """
        token = _generate_token()
        token_hash = _hash_token(token)
        token_prefix = token[:_DISPLAY_PREFIX_LEN]
        token_id = uuid.uuid4().hex[:8]
        now = _now_iso()

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tokens (id, token_hash, token_prefix, agent_id, label, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_id, token_hash, token_prefix, agent_id, label, now),
            )

        return {
            "id": token_id,
            "token": token,
            "token_prefix": token_prefix,
            "agent_id": agent_id,
            "label": label,
            "created_at": now,
            "last_used_at": None,
        }

    def delete_token(self, token_id: str) -> bool:
        """Delete a token by ID.

        Args:
            token_id: The token's unique identifier.

        Returns:
            True if deleted, False if not found.
        """
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
            return cursor.rowcount > 0

    def rotate_token(self, token_id: str) -> dict[str, Any] | None:
        """Rotate a token: generate new value, keep same ID and metadata.

        Args:
            token_id: The token's unique identifier.

        Returns:
            Dict with updated fields including new plaintext ``token``,
            or None if token_id not found.
        """
        new_token = _generate_token()
        new_hash = _hash_token(new_token)
        new_prefix = new_token[:_DISPLAY_PREFIX_LEN]

        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tokens SET token_hash = ?, token_prefix = ? WHERE id = ?",
                (new_hash, new_prefix, token_id),
            )
            if cursor.rowcount == 0:
                return None

            row = conn.execute(
                "SELECT id, token_prefix, agent_id, label, created_at, last_used_at "
                "FROM tokens WHERE id = ?",
                (token_id,),
            ).fetchone()

        result = dict(row)
        result["token"] = new_token
        return result

    def validate(self, token_str: str) -> str | None | bool:
        """Validate a token and return the associated agent_id.

        Uses constant-time comparison to prevent timing attacks.

        Args:
            token_str: The plaintext token from the request.

        Returns:
            - ``str``: the agent_id bound to this token
            - ``None``: valid supertoken (wildcard, any agent)
            - ``False``: invalid token
        """
        if not token_str or not token_str.startswith(_TOKEN_PREFIX):
            return False

        provided_hash = _hash_token(token_str)

        with self._connect() as conn:
            # Fetch all token hashes for constant-time scan.
            # For typical deployments (< 1000 tokens) this is fine.
            rows = conn.execute("SELECT id, token_hash, agent_id FROM tokens").fetchall()

        matched_id: str | None = None
        matched_agent: str | None | bool = False

        for row in rows:
            if secrets.compare_digest(provided_hash, row["token_hash"]):
                matched_id = row["id"]
                matched_agent = row["agent_id"]  # None for supertoken
                break

        if matched_id is None:
            return False

        # Update last_used_at (best-effort, don't block on failure)
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE tokens SET last_used_at = ? WHERE id = ?",
                    (_now_iso(), matched_id),
                )
        except sqlite3.Error:
            pass

        return matched_agent

    def has_tokens(self) -> bool:
        """Check if any tokens exist in the store.

        Returns:
            True if at least one token is configured.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()
            return row[0] > 0
