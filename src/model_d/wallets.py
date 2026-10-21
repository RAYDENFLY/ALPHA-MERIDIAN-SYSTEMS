"""Free-first, provenance-preserving EVM wallet discovery primitives."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib, json, re, time
from typing import Any, Dict, Iterable, List, Optional, Tuple
import requests

CHAIN_NAMES = {1: "ethereum", 56: "bsc", 42161: "arbitrum", 8453: "base"}
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


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
                ranking_score REAL, ranking_components_json TEXT, score_version TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(chain_id, address))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_d_wallet_score ON model_d_wallet_candidates(ranking_score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_d_wallet_chain ON model_d_wallet_candidates(chain_id, address)")
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_wallet_checkpoints (
                chain_id INTEGER NOT NULL, source TEXT NOT NULL, last_block INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT, last_error TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY(chain_id, source))""")
            conn.commit()

    def upsert(self, candidate: WalletCandidate) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute("""INSERT INTO model_d_wallet_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wallet_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                discovery_sources_json=excluded.discovery_sources_json, source_event_references_json=excluded.source_event_references_json,
                native_balance=excluded.native_balance, enrichment_status=excluded.enrichment_status,
                ranking_score=excluded.ranking_score, ranking_components_json=excluded.ranking_components_json,
                updated_at=excluded.updated_at""", (candidate.wallet_id,candidate.chain_id,candidate.address,now,now,
                json.dumps(candidate.discovery_sources),candidate.discovery_method,json.dumps(candidate.source_event_references),candidate.native_balance,
                candidate.transaction_count_observed,candidate.contract_classification,candidate.label_provenance,candidate.verification_status,
                candidate.enrichment_status,candidate.ranking_score,json.dumps(candidate.ranking_components) if candidate.ranking_components else None,
                candidate.score_version,now,now))
            conn.commit()

    def count_by_chain(self) -> Dict[int, int]:
        with self.db.get_connection() as conn:
            return {int(r[0]): int(r[1]) for r in conn.execute("SELECT chain_id, COUNT(*) FROM model_d_wallet_candidates GROUP BY chain_id")}

    def checkpoint(self, chain_id: int, source: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM model_d_wallet_checkpoints WHERE chain_id=? AND source=?", (chain_id, source)).fetchone()
        return dict(row) if row else None

    def save_checkpoint(self, chain_id: int, source: str, last_block: int, error: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute("""INSERT INTO model_d_wallet_checkpoints(chain_id,source,last_block,last_success_at,last_error,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(chain_id,source) DO UPDATE SET last_block=excluded.last_block,
                last_success_at=excluded.last_success_at,last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (chain_id, source, last_block, now if error is None else None, error, now))
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
    def __init__(self, registry: WalletRegistry, providers: Dict[int, JsonRpcProvider], block_window: int = 1000):
        self.registry, self.providers, self.block_window = registry, providers, max(1, min(block_window, 5000))

    def scan_once(self, chain_id: int, source: str = "public_rpc", contract: Optional[str] = None) -> Dict[str, Any]:
        provider = self.providers[chain_id]
        checkpoint = self.registry.checkpoint(chain_id, source)
        latest = provider.latest_block(); start = (int(checkpoint["last_block"]) + 1) if checkpoint else max(0, latest - self.block_window + 1)
        if start > latest:
            return {"chain_id": chain_id, "source": source, "from_block": start, "to_block": latest, "discovered": 0, "status": "NOOP"}
        end = min(latest, start + self.block_window - 1)
        try:
            logs = provider.logs(start, end, contract=contract)
            candidates = candidates_from_transfer_logs(chain_id, logs, source)
            for candidate in candidates: self.registry.upsert(candidate)
            self.registry.save_checkpoint(chain_id, source, end)
            return {"chain_id": chain_id, "source": source, "from_block": start, "to_block": end, "discovered": len(candidates), "status": "OK"}
        except Exception:
            self.registry.save_checkpoint(chain_id, source, start - 1, error="provider request failed")
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
