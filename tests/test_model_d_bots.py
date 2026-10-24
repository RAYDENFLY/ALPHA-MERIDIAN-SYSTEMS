import pytest
from types import SimpleNamespace
from src.model_d.bots import BotConfig, PersistentBotStore, default_bots
from src.data.database import DatabaseManager
from src.model_d.universe import ModelDUniverse


def test_three_bots_are_isolated_and_can_hold_same_symbol():
    bots = default_bots()
    assert set(bots) == {'MODEL_D_BOT_15', 'MODEL_D_BOT_25', 'MODEL_D_BOT_100'}
    for bot in bots.values(): bot.open('sig-1', 'BTCUSDT', 'LONG', 100.0, min_notional=0.1)
    assert [b.summary()['open_positions'] for b in bots.values()] == [1, 1, 1]
    bots['MODEL_D_BOT_15'].close('MODEL_D_BOT_15:sig-1', 101.0)
    assert bots['MODEL_D_BOT_25'].summary()['realized_pnl'] == 0


def test_duplicate_signal_and_infeasible_entry_rejected():
    bot = default_bots()['MODEL_D_BOT_15']
    bot.open('sig-1', 'BTCUSDT', 'LONG', 100.0, min_notional=0.1)
    with pytest.raises(ValueError, match='duplicate'):
        bot.open('sig-1', 'BTCUSDT', 'LONG', 100.0, min_notional=0.1)
    with pytest.raises(ValueError, match='INFEASIBLE'):
        bot.open('sig-2', 'BTCUSDT', 'LONG', 100.0, min_notional=1000.0)


def test_universe_filters_active_perpetuals():
    items = [SimpleNamespace(symbol='BTCUSDT', contract_type='PERPETUAL', is_active=True), SimpleNamespace(symbol='OLD', contract_type='PERPETUAL', is_active=False)]
    class Adapter:
        def discover_instruments(self, force_refresh=False): return items
    status = ModelDUniverse(Adapter()); status.refresh(); result = status.status()
    assert result['eligible_count'] == 1 and result['status'] == 'LIVE'


def test_persistent_bots_survive_reload_and_initialization_is_idempotent():
    from pathlib import Path
    import uuid
    db = DatabaseManager(Path('data') / f'model_d_bots_{uuid.uuid4().hex}.db')
    store = PersistentBotStore(db); configs = [BotConfig('MODEL_D_BOT_15', 15.0), BotConfig('MODEL_D_BOT_25', 25.0), BotConfig('MODEL_D_BOT_100', 100.0)]
    store.initialize(configs); bots = store.load(); position = bots['MODEL_D_BOT_25'].open('sig', 'ETHUSDT', 'SHORT', 100.0, min_notional=.1); store.persist_open(position)
    store.initialize(configs); reloaded = store.load()
    assert reloaded['MODEL_D_BOT_25'].equity == 25.0
    assert 'MODEL_D_BOT_25:sig' in reloaded['MODEL_D_BOT_25'].positions
    assert len(reloaded) == 3


def test_paper_exit_is_conservative_and_journaled():
    from pathlib import Path
    import uuid
    db = DatabaseManager(Path('data') / f'model_d_lifecycle_{uuid.uuid4().hex}.db')
    store = PersistentBotStore(db); store.initialize([BotConfig('MODEL_D_BOT_100', 100.0)])
    bots = store.load(); bot = bots['MODEL_D_BOT_100']
    position = bot.open('sig-exit', 'BTCUSDT', 'LONG', 100.0, min_notional=.1, stop_loss=95.0, take_profit=110.0)
    store.persist_open(position)
    assert bot.exit_trigger(position.position_id, high=112.0, low=94.0) == (95.0, 'STOP_LOSS')
    closed = bot.close(position.position_id, 95.0, fee_rate=0.001, reason='STOP_LOSS')
    store.persist_close(bot, closed)
    reloaded = store.load()['MODEL_D_BOT_100']
    assert reloaded.positions[position.position_id].status == 'CLOSED'
    assert {row['event_type'] for row in store.journal()} == {'ENTRY_COMMITTED', 'EXIT_COMMITTED'}


def test_structure_target_policy_is_directional_and_versioned():
    from types import SimpleNamespace
    from src.model_d.targets import derive_targets, TARGET_POLICY_VERSION
    stop, target, inputs = derive_targets('LONG', 100.0, SimpleNamespace(range_low=95.0, range_high=110.0))
    assert (stop, target) == (95.0, 110.0)
    assert inputs['policy_version'] == TARGET_POLICY_VERSION
    stop, target, _ = derive_targets('SHORT', 100.0, SimpleNamespace(range_low=90.0, range_high=105.0))
    assert (stop, target) == (105.0, 90.0)
