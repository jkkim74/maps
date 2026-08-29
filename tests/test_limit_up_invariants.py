"""Source guards for the mistakes this engine keeps making.

Four review rounds produced the same shape of defect: a safety fix that quietly
severed an adjacent safety path. Each guard below encodes one of those, so the
next person who changes eight call sites and misses the ninth gets a red test
instead of a production incident.

Precedent in this repo: ``test_preview_and_order_paths_share_one_implementation``
(liquidity cap).
"""

from __future__ import annotations

import inspect
import re

from maps.execution import order_manager
from maps.limit_up import after_hours, service, worker
from maps.limit_up.domain import EXIT_STRATEGY_IDS, exit_strategy_id


def test_entry_policy_is_the_only_thing_that_reads_mode() -> None:
    """Only entry may ask "is mode automatic"; every exit asks exits_are_live().

    Conflating the two made ``emergency_off()`` block selling — the system marked
    positions closed while the shares were still in the account.
    """
    source = inspect.getsource(service)
    checks = re.findall(r"self\.mode is LimitUpMode\.AUTOMATIC", source)

    assert len(checks) == 1, (
        f"청산 경로가 mode 를 보고 있다 ({len(checks)}곳). "
        "진입은 FIRE_NET 한 곳뿐이고 나머지는 exits_are_live() 여야 한다."
    )
    # and that one check must live in the command dispatcher's entry branch
    dispatcher = inspect.getsource(service.LimitUpService._handle_commands)
    assert "CommandKind.FIRE_NET" in dispatcher
    assert "self.mode is LimitUpMode.AUTOMATIC" in dispatcher


def test_order_log_is_never_queried_by_a_raw_broker_id() -> None:
    """``order_log.order_id`` holds audit ids; broker views hand out bare ODNOs.

    Comparing them without normalizing silently matches nothing on KIS — and
    MockBroker cannot catch it because both forms are identical there.

    Checked per function: a lookup is fine as long as *that function* converts
    between the two forms somewhere (an audit-id lookup with a raw fallback is a
    legitimate pattern).
    """
    import ast

    for module in (order_manager, worker):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # 쿼리 필터(OrderLog.order_id == x)만 본다. row.order_id 를 감사 ID 집합과
            # 비교하는 것은 두 형식을 섞지 않으므로 대상이 아니다.
            compares_column = any(
                isinstance(cmp.left, ast.Attribute)
                and cmp.left.attr == "order_id"
                and isinstance(cmp.left.value, ast.Name)
                and cmp.left.value.id == "OrderLog"
                for cmp in ast.walk(node)
                if isinstance(cmp, ast.Compare)
            )
            if not compares_column:
                continue
            normalizes = any(
                isinstance(call.func, ast.Name)
                and call.func.id in {"raw_broker_order_id", "order_log_id"}
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            )
            assert normalizes, (
                f"{module.__name__}.{node.name}: order_log 를 조회하면서 "
                "감사 ID/원주문 ID 변환을 전혀 하지 않는다"
            )


def test_every_exit_reason_gets_its_own_strategy_id() -> None:
    """Sharing one id makes the duplicate-order guard block the second exit.

    The buy legs avoided this by splitting into ``:S``/``:A``; exits must too,
    or a filled 15:18 trim blocks the 15:28 forced liquidation.
    """
    reasons = [
        "hard_stop",
        "time_stop",
        "eod_review_fail",
        "overnight_cap_unfilled",
        "next_open",
        "after_hours_break_exit",
    ]
    ids = {reason: exit_strategy_id(reason) for reason in reasons}

    # the exits that can collide on the same day must not share an id
    assert ids["hard_stop"] != ids["overnight_cap_unfilled"]
    assert ids["next_open"] != ids["after_hours_break_exit"]
    assert ids["eod_review_fail"] != ids["hard_stop"]
    assert set(ids.values()) <= set(EXIT_STRATEGY_IDS.values())

    # no exit order may be built with a hardcoded shared id
    for module in (worker, after_hours):
        source = inspect.getsource(module)
        assert 'strategy_id="limit_up_v1:exit"' not in source, (
            f"{module.__name__}: 청산이 공용 전략 ID 를 하드코딩했다"
        )


def test_market_exits_are_capped_by_session_owned_quantity() -> None:
    """``get_position()`` is account-wide; selling it liquidates other strategies.

    ``sell_overnight_excess`` already caps with ``min(...)``; the market exit
    must not be the odd one out.
    """
    source = inspect.getsource(worker.LimitUpCommandWorker.sell_actual_position)

    assert "owned_quantity" in source
    assert "min(position.quantity" in source, "계좌 전체 보유로 매도하고 있다"
    assert "quantity=position.quantity" not in source


def test_a_session_holding_shares_is_never_closed_without_an_order() -> None:
    """Marking a held session CLOSED drops it out of every recovery path at once.

    ``recover()`` filters on ``state != CLOSED``, and so do the after-hours watch
    and the forced liquidation — the shares become invisible to all three.
    """
    source = inspect.getsource(service)
    # every virtual-close fallback must consult the strand guard first
    fallbacks = re.findall(r"elif not self\._strand_unprotected\(", source)

    assert len(fallbacks) >= 4, (
        f"주문 없이 CLOSED 로 가는 폴백이 보호되지 않았다 ({len(fallbacks)}곳만 확인됨)"
    )


def test_transient_latches_are_not_persisted() -> None:
    """A feed blip must not survive a restart and block the whole day.

    Nothing released ``feed_disconnected``, and persisting it made a one-second
    outage permanent.
    """
    source = inspect.getsource(service)

    assert "_TRANSIENT_LATCHES" in source
    assert "halted_reasons - _TRANSIENT_LATCHES" in source
    assert "def on_feed_reconnect" in source
