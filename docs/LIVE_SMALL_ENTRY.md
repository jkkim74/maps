# Live Small Entry Gate

MAPS blocks Mock -> Live Small promotion unless the strategy has completed one of these paths:

1. Mock trading for at least 3 months
2. Equivalent replay validation with at least 63 trading days and `replay_equivalent_passed=true`

The PromotionGate metrics are:

```text
mock_months
replay_equivalent_passed
replay_trading_days
```

Example Mock path:

```python
gate.evaluate(
    "pullback_v3",
    {
        "robustness": 1.0,
        "risk": 1.0,
        "recovery": 1.0,
        "return": 1.0,
        "mc_mdd_p95": 0.12,
        "mock_months": 3.0,
    },
    PromotionStage.MOCK_CANDIDATE,
)
```

Example replay-equivalent path:

```python
gate.evaluate(
    "pullback_v3",
    {
        "robustness": 1.0,
        "risk": 1.0,
        "recovery": 1.0,
        "return": 1.0,
        "mc_mdd_p95": 0.12,
        "mock_months": 0.0,
        "replay_equivalent_passed": 1.0,
        "replay_trading_days": 63,
    },
    PromotionStage.MOCK_CANDIDATE,
)
```

Keep `MAPS_LIVE_TRADING_ENABLED=false` until this gate passes and the operator manually approves Live Small.
