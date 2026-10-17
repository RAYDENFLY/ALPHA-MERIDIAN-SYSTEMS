"""Qwen2.5-7B-Instruct Client & Controlled AI Signal Analyst.

Integrates with Qwen2.5-7B running on Google Colab via HTTPS ngrok tunnel.
Operates strictly as a read-only signal enrichment service.
Enforces deterministic client-side validation, schema compliance, timeout limits,
and automatic abstain guards on UNRELIABLE / INSUFFICIENT_DATA ML status.
"""

from datetime import datetime, timezone
from enum import Enum
import json
import re
import time
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, ValidationError
import requests

from config.settings import AIConfig, AIMode
from src.core.logger import logger
from src.core.types import SetupCandidate

PROMPT_SCHEMA_VERSION = "v1.0"


class DirectionAssessment(str, Enum):
    SUPPORTS_SETUP = "SUPPORTS_SETUP"
    CONFLICTS_WITH_SETUP = "CONFLICTS_WITH_SETUP"
    UNCLEAR = "UNCLEAR"


class SetupQuality(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AIAnalysisStatus(str, Enum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    API_ERROR = "API_ERROR"
    INVALID_JSON = "INVALID_JSON"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


class AIAnalysisOutput(BaseModel):
    """Schema for AI signal qualitative analysis."""
    summary: str = Field(description="1-2 sentence objective qualitative summary")
    direction_assessment: DirectionAssessment
    setup_quality: SetupQuality
    reasons: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    invalidation_conditions: List[str] = Field(default_factory=list)
    abstain: bool = Field(default=False)


class AIAnalysisResult(BaseModel):
    """Container for AI analysis output and infrastructure audit metadata."""
    candidate_id: str
    analysis_timestamp_utc: str
    model_id: str
    prompt_version: str = PROMPT_SCHEMA_VERSION
    latency_ms: float = 0.0
    status: AIAnalysisStatus
    correlation_id: str
    output: Optional[AIAnalysisOutput] = None
    error_message: Optional[str] = None
    deterministic_guards_applied: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "analysis_timestamp_utc": self.analysis_timestamp_utc,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "latency_ms": round(self.latency_ms, 2),
            "status": self.status.value,
            "correlation_id": self.correlation_id,
            "output": self.output.model_dump() if self.output else None,
            "error_message": self.error_message,
            "deterministic_guards_applied": self.deterministic_guards_applied,
        }


def extract_json_payload(raw_text: str) -> str:
    """Extract JSON from raw LLM output, handling Markdown code fences safely."""
    text = raw_text.strip()
    if not text:
        return ""

    # Match ```json ... ``` or ``` ... ```
    fence_pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
    match = re.search(fence_pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # If starts with ``` and ends with ```
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            inner = lines[1:-1]
            return "\n".join(inner).strip()

    # Look for the outermost { ... }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1].strip()

    return text


def build_candidate_prompt(candidate: SetupCandidate) -> str:
    """Construct a strictly bounded prompt using only verified candidate fields."""
    ml_prob_str = f"{candidate.ml_probability:.4f}" if candidate.ml_probability is not None else "N/A (Abstained)"
    risk_info = "N/A"
    if candidate.risk_assessment:
        ra = candidate.risk_assessment
        risk_info = (
            f"Feasible: {ra.is_feasible}, Isolated Margin: ${ra.target_isolated_margin_usdt:.2f} USDT, "
            f"Risk Budget: ${ra.risk_budget_usdt:.2f} USDT, Loss at SL: ${ra.estimated_loss_usdt:.2f} USDT"
        )

    smc_items = candidate.smc_evidence if candidate.smc_evidence else ["None specified"]
    snr_items = candidate.snr_evidence if candidate.snr_evidence else ["None specified"]

    return f"""Anda adalah AI Signal Analyst untuk sistem trading kuantitatif. Analisis data kandidat setup berikut secara objektif dan hasilkan analisis dalam format JSON valid.

KANDIDAT SETUP:
- Candidate ID: {candidate.candidate_id}
- Symbol: {candidate.symbol}
- Direction: {candidate.direction.value}
- 4H Bias: {candidate.bias_4h.value}
- 1H Structure: {candidate.structure_1h.value}
- Entry: {candidate.entry_price}, Stop Loss: {candidate.stop_loss}, Take Profit 1: {candidate.take_profit_1} (Net R:R: {candidate.net_reward_to_risk:.2f})
- Invalidation Price: {candidate.invalidation_price} ({candidate.invalidation_reason})
- SMC Evidence: {smc_items}
- SnR Evidence: {snr_items}
- ML Probability: {ml_prob_str} (Calibration Status: {candidate.calibration_status}, Support: {candidate.eval_sample_count} samples)
- Risk Feasibility: {risk_info}

ATURAN WAJIB:
1. Analisis HANYA fakta di atas. DILARANG mengarang harga, indikator, berita, atau data pasar di luar input.
2. Jika ML status UNRELIABLE atau INSUFFICIENT_DATA, wajib set abstain: true dan setup_quality: INSUFFICIENT_DATA.
3. Kategori HIGH hanya evaluasi tekstual kualitas SMC/SnR, BUKAN jaminan profit atau probabilitas.
4. DILARANG mengubah level entry, SL, TP, ML score, atau status setup.
5. Balas HANYA dengan JSON valid di dalam blok ```json ... ``` persis sesuai schema berikut:
```json
{{
  "summary": "ringkasan analisis 1-2 kalimat objektif",
  "direction_assessment": "SUPPORTS_SETUP | CONFLICTS_WITH_SETUP | UNCLEAR",
  "setup_quality": "LOW | MEDIUM | HIGH | INSUFFICIENT_DATA",
  "reasons": ["alasan 1", "alasan 2"],
  "risks": ["risiko 1"],
  "invalidation_conditions": ["kondisi pembatalan"],
  "abstain": false
}}
```"""


class QwenClient:
    """Client for Qwen2.5-7B-Instruct REST API with deterministic guards and error isolation."""

    def __init__(self, config: Optional[AIConfig] = None) -> None:
        self.config = config or AIConfig()
        self.session = requests.Session()

    def analyze_candidate(self, candidate: SetupCandidate) -> AIAnalysisResult:
        """Perform read-only AI qualitative evaluation on a candidate setup."""
        correlation_id = str(uuid.uuid4())[:8]
        timestamp_utc = datetime.now(timezone.utc).isoformat()

        if not self.config.enabled or self.config.mode == AIMode.NO_AI:
            return AIAnalysisResult(
                candidate_id=candidate.candidate_id,
                analysis_timestamp_utc=timestamp_utc,
                model_id=self.config.model_id,
                latency_ms=0.0,
                status=AIAnalysisStatus.DISABLED,
                correlation_id=correlation_id,
                error_message="AI analysis disabled or in NO_AI mode",
            )

        prompt = build_candidate_prompt(candidate)
        payload = {
            "prompt": prompt,
            "max_tokens": self.config.max_tokens,
        }

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
            "X-Correlation-ID": correlation_id,
        }
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"

        t0 = time.time()
        raw_response_text = ""
        last_error = ""
        status = AIAnalysisStatus.OK

        # Retry loop for safe transient HTTP/network failures
        for attempt in range(1, self.config.max_retries + 2):
            try:
                resp = self.session.post(
                    self.config.api_url,
                    json=payload,
                    headers=headers,
                    timeout=(self.config.connect_timeout_sec, self.config.read_timeout_sec),
                )
                if resp.status_code == 200:
                    try:
                        resp_json = resp.json()
                        raw_response_text = resp_json.get("response", "")
                        status = AIAnalysisStatus.OK
                        break
                    except Exception as json_err:
                        status = AIAnalysisStatus.INVALID_JSON
                        last_error = f"Malformed server response JSON: {json_err}"
                        break
                elif resp.status_code in (502, 503, 504) and attempt <= self.config.max_retries:
                    time.sleep(self.config.retry_delay_sec * attempt)
                    continue
                else:
                    status = AIAnalysisStatus.API_ERROR
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    break

            except requests.exceptions.Timeout as t_err:
                status = AIAnalysisStatus.TIMEOUT
                last_error = f"Request timed out (read/connect): {t_err}"
                break
            except requests.exceptions.ConnectionError as c_err:
                if attempt <= self.config.max_retries:
                    time.sleep(self.config.retry_delay_sec * attempt)
                    continue
                status = AIAnalysisStatus.UNAVAILABLE
                last_error = f"Connection/Tunnel unreachable: {c_err}"
                break
            except Exception as e:
                status = AIAnalysisStatus.API_ERROR
                last_error = f"Unexpected client error: {type(e).__name__}: {e}"
                break

        latency_ms = (time.time() - t0) * 1000.0

        if status != AIAnalysisStatus.OK:
            logger.warning(f"[QwenClient:{correlation_id}] AI analysis failed for {candidate.symbol}: {last_error}")
            return AIAnalysisResult(
                candidate_id=candidate.candidate_id,
                analysis_timestamp_utc=timestamp_utc,
                model_id=self.config.model_id,
                latency_ms=latency_ms,
                status=status,
                correlation_id=correlation_id,
                error_message=last_error,
            )

        # Parse extracted JSON into Pydantic schema
        extracted_json_str = extract_json_payload(raw_response_text)
        if not extracted_json_str:
            return AIAnalysisResult(
                candidate_id=candidate.candidate_id,
                analysis_timestamp_utc=timestamp_utc,
                model_id=self.config.model_id,
                latency_ms=latency_ms,
                status=AIAnalysisStatus.INVALID_JSON,
                correlation_id=correlation_id,
                error_message="Empty response or no JSON block found",
            )

        try:
            parsed_dict = json.loads(extracted_json_str)
            analysis_output = AIAnalysisOutput(**parsed_dict)
        except (json.JSONDecodeError, ValidationError, TypeError) as parse_exc:
            logger.warning(f"[QwenClient:{correlation_id}] JSON schema validation failed: {parse_exc}")
            return AIAnalysisResult(
                candidate_id=candidate.candidate_id,
                analysis_timestamp_utc=timestamp_utc,
                model_id=self.config.model_id,
                latency_ms=latency_ms,
                status=AIAnalysisStatus.INVALID_JSON,
                correlation_id=correlation_id,
                error_message=f"JSON/Schema validation failed: {parse_exc}",
            )

        # Deterministic Laptop-Side Safety Override
        guards_applied: List[str] = []
        is_ml_unreliable = candidate.calibration_status in ("UNRELIABLE", "INSUFFICIENT_DATA")

        if is_ml_unreliable:
            if not analysis_output.abstain or analysis_output.setup_quality != SetupQuality.INSUFFICIENT_DATA:
                analysis_output.abstain = True
                analysis_output.setup_quality = SetupQuality.INSUFFICIENT_DATA
                guards_applied.append(
                    f"ML calibration status is {candidate.calibration_status} -> forced abstain=True and setup_quality=INSUFFICIENT_DATA"
                )
                analysis_output.reasons.append(
                    f"DETERMINISTIC_GUARD: ML calibration status is {candidate.calibration_status}; quantitative win rate cannot be certified."
                )

        return AIAnalysisResult(
            candidate_id=candidate.candidate_id,
            analysis_timestamp_utc=timestamp_utc,
            model_id=self.config.model_id,
            latency_ms=latency_ms,
            status=AIAnalysisStatus.OK,
            correlation_id=correlation_id,
            output=analysis_output,
            deterministic_guards_applied=guards_applied,
        )
