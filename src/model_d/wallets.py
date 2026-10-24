"""Free-first, provenance-preserving EVM wallet discovery primitives."""
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib, json, re, time, threading
from typing import Any, Dict, Iterable, List, Optional, Tuple
import requests

CHAIN_NAMES = {1: "ethereum", 56: "bsc", 42161: "arbitrum", 8453: "base"}
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def safe_provider_error(exc: Exception) -> str:
    """Return a non-sensitive provider failure category for API/state exposure."""
    text = str(exc).lower()
    if "chain id mismatch" in text:
        return "CHAIN_ID_MISMATCH"
    if "timeout" in text:
        return "TIMEOUT"
    if "connection" in text or "network" in text or "name or service" in text:
        return "UNAVAILABLE"
    return "ERROR"


def normalize_address(address: str) -> str:
    value = address.strip()
    if not ADDRESS_RE.fullmatch(value):
        raise ValueError("invalid EVM address")
    return "0x" + value[2:].lower()


@dataclass
class WalletCandidate:
    wallet_id: str
    chain_id: int
    address: str
    discovery_sources: List[str]
    discovery_method: str
    source_event_references: List[str]
    native_balance: Optional[str] = None
    transaction_count_observed: Optional[int] = None
    contract_classification: str = "UNKNOWN"
    label_provenance: Optional[str] = None
    verification_status: str = "UNVERIFIED"
    enrichment_status: str = "UNENRICHED"
    enrichment_error: Optional[str] = None
    ranking_score: Optional[float] = None
    ranking_components: Optional[Dict[str, float]] = None
    score_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


def wallet_id(chain_id: int, address: str) -> str:
    return hashlib.sha256(f"{chain_id}:{normalize_address(address)}".encode()).hexdigest()


class JsonRpcProvider:
    def __init__(self, chain_id: int, url: str, timeout: float = 10.0, session: Optional[requests.Session] = None):
        if chain_id not in CHAIN_NAMES: raise ValueError("unsupported production chain")
        self.chain_id, self.url, self.timeout = chain_id, url, timeout
        self.session = session or requests.Session()

    def call(self, method: str, params: List[Any]) -> Any:
        response = self.session.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("error"): raise RuntimeError("RPC error returned by provider")
        return body.get("result")

    def verify_chain(self) -> bool:
        return int(self.call("eth_chainId", []), 16) == self.chain_id

    def latest_block(self) -> int: return int(self.call("eth_blockNumber", []), 16)

    def block_hash(self, block_number: int) -> Optional[str]:
        block = self.call("eth_getBlockByNumber", [hex(block_number), False])
        return (block or {}).get("hash")

    def native_balance(self, address: str) -> Optional[str]:
        return self.call("eth_getBalance", [normalize_address(address), "latest"])

    def logs(self, from_block: int, to_block: int, contract: Optional[str] = None) -> List[Dict[str, Any]]:
        if to_block < from_block or to_block - from_block > 5000: raise ValueError("bounded block range required")
        query: Dict[str, Any] = {"fromBlock": hex(from_block), "toBlock": hex(to_block), "topics": [TRANSFER_TOPIC]}
        if contract: query["address"] = normalize_address(contract)
        return self.call("eth_getLogs", [query]) or []


class WalletRegistry:
    """Isolated persistence using the repository's existing DatabaseManager connection."""
    def __init__(self, db: Any):
        self.db = db
        with self.db.get_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_wallet_candidates (
                wallet_id TEXT PRIMARY KEY, chain_id INTEGER NOT NULL, address TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, discovery_sources_json TEXT NOT NULL,
                discovery_method TEXT NOT NULL, source_event_references_json TEXT NOT NULL,
                native_balance TEXT, transaction_count_observed INTEGER, contract_classification TEXT NOT NULL,
                label_provenance TEXT, verification_status TEXT NOT NULL, enrichment_status TEXT NOT NULL,
                enrichment_error TEXT,
                ranking_score REAL, ranking_components_json TEXT, score_version TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(chain_id, address))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_d_wallet_score ON model_d_wallet_candidates(ranking_score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_d_wallet_chain ON model_d_wallet_candidates(chain_id, address)")
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_wallet_checkpoints (
                chain_id INTEGER NOT NULL, source TEXT NOT NULL, last_block INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT, last_error TEXT, updated_at TEXT NOT NULL,
                last_block_hash TEXT, confirmation_depth INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(chain_id, source))""")
            conn.commit()
            columns = {row[1] for row in conn.execute("PRAGMA table_info(model_d_wallet_candidates)")}
            if "enrichment_error" not in columns:
                conn.execute("ALTER TABLE model_d_wallet_candidates ADD COLUMN enrichment_error TEXT")
                conn.commit()
            checkpoint_columns = {row[1] for row in conn.execute("PRAGMA table_info(model_d_wallet_checkpoints)")}
            if "last_block_hash" not in checkpoint_columns:
                conn.execute("ALTER TABLE model_d_wallet_checkpoints ADD COLUMN last_block_hash TEXT")
            if "confirmation_depth" not in checkpoint_columns:
                conn.execute("ALTER TABLE model_d_wallet_checkpoints ADD COLUMN confirmation_depth INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def upsert(self, candidate: WalletCandidate) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute("""INSERT INTO model_d_wallet_candidates
                (wallet_id,chain_id,address,first_seen_at,last_seen_at,discovery_sources_json,discovery_method,
                 source_event_references_json,native_balance,transaction_count_observed,contract_classification,
                 label_provenance,verification_status,enrichment_status,enrichment_error,ranking_score,
                 ranking_components_json,score_version,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wallet_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                discovery_sources_json=excluded.discovery_sources_json, source_event_references_json=excluded.source_event_references_json,
                native_balance=excluded.native_balance, transaction_count_observed=excluded.transaction_count_observed,
                verification_status=excluded.verification_status, enrichment_status=excluded.enrichment_status,
                enrichment_error=excluded.enrichment_error,
                ranking_score=excluded.ranking_score, ranking_components_json=excluded.ranking_components_json,
                updated_at=excluded.updated_at""", (candidate.wallet_id,candidate.chain_id,candidate.address,now,now,
                json.dumps(candidate.discovery_sources),candidate.discovery_method,json.dumps(candidate.source_event_references),candidate.native_balance,
                candidate.transaction_count_observed,candidate.contract_classification,candidate.label_provenance,candidate.verification_status,
                candidate.enrichment_status,getattr(candidate, "enrichment_error", None),candidate.ranking_score,json.dumps(candidate.ranking_components) if candidate.ranking_components else None,
                candidate.score_version,now,now))
            conn.commit()

    def count_by_chain(self) -> Dict[int, int]:
        with self.db.get_connection() as conn:
            return {int(r[0]): int(r[1]) for r in conn.execute("SELECT chain_id, COUNT(*) FROM model_d_wallet_candidates GROUP BY chain_id")}

    def checkpoint(self, chain_id: int, source: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM model_d_wallet_checkpoints WHERE chain_id=? AND source=?", (chain_id, source)).fetchone()
        return dict(row) if row else None

    def save_checkpoint(self, chain_id: int, source: str, last_block: int, error: Optional[str] = None,
                        block_hash: Optional[str] = None, confirmation_depth: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute("""INSERT INTO model_d_wallet_checkpoints
                (chain_id,source,last_block,last_success_at,last_error,updated_at,last_block_hash,confirmation_depth)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chain_id,source) DO UPDATE SET last_block=excluded.last_block,
                last_success_at=excluded.last_success_at,last_error=excluded.last_error,updated_at=excluded.updated_at,
                last_block_hash=excluded.last_block_hash,confirmation_depth=excluded.confirmation_depth""",
                (chain_id, source, last_block, now if error is None else None, error, now, block_hash, max(0, int(confirmation_depth))))
            conn.commit()

    def list_candidates(self, limit: int = 50, offset: int = 0, chain_id: Optional[int] = None, address: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 200)); offset = max(0, offset)
        query = "SELECT * FROM model_d_wallet_candidates"; params: List[Any] = []; clauses = []
        if chain_id in CHAIN_NAMES: clauses.append("chain_id=?"); params.append(chain_id)
        if address:
            clauses.append("address=?"); params.append(normalize_address(address))
        if clauses: query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ranking_score DESC NULLS LAST, updated_at DESC LIMIT ? OFFSET ?"; params.extend([limit, offset])
        with self.db.get_connection() as conn: return [dict(row) for row in conn.execute(query, params).fetchall()]


def candidates_from_transfer_logs(chain_id: int, logs: Iterable[Dict[str, Any]], source: str) -> List[WalletCandidate]:
    """Extract indexed from/to addresses from ERC-20 Transfer logs only."""
    result: List[WalletCandidate] = []
    for log in logs:
        topics = log.get("topics") or []
        if len(topics) < 3 or str(topics[0]).lower() != TRANSFER_TOPIC: continue
        refs = [str(log.get("transactionHash", "")) + ":" + str(log.get("logIndex", ""))]
        for topic in topics[1:3]:
            raw = str(topic)
            if len(raw) != 66: continue
            address = "0x" + raw[-40:]
            try: address = normalize_address(address)
            except ValueError: continue
            if address == "0x" + "0" * 40: continue
            result.append(WalletCandidate(wallet_id(chain_id, address), chain_id, address, [source], "transfer_counterparty", refs))
    return merge_candidates(result)


class WalletDiscoveryWorker:
    """One bounded, resumable discovery step; caller controls scheduling."""
    def __init__(self, registry: WalletRegistry, providers: Dict[int, JsonRpcProvider], block_window: int = 1000,
                 confirmation_depth: int = 0):
        self.registry, self.providers = registry, providers
        self.block_window = max(1, min(block_window, 5000))
        self.confirmation_depth = max(0, int(confirmation_depth))

    def scan_once(self, chain_id: int, source: str = "public_rpc", contract: Optional[str] = None) -> Dict[str, Any]:
        provider = self.providers[chain_id]
        checkpoint = self.registry.checkpoint(chain_id, source)
        latest_head = provider.latest_block()
        latest = max(0, latest_head - self.confirmation_depth)
        if checkpoint and checkpoint.get("last_block_hash") and hasattr(provider, "block_hash"):
            canonical_hash = provider.block_hash(int(checkpoint["last_block"]))
            if canonical_hash and canonical_hash != checkpoint["last_block_hash"]:
                rollback = max(0, int(checkpoint["last_block"]) - max(1, self.confirmation_depth))
                rollback_hash = provider.block_hash(rollback) if hasattr(provider, "block_hash") else None
                self.registry.save_checkpoint(chain_id, source, rollback, block_hash=rollback_hash,
                                              confirmation_depth=self.confirmation_depth)
                checkpoint = self.registry.checkpoint(chain_id, source)
        start = (int(checkpoint["last_block"]) + 1) if checkpoint else max(0, latest - self.block_window + 1)
        if start > latest:
            return {"chain_id": chain_id, "source": source, "from_block": start, "to_block": latest, "discovered": 0, "status": "NOOP"}
        end = min(latest, start + self.block_window - 1)
        try:
            logs = provider.logs(start, end, contract=contract)
            candidates = candidates_from_transfer_logs(chain_id, logs, source)
            for candidate in candidates: self.registry.upsert(candidate)
            end_hash = provider.block_hash(end) if hasattr(provider, "block_hash") else None
            self.registry.save_checkpoint(chain_id, source, end, block_hash=end_hash,
                                          confirmation_depth=self.confirmation_depth)
            return {"chain_id": chain_id, "source": source, "from_block": start, "to_block": end, "discovered": len(candidates), "status": "OK"}
        except Exception as exc:
            if checkpoint:
                self.registry.save_checkpoint(chain_id, source, int(checkpoint["last_block"]),
                                              error=safe_provider_error(exc),
                                              block_hash=checkpoint.get("last_block_hash"),
                                              confirmation_depth=self.confirmation_depth)
            return {"chain_id": chain_id, "source": source, "from_block": start, "to_block": end, "discovered": 0, "status": "ERROR"}


def merge_candidates(records: Iterable[WalletCandidate]) -> List[WalletCandidate]:
    merged: Dict[Tuple[int, str], WalletCandidate] = {}
    for item in records:
        key = (item.chain_id, normalize_address(item.address))
        if key not in merged: merged[key] = item
        else:
            current = merged[key]
            current.discovery_sources = sorted(set(current.discovery_sources + item.discovery_sources))
            current.source_event_references = sorted(set(current.source_event_references + item.source_event_references))
    return list(merged.values())


def rank_candidate(candidate: WalletCandidate, now: Optional[float] = None) -> WalletCandidate:
    if candidate.enrichment_status != "ENRICHED": return candidate
    components = {"activity_score": min(1.0, (candidate.transaction_count_observed or 0) / 100.0), "data_quality_score": 1.0}
    candidate.ranking_components = components
    candidate.ranking_score = round(sum(components.values()) / len(components), 6)
    candidate.score_version = "wallet-heuristic-v1"
    return candidate


class WalletPipelineWorker:
    """Lifecycle-managed, paper-only discovery/enrichment/ranking worker."""
    def __init__(self, registry: WalletRegistry, providers: Dict[int, JsonRpcProvider], interval_seconds: int = 3600,
                 enrichment_enabled: bool = True, ranking_enabled: bool = True, max_workers: int = 2,
                 confirmation_depths: Optional[Dict[int, int]] = None):
        self.registry, self.providers = registry, providers
        self.interval_seconds = max(60, int(interval_seconds)); self.max_workers = max(1, min(int(max_workers), 8))
        self.enrichment_enabled, self.ranking_enabled = enrichment_enabled, ranking_enabled
        self.confirmation_depths = {int(k): max(0, int(v)) for k, v in (confirmation_depths or {}).items()}
        self._stop = threading.Event(); self._lock = threading.Lock(); self._thread = None
        self.state: Dict[str, Any] = {"health": "NOT_STARTED", "last_scan_start_utc": None, "last_scan_complete_utc": None,
            "next_scan_utc": None, "last_success_utc": None, "current_cycle_status": "NEVER_RUN", "last_error": None,
            "interval_seconds": self.interval_seconds, "chains": {}, "discovered": 0, "enriched": 0, "ranked": 0, "rejected": 0}

    def _set_chain(self, chain_id: int, **values: Any) -> None:
        self.state["chains"].setdefault(str(chain_id), {}).update(values)

    def validate_providers(self) -> Dict[int, Dict[str, Any]]:
        result = {}
        for chain_id, provider in self.providers.items():
            try:
                if not provider.verify_chain(): raise ValueError("RPC chain ID mismatch")
                latest = provider.latest_block()
                result[chain_id] = {"status": "AVAILABLE", "latest_block": latest}
                self._set_chain(chain_id, status="AVAILABLE", latest_block=latest, last_error=None)
            except Exception as exc:
                error = safe_provider_error(exc)
                result[chain_id] = {"status": error, "last_error": error}
                self._set_chain(chain_id, status=error, last_error=error)
        return result

    def start(self) -> bool:
        if self._thread and self._thread.is_alive(): return False
        self._stop.clear(); self._thread = threading.Thread(target=self._loop, name="model-d-wallet-pipeline", daemon=True); self._thread.start(); return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=5)
        if self.state["health"] == "RUNNING": self.state["health"] = "STOPPED"

    def _loop(self) -> None:
        self.state["health"] = "IDLE"
        self.run_once()
        while not self._stop.wait(self.interval_seconds):
            self.run_once()

    def _enrich_and_rank(self, chain_id: int) -> tuple[int, int, int]:
        rows = self.registry.list_candidates(limit=200, offset=0, chain_id=chain_id)
        enriched = ranked = rejected = 0
        provider = self.providers[chain_id]
        for row in rows:
            candidate = WalletCandidate(wallet_id=row["wallet_id"], chain_id=row["chain_id"], address=row["address"],
                discovery_sources=json.loads(row["discovery_sources_json"]), discovery_method=row["discovery_method"],
                source_event_references=json.loads(row["source_event_references_json"]), native_balance=row["native_balance"],
                transaction_count_observed=row["transaction_count_observed"], contract_classification=row["contract_classification"],
                label_provenance=row["label_provenance"], verification_status=row["verification_status"],
                enrichment_status=row["enrichment_status"], enrichment_error=row.get("enrichment_error") if hasattr(row, "get") else None,
                ranking_score=row["ranking_score"], score_version=row["score_version"])
            if self.enrichment_enabled and candidate.enrichment_status != "ENRICHED":
                try:
                    candidate.native_balance = provider.native_balance(candidate.address)
                    candidate.transaction_count_observed = max(candidate.transaction_count_observed or 0, len(candidate.source_event_references))
                    candidate.verification_status = "RPC_VERIFIED"; candidate.enrichment_status = "ENRICHED"; candidate.enrichment_error = None; enriched += 1
                except Exception as exc:
                    candidate.enrichment_status = "ERROR"; candidate.enrichment_error = str(exc); rejected += 1
            if self.ranking_enabled and candidate.enrichment_status == "ENRICHED":
                rank_candidate(candidate); ranked += 1
            self.registry.upsert(candidate)
        return enriched, ranked, rejected

    def run_once(self) -> Dict[str, Any]:
        if not self._lock.acquire(blocking=False): return {"status": "OVERLAPPING_SCAN_REJECTED", **self.state}
        started = datetime.now(timezone.utc).isoformat(); self.state.update({"health": "RUNNING", "current_cycle_status": "RUNNING", "last_scan_start_utc": started, "last_error": None})
        discovered = enriched = ranked = rejected = 0
        try:
            if not self.providers:
                self.state.update({"health": "UNAVAILABLE", "current_cycle_status": "UNAVAILABLE", "last_error": "no configured RPC providers"})
                return {"status": "UNAVAILABLE", **self.state}
            validation = self.validate_providers()
            if not any(item.get("status") == "AVAILABLE" for item in validation.values()):
                self.state.update({"health": "ERROR", "current_cycle_status": "FAILED", "last_error": "all configured RPC providers failed validation"})
                return {"status": "ERROR", **self.state}
            for chain_id, provider in self.providers.items():
                if validation.get(chain_id, {}).get("status") != "AVAILABLE": continue
                try:
                    result = WalletDiscoveryWorker(
                        self.registry, {chain_id: provider},
                        confirmation_depth=self.confirmation_depths.get(chain_id, 0),
                    ).scan_once(chain_id)
                    discovered += int(result.get("discovered", 0)); self._set_chain(chain_id, last_scan_status=result.get("status"), last_processed_block=result.get("to_block"), discovered=result.get("discovered", 0), last_error=None if result.get("status") != "ERROR" else "provider request failed")
                    e, r, x = self._enrich_and_rank(chain_id); enriched += e; ranked += r; rejected += x
                except Exception as exc:
                    rejected += 1
                    error = safe_provider_error(exc)
                    self._set_chain(chain_id, status=error, last_scan_status="ERROR", last_error=error)
            complete = datetime.now(timezone.utc).isoformat(); self.state.update({"health": "IDLE", "current_cycle_status": "COMPLETED", "last_scan_complete_utc": complete, "last_success_utc": complete, "next_scan_utc": (datetime.now(timezone.utc) + timedelta(seconds=self.interval_seconds)).isoformat(), "discovered": discovered, "enriched": enriched, "ranked": ranked, "rejected": rejected})
            return {"status": "OK", **self.state}
        except Exception as exc:
            self.state.update({"health": "ERROR", "current_cycle_status": "FAILED", "last_scan_complete_utc": datetime.now(timezone.utc).isoformat(), "last_error": str(exc)})
            return {"status": "ERROR", **self.state}
        finally: self._lock.release()
