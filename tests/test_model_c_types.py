"""Unit tests for Model C schema definitions and research types.

Phase C0: Pump.fun Research Scope & Data Feasibility.
"""

import pytest
from src.model_c.types import (
    ModelCLifecycleState,
    DataFeasibilityStatus,
    DataSourceType,
    PumpFunProgramConstants,
    PumpFunTokenMetadata,
    BondingCurveState,
    ResearchLabelSpec,
)


def test_model_c_lifecycle_states_completeness():
    expected_states = {
        "DISCOVERED",
        "BONDING_CURVE",
        "MIGRATING",
        "MIGRATED",
        "ACTIVE",
        "ILLIQUID",
        "FAILED",
        "UNKNOWN",
    }
    actual_states = {s.value for s in ModelCLifecycleState}
    assert expected_states.issubset(actual_states)


def test_model_c_feasibility_status_values():
    assert {s.value for s in DataFeasibilityStatus} == {
        "FEASIBLE",
        "FEASIBLE_WITH_LIMITATIONS",
        "NOT_FEASIBLE",
    }


def test_pump_fun_canonical_constants():
    constants = PumpFunProgramConstants()
    assert constants.PROGRAM_ID == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    assert constants.TOTAL_SUPPLY == 1_000_000_000_000_000
    assert constants.BONDING_CURVE_ALLOCATION == 800_000_000_000_000
    assert constants.MIGRATION_DEX_RESERVE == 200_000_000_000_000
    assert constants.GRADUATION_REAL_SOL_TARGET == 85_000_000_000
    assert constants.DECIMALS == 6


def test_bonding_curve_state_derivation():
    # Initial state: 0 real SOL deposited
    state_initial = BondingCurveState.from_reserves(
        mint="TEST_MINT_1111",
        observed_timestamp_ms=1729800000000,
        slot=290000000,
        virtual_sol=30_000_000_000,
        virtual_token=1_073_000_000_000_000,
        real_sol=0,
        real_token=800_000_000_000_000,
        complete=False,
    )
    assert state_initial.progress_pct == 0.0
    assert state_initial.complete is False
    assert state_initial.marginal_price_sol > 0

    # Halfway state: ~42.5 SOL real deposited
    state_half = BondingCurveState.from_reserves(
        mint="TEST_MINT_1111",
        observed_timestamp_ms=1729800060000,
        slot=290000150,
        virtual_sol=72_500_000_000,
        virtual_token=600_000_000_000_000,
        real_sol=42_500_000_000,
        real_token=400_000_000_000_000,
        complete=False,
    )
    assert state_half.progress_pct == 50.0
    assert state_half.complete is False

    # Completed state: 85 SOL real deposited
    state_graduated = BondingCurveState.from_reserves(
        mint="TEST_MINT_1111",
        observed_timestamp_ms=1729800180000,
        slot=290000450,
        virtual_sol=115_000_000_000,
        virtual_token=273_000_000_000_000,
        real_sol=85_000_000_000,
        real_token=0,
        complete=True,
    )
    assert state_graduated.progress_pct == 100.0
    assert state_graduated.complete is True


def test_research_label_window_separation():
    label = ResearchLabelSpec(
        label_id="GRADUATED_WITHIN_2H",
        description="Token graduated to Raydium within 2 hours of discovery",
        observation_window_seconds=300,   # First 5 minutes of features
        prediction_horizon_seconds=7200,  # 2 hour outcome
        label_type="BINARY",
        evaluation_formula="lifecycle_state == 'MIGRATED'",
        leakage_prevention_rule="Strict window separation: features strictly computed for t <= t0 + 300s; target checked at t0 + 7200s to eliminate look-ahead leakage",
    )
    assert label.observation_window_seconds < label.prediction_horizon_seconds
    assert "leakage" in label.leakage_prevention_rule.lower()


def test_token_metadata_serialization():
    meta = PumpFunTokenMetadata(
        mint="SoL1111111111111111111111111111111111111111",
        bonding_curve_pda="Curve1111111111111111111111111111111111111",
        creator_address="Dev11111111111111111111111111111111111111",
        name="Test Meme",
        symbol="MEME",
        uri="https://arweave.net/test",
        creation_timestamp_ms=1729800000000,
        creation_slot=290000000,
        creation_tx_signature="sig123",
        initial_buy_sol=0.5,
        initial_buy_tokens=15_000_000.0,
    )
    d = meta.to_dict()
    assert d["mint"] == "SoL1111111111111111111111111111111111111111"
    assert d["initial_buy_sol"] == 0.5
