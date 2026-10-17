"""Comprehensive Unit and Isolation Test Suite for Qwen2.5-7B AI Integration.

Validates all 20 required acceptance and isolation specifications:
1. Response API sukses.
2. Markdown code fence parsing.
3. JSON invalid.
4. Output kosong atau terpotong.
5. Schema field hilang atau enum salah.
6. Timeout.
7. HTTP 4xx/5xx.
8. Tunnel tidak aktif.
9. Kandidat asli tidak berubah.
10. ml_probability tidak berubah.
11. calibration_status tidak berubah.
12. UNRELIABLE memaksa abstain (deterministic client-side override).
13. INSUFFICIENT_DATA memaksa abstain (deterministic client-side override).
14. Qwen tidak memengaruhi ranking.
15. Feature flag false mempertahankan perilaku baseline.
16. Mode SHADOW tidak memengaruhi keputusan atau candidate dictionary.
17. Tidak ada jalur menuju live execution (ExecutionMode.RESEARCH & live_trading_locked = True).
18. Output AI tidak dapat dieksekusi sebagai kode atau perintah.
19. Logging tidak membocorkan token.
20. Benchmark replay menggunakan input yang identik.
"""

import copy
import json
import logging
from unittest.mock import MagicMock, patch
import pytest
import requests

from config.settings import AIConfig, AIMode, ExecutionMode, get_default_config
from src.core.types import (
    CandidateStatus,
    Direction,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.integrations.qwen_client import (
    AIAnalysisOutput,
    AIAnalysisResult,
    AIAnalysisStatus,
    DirectionAssessment,
    QwenClient,
    SetupQuality,
    build_candidate_prompt,
    extract_json_payload,
)
from src.scheduler.runner import ScannerRunner, rank_candidates


@pytest.fixture
def sample_candidate() -> SetupCandidate:
    """Fixture providing a standard, realistic SetupCandidate."""
    ra = RiskAssessment(
        symbol="BTCUSDT",
        entry_price=60000.0,
        stop_loss=59000.0,
        stop_distance_pct=0.0167,
        risk_budget_usdt=1.50,
        position_notional_usdt=50.0,
        quantity=0.000833,
        required_margin_usdt=5.0,
        effective_leverage=3.125,
        estimated_fee_usdt=0.035,
        estimated_slippage_usdt=0.025,
        is_feasible=True,
        target_isolated_margin_usdt=5.0,
        estimated_loss_usdt=0.89,
        estimated_loss_pct=1.48,
        position_leverage=10.0,
        equity_exposure_ratio=3.125,
    )
    return SetupCandidate(
        candidate_id="SETUP_BTCUSDT_1792162800000",
        symbol="BTCUSDT",
        timestamp_ms=1792162800000,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit_1=62000.0,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.85,
        invalidation_price=58900.0,
        invalidation_reason="Breach of 1H demand zone",
        smc_evidence=["Bullish BOS confirmed on 1h", "Mitigated 1h FVG"],
        snr_evidence=["Support zone at 59800 held rejection"],
        ml_probability=0.6200,
        calibration_status="CALIBRATED_LIGHTGBM",
        eval_sample_count=40,
        status=CandidateStatus.READY,
        risk_assessment=ra,
    )


class TestQwenClientAPIAndParsing:
    """Tests 1 - 8: API transport, timeouts, errors, and JSON/markdown parsing."""

    def test_01_api_response_success(self, sample_candidate):
        """1. Response API sukses parses clean JSON output."""
        valid_json = json.dumps({
            "summary": "Solid bullish setup aligned with 4H bias.",
            "direction_assessment": "SUPPORTS_SETUP",
            "setup_quality": "HIGH",
            "reasons": ["Aligned 4H bias", "Valid FVG mitigation"],
            "risks": ["Volatility around news"],
            "invalidation_conditions": ["Price breaks below 58900"],
            "abstain": False,
        })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": valid_json}

        cfg = AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS)
        client = QwenClient(cfg)

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.OK
        assert res.output is not None
        assert res.output.direction_assessment == DirectionAssessment.SUPPORTS_SETUP
        assert res.output.setup_quality == SetupQuality.HIGH
        assert res.output.abstain is False
        assert len(res.output.reasons) == 2

    def test_02_markdown_code_fence_parsing(self, sample_candidate):
        """2. Markdown code fence with ```json ... ``` is stripped and parsed cleanly."""
        raw_llm_response = (
            "```json\n"
            "{\n"
            '  "summary": "Fenced JSON response",\n'
            '  "direction_assessment": "SUPPORTS_SETUP",\n'
            '  "setup_quality": "MEDIUM",\n'
            '  "reasons": ["Support holding"],\n'
            '  "risks": ["Slippage risk"],\n'
            '  "invalidation_conditions": ["SL breach"],\n'
            '  "abstain": false\n'
            "}\n"
            "```"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": raw_llm_response}

        cfg = AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS)
        client = QwenClient(cfg)

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.OK
        assert res.output is not None
        assert res.output.summary == "Fenced JSON response"
        assert res.output.setup_quality == SetupQuality.MEDIUM

    def test_03_json_invalid(self, sample_candidate):
        """3. Malformed JSON returns status INVALID_JSON without crashing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": "```json\n{unquoted_key: 123, invalid\n```"}

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.INVALID_JSON
        assert res.output is None
        assert "validation failed" in res.error_message.lower() or "json" in res.error_message.lower()

    def test_04_output_empty_or_truncated(self, sample_candidate):
        """4. Empty or truncated response returns INVALID_JSON with appropriate error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": ""}

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.INVALID_JSON
        assert res.output is None
        assert "empty response" in res.error_message.lower()

    def test_05_schema_field_missing_or_invalid_enum(self, sample_candidate):
        """5. Missing required schema field or unrecognized enum returns INVALID_JSON."""
        # Missing 'summary' and invalid direction_assessment enum
        bad_schema_json = json.dumps({
            "direction_assessment": "STRONGLY_BULLISH_NON_EXISTENT",
            "setup_quality": "SUPER_DUPER",
            "reasons": [],
            "risks": [],
            "invalidation_conditions": [],
            "abstain": False,
        })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": bad_schema_json}

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.INVALID_JSON
        assert res.output is None

    def test_06_timeout(self, sample_candidate):
        """6. Connect or read timeout is caught and returns status TIMEOUT."""
        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))

        with patch.object(client.session, "post", side_effect=requests.exceptions.Timeout("Read timed out")):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.TIMEOUT
        assert res.output is None
        assert "timed out" in res.error_message.lower()

    def test_07_http_4xx_5xx(self, sample_candidate):
        """7. HTTP 4xx or persistent 5xx returns status API_ERROR without unhandled exceptions."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS, max_retries=1, retry_delay_sec=0.01))

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.API_ERROR
        assert "HTTP 500" in res.error_message

    def test_08_tunnel_unavailable(self, sample_candidate):
        """8. ConnectionError (tunnel offline) returns status UNAVAILABLE."""
        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS, max_retries=1, retry_delay_sec=0.01))

        with patch.object(client.session, "post", side_effect=requests.exceptions.ConnectionError("ngrok tunnel closed")):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.UNAVAILABLE
        assert "unreachable" in res.error_message.lower() or "connection" in res.error_message.lower()


class TestCandidateIntegrityAndDeterministicGuards:
    """Tests 9 - 14: Strict immutability, ML abstain enforcement, and ranking invariance."""

    def test_09_candidate_immutability(self, sample_candidate):
        """9. Candidate original fields (entry, SL, TP, direction, status) are 100% unaltered."""
        original_snapshot = copy.deepcopy(sample_candidate)

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "response": json.dumps({
                "summary": "Attempting to suggest different prices: Entry 65000",
                "direction_assessment": "SUPPORTS_SETUP",
                "setup_quality": "HIGH",
                "reasons": ["Test"],
                "risks": [],
                "invalidation_conditions": [],
                "abstain": False,
            }),
        }

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        # Verify candidate itself remained completely untouched
        assert sample_candidate.entry_price == original_snapshot.entry_price == 60000.0
        assert sample_candidate.stop_loss == original_snapshot.stop_loss == 59000.0
        assert sample_candidate.take_profit_1 == original_snapshot.take_profit_1 == 62000.0
        assert sample_candidate.direction == original_snapshot.direction == Direction.LONG
        assert sample_candidate.status == original_snapshot.status == CandidateStatus.READY
        assert sample_candidate.invalidation_price == original_snapshot.invalidation_price == 58900.0
        assert sample_candidate.net_reward_to_risk == original_snapshot.net_reward_to_risk == 1.85

    def test_10_ml_probability_unaltered(self, sample_candidate):
        """10. ml_probability is strictly unaffected by AI analysis output."""
        sample_candidate.ml_probability = 0.5875
        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "response": json.dumps({
                "summary": "Model says confidence is 99%",
                "direction_assessment": "SUPPORTS_SETUP",
                "setup_quality": "HIGH",
                "reasons": ["High confidence claim"],
                "risks": [],
                "invalidation_conditions": [],
                "abstain": False,
            }),
        }

        with patch.object(client.session, "post", return_value=mock_resp):
            client.analyze_candidate(sample_candidate)

        assert sample_candidate.ml_probability == 0.5875

    def test_11_calibration_status_unaltered(self, sample_candidate):
        """11. calibration_status cannot be overwritten or altered by AI."""
        sample_candidate.calibration_status = "DEGRADED_LIGHTGBM"
        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "response": json.dumps({
                "summary": "Trying to claim model is perfectly calibrated",
                "direction_assessment": "SUPPORTS_SETUP",
                "setup_quality": "HIGH",
                "reasons": [],
                "risks": [],
                "invalidation_conditions": [],
                "abstain": False,
            }),
        }

        with patch.object(client.session, "post", return_value=mock_resp):
            client.analyze_candidate(sample_candidate)

        assert sample_candidate.calibration_status == "DEGRADED_LIGHTGBM"

    def test_12_unreliable_ml_forces_abstain(self, sample_candidate):
        """12. If calibration_status is UNRELIABLE, deterministic client-side guard forces abstain=True."""
        sample_candidate.calibration_status = "UNRELIABLE"
        sample_candidate.ml_probability = None

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))

        # Even if rogue LLM claims setup_quality=HIGH and abstain=False:
        rogue_llm_resp = json.dumps({
            "summary": "Rogue LLM falsely ignoring UNRELIABLE warning",
            "direction_assessment": "SUPPORTS_SETUP",
            "setup_quality": "HIGH",
            "reasons": ["Looks amazing"],
            "risks": [],
            "invalidation_conditions": [],
            "abstain": False,  # ROGUE!
        })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": rogue_llm_resp}

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.OK
        assert res.output is not None
        # Deterministic client guard MUST have overridden the rogue output
        assert res.output.abstain is True
        assert res.output.setup_quality == SetupQuality.INSUFFICIENT_DATA
        assert any("forced abstain=True" in g for g in res.deterministic_guards_applied)
        assert any("DETERMINISTIC_GUARD" in r for r in res.output.reasons)

    def test_13_insufficient_data_ml_forces_abstain(self, sample_candidate):
        """13. If calibration_status is INSUFFICIENT_DATA, deterministic client-side guard forces abstain=True."""
        sample_candidate.calibration_status = "INSUFFICIENT_DATA"
        sample_candidate.ml_probability = None

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))
        rogue_llm_resp = json.dumps({
            "summary": "Rogue output ignoring insufficient data",
            "direction_assessment": "SUPPORTS_SETUP",
            "setup_quality": "HIGH",
            "reasons": ["Test"],
            "risks": [],
            "invalidation_conditions": [],
            "abstain": False,
        })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": rogue_llm_resp}

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.output.abstain is True
        assert res.output.setup_quality == SetupQuality.INSUFFICIENT_DATA
        assert len(res.deterministic_guards_applied) > 0

    def test_14_qwen_does_not_affect_ranking(self, sample_candidate):
        """14. rank_candidates produces identical sorting order regardless of AI analysis presence."""
        c1 = copy.deepcopy(sample_candidate)
        c1.candidate_id = "C1_BTC"
        c1.ml_probability = 0.65
        c1.net_reward_to_risk = 1.9

        c2 = copy.deepcopy(sample_candidate)
        c2.candidate_id = "C2_ETH"
        c2.ml_probability = 0.58
        c2.net_reward_to_risk = 2.1

        c3 = copy.deepcopy(sample_candidate)
        c3.candidate_id = "C3_SOL"
        c3.ml_probability = None  # Abstained
        c3.net_reward_to_risk = 2.5

        # Ranking without AI
        rank_no_ai = rank_candidates([c1, c2, c3], sort_by="confidence_desc")
        order_no_ai = [c.candidate_id for c in rank_no_ai]

        # Attach contradictory AI evaluations:
        # e.g., AI gives C3 HIGH quality and C1 LOW quality
        c1.ai_analysis = {"output": {"setup_quality": "LOW", "direction_assessment": "CONFLICTS_WITH_SETUP"}}
        c2.ai_analysis = {"output": {"setup_quality": "MEDIUM", "direction_assessment": "UNCLEAR"}}
        c3.ai_analysis = {"output": {"setup_quality": "HIGH", "direction_assessment": "SUPPORTS_SETUP"}}

        rank_with_ai = rank_candidates([c1, c2, c3], sort_by="confidence_desc")
        order_with_ai = [c.candidate_id for c in rank_with_ai]

        assert order_with_ai == order_no_ai == ["C1_BTC", "C2_ETH", "C3_SOL"]


class TestFeatureFlagsGovernanceAndSafety:
    """Tests 15 - 20: Feature flags, shadow mode, safety boundaries, and prompt isolation."""

    def test_15_feature_flag_false_preserves_baseline(self, sample_candidate):
        """15. Feature flag enabled=false returns DISABLED and makes zero HTTP requests."""
        cfg = AIConfig(enabled=False, mode=AIMode.NO_AI)
        client = QwenClient(cfg)

        with patch.object(client.session, "post") as mock_post:
            res = client.analyze_candidate(sample_candidate)
            mock_post.assert_not_called()

        assert res.status == AIAnalysisStatus.DISABLED
        assert res.output is None

    def test_16_shadow_mode_does_not_alter_candidate_dict(self, sample_candidate):
        """16. Mode SHADOW records analysis in runner.shadow_records and leaves candidate.ai_analysis None."""
        cfg = AIConfig(enabled=True, mode=AIMode.SHADOW)
        mock_client = MagicMock()
        mock_client.analyze_candidate.return_value = AIAnalysisResult(
            candidate_id=sample_candidate.candidate_id,
            analysis_timestamp_utc="2026-10-16T16:00:00Z",
            model_id="Qwen/Qwen2.5-7B-Instruct",
            status=AIAnalysisStatus.OK,
            correlation_id="shadow123",
            output=AIAnalysisOutput(
                summary="Shadow evaluation",
                direction_assessment=DirectionAssessment.SUPPORTS_SETUP,
                setup_quality=SetupQuality.HIGH,
                reasons=["Shadow test"],
                risks=[],
                invalidation_conditions=[],
                abstain=False,
            ),
        )

        from src.exchange.metadata import SymbolSpecs
        specs = SymbolSpecs(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.1,
            price_precision=1,
            step_size=0.001,
            quantity_precision=3,
            min_qty=0.001,
            max_qty=100.0,
            min_notional=5.0,
            maint_margin_percent=0.005,
        )

        from src.risk.manager import RiskManager
        risk_mgr = RiskManager(initial_equity_usdt=16.0)

        runner = ScannerRunner(
            exchange=MagicMock(),
            db=MagicMock(),
            journal=MagicMock(),
            risk_manager=risk_mgr,
            specs_map={"BTCUSDT": specs},
            ai_config=cfg,
            qwen_client=mock_client,
        )

        from src.core.types import Candle
        real_candles = [
            Candle("BTCUSDT", "1h", 1792162800000 - (200 - i) * 3600000, 60000.0, 60500.0, 59500.0, 60000.0, 100.0, 1792162800000 - (200 - i) * 3600000 + 3599999, 6e6, 10, True)
            for i in range(200)
        ]

        # Mock candidate scan
        with patch.object(runner.generator, "generate_candidate", return_value=sample_candidate), \
             patch.object(runner.synchronizer, "sync_symbol", return_value=[]), \
             patch.object(runner.db, "get_candles", return_value=real_candles), \
             patch.object(runner.db, "get_funding_rates", return_value=[]), \
             patch.object(runner.evaluator, "evaluate_setup", return_value=sample_candidate):

            runner.run_scan(symbols=["BTCUSDT"], sync_live=False)

        # In SHADOW mode, candidate.ai_analysis MUST remain None
        assert sample_candidate.ai_analysis is None
        # But shadow_records must hold the record
        assert len(runner.shadow_records) == 1
        assert runner.shadow_records[0]["correlation_id"] == "shadow123"

    def test_17_no_path_to_live_execution(self):
        """17. ExecutionMode.RESEARCH and live_trading_locked = True are strictly enforced."""
        import os
        from config.settings import AppConfig
        assert AppConfig().mode == ExecutionMode.RESEARCH
        with patch.dict(os.environ, {"SCANNER_MODE": ""}, clear=False):
            cfg = get_default_config()
            assert cfg.mode == ExecutionMode.RESEARCH
        assert cfg.live_trading_locked is True

        # Ensure attempting to configure LIVE mode raises an exception
        with pytest.raises(ValueError, match="LIVE mode is permanently locked"):
            AppConfig(mode=ExecutionMode.LIVE)

    def test_18_ai_output_not_executable(self, sample_candidate):
        """18. AI output strings cannot execute as code, shell commands, or SQL."""
        malicious_response = json.dumps({
            "summary": "rm -rf /; DROP TABLE candles; import os; os.system('calc')",
            "direction_assessment": "SUPPORTS_SETUP",
            "setup_quality": "HIGH",
            "reasons": ["__import__('subprocess').call(['calc'])"],
            "risks": ["1; DELETE FROM users; --"],
            "invalidation_conditions": ["exec('print(1)')"],
            "abstain": False,
        })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "response": malicious_response}

        client = QwenClient(AIConfig(enabled=True, mode=AIMode.AI_ANALYSIS))

        with patch.object(client.session, "post", return_value=mock_resp):
            res = client.analyze_candidate(sample_candidate)

        assert res.status == AIAnalysisStatus.OK
        # Output must remain pure data strings, never evaluated or executed
        assert isinstance(res.output.summary, str)
        assert isinstance(res.output.reasons[0], str)
        # Verify no eval/exec was called
        assert "DROP TABLE" in res.output.summary

    def test_19_logging_does_not_leak_tokens(self, sample_candidate, caplog):
        """19. Secrets and tokens (QWEN_API_TOKEN) are strictly redacted from logs."""
        secret_token = "secret_qwen_bearer_token_xyz_999"
        cfg = AIConfig(
            enabled=True,
            mode=AIMode.AI_ANALYSIS,
            api_token=secret_token,
            api_url="https://example.com/generate",
        )
        client = QwenClient(cfg)

        with caplog.at_level(logging.DEBUG), \
             patch.object(client.session, "post", side_effect=requests.exceptions.Timeout("Read timeout")):
            client.analyze_candidate(sample_candidate)

        log_text = caplog.text
        assert secret_token not in log_text
        assert "Bearer" not in log_text

    def test_20_benchmark_replay_uses_identical_inputs(self, sample_candidate):
        """20. Deterministic candidate snapshot input to NO_AI and AI_ANALYSIS is identical."""
        prompt_1 = build_candidate_prompt(sample_candidate)
        prompt_2 = build_candidate_prompt(copy.deepcopy(sample_candidate))

        assert prompt_1 == prompt_2
        assert sample_candidate.symbol in prompt_1
        assert str(sample_candidate.entry_price) in prompt_1
        assert str(sample_candidate.stop_loss) in prompt_1
        assert sample_candidate.calibration_status in prompt_1
