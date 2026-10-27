"""Centralized Groq API Key Fallback, Health Tracking & Quota Rotation Manager.

Provides thread-safe multi-key rotation across:
- PRIMARY: GROQ_API_KEY (or groq)
- FALLBACK_A: GROQ_API_KEY_FALLBACK_A
- FALLBACK_B: GROQ_API_KEY_FALLBACK_B

Architecture & Guarantees:
1. Strict Priority Ordering: PRIMARY -> FALLBACK_A -> FALLBACK_B.
2. Sanitization: Keys are never logged, printed, or exposed in repr/metadata.
3. Health Classification:
   - Quota Exhaustion (429 with insufficient_quota/TPD/RPD): permanently marked exhausted for session.
   - Temporary Rate Limit (429 with TPM/retry_after): respects Retry-After cooldown.
   - Invalid Key (401): marked invalid, skipped without treating as quota exhaustion.
   - Provider Failure (5xx/timeouts): rotates to next available key.
   - Bad Request (400/422): does not rotate keys.
4. Concurrency Safety:
   - Uses threading.RLock for thread safety across concurrent background scans and UI previews.
   - State is process-local within the running Python process (does not synchronize across
     separate OS worker processes).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GroqKeyEntry:
    """Represents a single Groq API key entry with health and rate-limit tracking."""

    def __init__(self, alias: str, key: str) -> None:
        self.alias = alias
        self._key = key
        self.is_exhausted = False
        self.exhausted_reason = ""
        self.exhausted_at = 0.0
        self.exhausted_models: set = set()
        self.rate_limited_until = 0.0
        self.is_invalid = False
        self.invalid_reason = ""
        self.total_requests = 0
        self.total_successes = 0
        self.total_failures = 0
        self.last_used_utc: Optional[str] = None
        self.last_error: Optional[str] = None

    def is_model_exhausted(self, model: str) -> bool:
        """Check if a specific model token quota is exhausted for this key."""
        return self.is_exhausted or (model in self.exhausted_models)

    def mark_model_exhausted(self, model: str, reason: str = "") -> None:
        """Mark a specific model token quota exhausted for this key."""
        self.exhausted_models.add(model)
        self.last_error = f"MODEL_TPD_EXHAUSTED:{model}"

    @property
    def key(self) -> str:
        """Raw API key access (strictly internal to HTTP dispatchers)."""
        return self._key

    def is_available(self, now: Optional[float] = None) -> bool:
        """Return True if this key is healthy and not in cooldown."""
        if self.is_invalid or self.is_exhausted:
            return False
        current_time = now if now is not None else time.time()
        return current_time >= self.rate_limited_until

    def __repr__(self) -> str:
        return (
            f"GroqKeyEntry(alias='{self.alias}', "
            f"configured={bool(self._key)}, "
            f"exhausted={self.is_exhausted}, "
            f"rate_limited={self.rate_limited_until > time.time()}, "
            f"invalid={self.is_invalid})"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return sanitized dictionary representation safe for telemetry and logs."""
        now = time.time()
        cooldown_remaining = max(0.0, round(self.rate_limited_until - now, 1))
        return {
            "alias": self.alias,
            "configured": bool(self._key),
            "is_exhausted": self.is_exhausted,
            "exhausted_reason": self.exhausted_reason or None,
            "is_rate_limited": cooldown_remaining > 0.0,
            "cooldown_remaining_sec": cooldown_remaining,
            "is_invalid": self.is_invalid,
            "total_requests": self.total_requests,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "last_used_utc": self.last_used_utc,
            "last_error": self.last_error,
        }


class GroqKeyManager:
    """Centralized thread-safe Groq API Key and Quota Rotation Manager."""

    DEFAULT_COOLDOWN_SEC = 3.0
    MAX_COOLDOWN_SEC = 30.0

    def __init__(
        self,
        primary_key: Optional[str] = None,
        fallback_a_key: Optional[str] = None,
        fallback_b_key: Optional[str] = None,
        keys: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._keys: List[GroqKeyEntry] = []
        self._initialize_keys(
            primary_key=primary_key,
            fallback_a_key=fallback_a_key,
            fallback_b_key=fallback_b_key,
            custom_keys=keys,
        )

    def _initialize_keys(
        self,
        primary_key: Optional[str] = None,
        fallback_a_key: Optional[str] = None,
        fallback_b_key: Optional[str] = None,
        custom_keys: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        """Load, sanitize, and deduplicate keys with strict priority."""
        with self._lock:
            self._keys = []
            seen_keys = set()

            raw_candidates: List[Tuple[str, Optional[str]]] = []
            if custom_keys:
                raw_candidates.extend(custom_keys)
            else:
                p_key = primary_key if primary_key is not None else (os.getenv("GROQ_API_KEY") or os.getenv("groq"))
                fa_key = fallback_a_key if fallback_a_key is not None else os.getenv("GROQ_API_KEY_FALLBACK_A")
                fb_key = fallback_b_key if fallback_b_key is not None else os.getenv("GROQ_API_KEY_FALLBACK_B")

                raw_candidates.append(("PRIMARY", p_key))
                raw_candidates.append(("FALLBACK_A", fa_key))
                raw_candidates.append(("FALLBACK_B", fb_key))

            for alias, val in raw_candidates:
                if not val:
                    continue
                clean_val = str(val).strip()
                if not clean_val or clean_val in seen_keys:
                    continue
                seen_keys.add(clean_val)
                self._keys.append(GroqKeyEntry(alias=alias, key=clean_val))

            configured_aliases = [k.alias for k in self._keys]
            if configured_aliases:
                logger.info(f"[GROQ] Initialized GroqKeyManager with keys: {configured_aliases}")
            else:
                logger.warning("[GROQ] Initialized GroqKeyManager with ZERO configured keys.")

    def has_keys(self) -> bool:
        """Return True if at least one key is configured."""
        with self._lock:
            return len(self._keys) > 0

    def get_configured_aliases(self) -> List[str]:
        """Return list of configured key aliases in priority order."""
        with self._lock:
            return [k.alias for k in self._keys]

    def get_available_keys(self, force_include_cooling: bool = False) -> List[GroqKeyEntry]:
        """Return healthy, unexhausted keys in priority order.

        If force_include_cooling is True and all healthy keys are cooling down,
        returns healthy keys sorted by earliest cooldown expiry.
        """
        with self._lock:
            now = time.time()
            healthy_keys = [k for k in self._keys if not k.is_invalid and not k.is_exhausted]
            ready_keys = [k for k in healthy_keys if k.is_available(now)]

            if ready_keys:
                return list(ready_keys)

            if force_include_cooling and healthy_keys:
                # Return keys sorted by lowest cooldown remaining
                return sorted(healthy_keys, key=lambda k: k.rate_limited_until)

            return []

    def get_key_by_alias(self, alias: str) -> Optional[GroqKeyEntry]:
        """Find key entry by alias name."""
        with self._lock:
            for k in self._keys:
                if k.alias.upper() == alias.upper():
                    return k
            return None

    def mark_model_quota_exhausted(self, alias: str, model: str, reason: str = "") -> None:
        """Mark a specific model as having exhausted its quota for this key."""
        with self._lock:
            k = self.get_key_by_alias(alias)
            if k:
                k.mark_model_exhausted(model, reason)
                k.total_failures += 1
                logger.warning(f"[GROQ] {alias} model '{model}' quota exhausted ({reason or 'TPD limit'})")

    def mark_quota_exhausted(self, alias: str, reason: str = "") -> None:
        """Mark a key as having exhausted its daily or account quota."""
        with self._lock:
            k = self.get_key_by_alias(alias)
            if k and not k.is_exhausted:
                k.is_exhausted = True
                k.exhausted_reason = reason or "quota_exhausted"
                k.exhausted_at = time.time()
                k.total_failures += 1
                logger.warning(f"[GROQ] {alias} key failed: quota exhausted ({reason or 'TPD/RPD limit'})")

    def mark_rate_limited(self, alias: str, retry_after_sec: float = 3.0, reason: str = "") -> None:
        """Mark a key as temporarily rate-limited with a backoff duration."""
        with self._lock:
            k = self.get_key_by_alias(alias)
            if k:
                bounded_delay = max(0.5, min(self.MAX_COOLDOWN_SEC, float(retry_after_sec)))
                k.rate_limited_until = time.time() + bounded_delay
                k.total_failures += 1
                k.last_error = f"RATE_LIMITED_{bounded_delay:.1f}s"
                logger.info(f"[GROQ] {alias} failed: rate limited ({bounded_delay:.1f}s delay) {reason}".strip())

    def mark_invalid(self, alias: str, reason: str = "") -> None:
        """Mark a key as invalid (e.g. HTTP 401). Never treat as quota exhaustion."""
        with self._lock:
            k = self.get_key_by_alias(alias)
            if k and not k.is_invalid:
                k.is_invalid = True
                k.invalid_reason = reason or "HTTP_401_UNAUTHORIZED"
                k.total_failures += 1
                k.last_error = "INVALID_KEY_HTTP_401"
                logger.error(f"[GROQ] Key {alias} is invalid (HTTP 401). Skipping invalid key.")

    def mark_temporary_failure(self, alias: str, error_type: str = "PROVIDER_ERROR") -> None:
        """Record temporary 5xx or network failure without permanently disabling the key."""
        with self._lock:
            k = self.get_key_by_alias(alias)
            if k:
                # Brief transient cooldown (1.5s) to allow provider recovery
                k.rate_limited_until = time.time() + 1.5
                k.total_failures += 1
                k.last_error = error_type
                logger.warning(f"[GROQ] {alias} temporary provider failure: {error_type}")

    def mark_success(self, alias: str) -> None:
        """Record successful completion using key."""
        with self._lock:
            k = self.get_key_by_alias(alias)
            if k:
                k.total_requests += 1
                k.total_successes += 1
                k.last_used_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                k.last_error = None
                # Clear transient rate-limit flag upon successful call
                if k.rate_limited_until <= time.time():
                    k.rate_limited_until = 0.0

    def reset_exhaustion(self) -> None:
        """Reset quota exhaustion states (e.g. on daily rollover or testing)."""
        with self._lock:
            for k in self._keys:
                k.is_exhausted = False
                k.exhausted_models.clear()
                k.exhausted_reason = ""
                k.rate_limited_until = 0.0
            logger.info("[GROQ] Reset quota exhaustion state across all keys.")

    @staticmethod
    def friendly_name(alias: str) -> str:
        """Return human-readable key alias description for logs and notifications."""
        alias_upper = (alias or "").upper()
        if alias_upper == "PRIMARY":
            return "Primary key"
        if alias_upper.startswith("FALLBACK_"):
            suffix = alias_upper.split("_", 1)[1]
            return f"Fallback {suffix}"
        return alias

    @staticmethod
    def classify_groq_error(
        status_code: int,
        response_text: str = "",
        headers: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, float]:
        """Classify Groq HTTP response and determine error category & retry delay.

        Returns:
            Tuple[category, retry_after_seconds]:
            - category: 'QUOTA_EXHAUSTED', 'RATE_LIMITED', 'INVALID_KEY',
                        'BAD_REQUEST', 'PROVIDER_TEMPORARY', 'UNKNOWN'
            - retry_after_seconds: parsed from headers or body regex, bounded.
        """
        resp_lower = (response_text or "").lower()

        # Parse retry_after
        retry_after = 3.0
        if headers:
            header_val = headers.get("retry-after") or headers.get("Retry-After")
            if header_val is not None:
                try:
                    retry_after = float(header_val)
                except (ValueError, TypeError):
                    pass

        # Also search body for "try again in X.Xs"
        match = re.search(r"try again in ([\d\.]+)s", response_text or "")
        if match:
            try:
                retry_after = max(retry_after, float(match.group(1)) + 0.5)
            except (ValueError, TypeError):
                pass

        retry_after = max(0.5, min(30.0, retry_after))

        if status_code == 401:
            return "INVALID_KEY", 0.0

        if status_code in (413, 429):
            # Check for permanent daily/monthly quota exhaustion signals
            quota_exhaustion_patterns = [
                "insufficient_quota",
                "quota_exceeded",
                "tokens_per_day",
                "requests_per_day",
                "daily limit",
                "monthly limit",
                "tpd",
                "rpd",
                "exceeded your current quota",
            ]
            if any(pat in resp_lower for pat in quota_exhaustion_patterns):
                return "QUOTA_EXHAUSTED", retry_after

            # Check for input tokens per minute (ITPM) or TPM rate limiting (often returned as 413 by Groq)
            return "RATE_LIMITED", retry_after

        if status_code in (500, 502, 503, 504, 520, 521, 522, 524):
            return "PROVIDER_TEMPORARY", 1.5

        if status_code in (400, 404, 422):
            return "BAD_REQUEST", 0.0

        return "UNKNOWN", 0.0

    def get_status(self) -> Dict[str, Any]:
        """Return complete sanitized status for dashboard telemetry."""
        with self._lock:
            return {
                "total_configured_keys": len(self._keys),
                "healthy_keys_count": len([k for k in self._keys if not k.is_invalid and not k.is_exhausted]),
                "active_cooling_keys_count": len([k for k in self._keys if k.rate_limited_until > time.time()]),
                "exhausted_keys_count": len([k for k in self._keys if k.is_exhausted]),
                "invalid_keys_count": len([k for k in self._keys if k.is_invalid]),
                "keys": [k.to_dict() for k in self._keys],
            }
