"""Offline completeness and timing contracts for public theme collection."""
from datetime import date

import pytest
import requests

from maps.common.exceptions import DataCollectionError
from maps.data.naver_themes import NaverThemeAdapter


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Session:
    def __init__(self, responses, clock):
        self.responses = iter(responses)
        self.calls = []
        self.clock = clock

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, self.clock.now))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return Response(value)


class Response:
    def __init__(self, value):
        self.value = value
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def theme(no="1", name="Theme", count=1):
    return {"no": no, "name": name, "totalCnt": count}


def member(ticker="005930", name="Company", market="KOSPI"):
    return {"itemCode": ticker, "itemName": name, "marketType": market}


def member_page(*items, has_next=False, cursor=None):
    """Recorded-shaped domestic member cursor envelope (not the catalog list)."""
    return {"items": list(items), "hasNext": has_next, "cursor": cursor}


def collect(responses, expected=None, **kwargs):
    clock = Clock()
    session = Session(responses, clock)
    adapter = NaverThemeAdapter(session=session, sleep=clock.sleep,
                                monotonic=clock.monotonic, **kwargs)
    return adapter.collect(date(2026, 10, 7), expected or ["005930", "000660"]), session


def test_multiple_memberships_verified_empty_and_spacing():
    catalog = [theme(), theme("2", "Second", 2)]
    result, session = collect([catalog, member_page(member()), member_page(),
        member_page(member()), member_page(member("ABC123", market="KOSDAQ")), catalog])
    assert result.memberships == {"005930": ["1", "2"], "000660": []}
    assert result.catalog == {"1": "Theme", "2": "Second"}
    assert result.kind == "theme"
    assert all(b[2] - a[2] >= .25 for a, b in zip(session.calls, session.calls[1:]))


def test_actual_wire_catalog_uses_decimal_string_counts():
    catalog = [theme(no="27", name="자동차부품", count="2")]
    result, _ = collect([catalog, member_page(member(), member("000660")), member_page(), catalog])
    assert result.memberships == {"005930": ["27"], "000660": ["27"]}


def test_catalog_still_uses_page_numbers_beyond_200():
    catalog = [theme(str(i), count=1 if i == 0 else 0) for i in range(201)]
    responses = [catalog[:200], catalog[200:], member_page(member()), member_page()]
    responses += [member_page()] * 400 + [catalog[:200], catalog[200:]]
    result, session = collect(responses)
    assert len(result.catalog) == 201
    catalog_pages = [c[1]["params"]["startIdx"] for c in session.calls if c[0].endswith("/list")]
    assert catalog_pages == [0, 1, 0, 1]


def test_both_markets_use_independent_cursors_and_camel_case_members():
    catalog = [theme(count=4)]
    result, session = collect([catalog,
        member_page(member(), has_next=True, cursor="kospi-next"),
        member_page(member("000660")),
        member_page(member("035720", market="KOSDAQ"), has_next=True, cursor="kosdaq-next"),
        member_page(member("OUTSIDE", market="KOSDAQ")), catalog],
        expected=["005930", "000660", "035720", "NONE"])
    assert result.memberships == {"005930": ["1"], "000660": ["1"], "035720": ["1"], "NONE": []}
    assert result.metrics["source_relation_count"] == 4
    assert [call[1]["params"] for call in session.calls[1:5]] == [
        {"marketType": "KOSPI", "orderType": "marketSum", "size": 50},
        {"marketType": "KOSPI", "orderType": "marketSum", "size": 50, "cursor": "kospi-next"},
        {"marketType": "KOSDAQ", "orderType": "marketSum", "size": 50},
        {"marketType": "KOSDAQ", "orderType": "marketSum", "size": 50, "cursor": "kosdaq-next"}]


def test_recorded_spac_contract_allows_empty_kospi_and_fifty_item_kosdaq_page():
    catalog = [theme("284", "SPAC", 51)]
    tickers = [f"A{i:05}" for i in range(51)]
    result, _ = collect([catalog, member_page(),
        member_page(*(member(t, market="KOSDAQ") for t in tickers[:50]),
                    has_next=True, cursor="opaque-next"),
        member_page(member(tickers[50], market="KOSDAQ")), catalog], expected=[tickers[0]])
    assert result.memberships == {tickers[0]: ["284"]}
    assert result.metrics["source_relation_count"] == 51


@pytest.mark.parametrize("responses", [
    [[]], [None], [{"error": "failed"}], [[None]],
    [[theme(), theme()]], [[theme(name=" ")]], [[theme(no=None)]],
    [[theme(count=-1)]], [[theme(count=True)]], [[theme(count="1.0")]],
    [[theme(count="-1")]], [[theme(count=1.5)]], [[theme(count="nan")]],
    [[theme()], []],
    [[theme()], {"error": "member source failed"}], [[theme()], [None]],
    [[theme(count=2)], member_page(member(), member())],
    [[theme()], member_page(member(ticker=" "))], [[theme()], member_page(member(ticker=None))],
    [[theme()], member_page(member(ticker="A B"))], [[theme()], member_page(member(ticker="A\x00"))],
    [[theme()], member_page(member(ticker="A" * 17))], [[theme()], member_page(member(name=""))],
    [[theme()], member_page(member("OUTSIDE")), member_page(), [theme()]],
    [[theme()], member_page(member()), member_page(), [theme(name="Changed")]],
    [[theme()], member_page(member()), member_page(), [theme(count=2)]],
    [[theme()], member_page(), member_page()],
    [ValueError("bad JSON")],
])
def test_invalid_or_incomplete_source_fails(responses):
    with pytest.raises(DataCollectionError):
        collect(responses)


def test_timeout_retry_recovers_and_is_bounded():
    result, session = collect([requests.Timeout(), [theme()], member_page(member()), member_page(), [theme()]])
    assert result.memberships["005930"] == ["1"]
    assert len(session.calls) == 5
    with pytest.raises(DataCollectionError):
        collect([requests.Timeout()] * 3)


def test_deadline_during_spacing_prevents_next_request():
    with pytest.raises(DataCollectionError, match="deadline"):
        collect([[theme()], member_page(member()), member_page(), [theme()]], deadline_seconds=.2)


def test_full_page_guard_does_not_claim_complete():
    catalog = [[theme(str(page * 200 + i), count=0) for i in range(200)] for page in range(100)]
    with pytest.raises(DataCollectionError, match="page"):
        collect(catalog)


@pytest.mark.parametrize("status, retry", [(429, True), (503, True), (404, False)])
def test_http_status_retries_only_transient_errors(status, retry):
    response = requests.Response()
    response.status_code = status
    error = requests.HTTPError(response=response)
    if retry:
        result, _ = collect([error, [theme()], member_page(member()), member_page(), [theme()]])
        assert result.memberships["005930"] == ["1"]
    else:
        with pytest.raises(DataCollectionError):
            collect([error])


def test_deadline_after_http_return_rejects_response():
    clock = Clock()
    class SlowSession(Session):
        def get(self, url, **kwargs):
            result = super().get(url, **kwargs)
            clock.now += 2
            return result
    session = SlowSession([[theme()]], clock)
    adapter = NaverThemeAdapter(session=session, sleep=clock.sleep,
        monotonic=clock.monotonic, deadline_seconds=1)
    with pytest.raises(DataCollectionError, match="deadline"):
        adapter.collect(date(2026, 10, 7), ["005930"])
    assert session.calls[0][1]["timeout"] <= 1


def test_duplicate_member_across_pages_fails_even_with_matching_count():
    with pytest.raises(DataCollectionError, match="duplicate"):
        collect([[theme(count=2)], member_page(member(), has_next=True, cursor="next"),
                 member_page(member())])


def test_full_member_page_with_explicit_completion_needs_no_extra_request():
    members = [member("005930")] + [member(f"A{i}") for i in range(49)]
    result, session = collect([[theme(count=50)], member_page(*members), member_page(), [theme(count=50)]])
    assert result.memberships["005930"] == ["1"]
    assert len(session.calls) == 4


@pytest.mark.parametrize("page", [
    None, [], {}, {"items": None, "hasNext": False, "cursor": None},
    {"items": [member()], "hasNext": "false", "cursor": None},
    {"items": [member()], "cursor": None},
    {"items": [member()], "hasNext": False},
    member_page(None), member_page(*([member()] * 51)),
    member_page(has_next=True, cursor="next"),
    member_page(member(), has_next=True),
    member_page(member(), has_next=True, cursor=" "),
    member_page(member(), has_next=True, cursor=123),
    member_page(member(market="KOSDAQ")), member_page(member(market="ALL")),
    member_page({"itemCode": "005930", "itemName": "Company"}),
    member_page({"itemcode": "005930", "itemname": "Company", "marketType": "KOSPI"}),
])
def test_malformed_member_envelope_or_market_fails(page):
    with pytest.raises(DataCollectionError):
        collect([[theme()], page])


def test_duplicate_membership_across_markets_fails():
    with pytest.raises(DataCollectionError, match="duplicate"):
        collect([[theme(count=2)], member_page(member()), member_page(member(market="KOSDAQ"))])


def test_repeated_member_cursor_fails_even_with_unique_items():
    with pytest.raises(DataCollectionError, match="cursor"):
        collect([[theme(count=3)], member_page(member(), has_next=True, cursor="loop"),
            member_page(member("000660"), has_next=True, cursor="loop")])


def test_member_page_guard_never_claims_completion():
    pages = [member_page(member(f"A{i}"), has_next=True, cursor=f"next-{i}") for i in range(100)]
    with pytest.raises(DataCollectionError, match="page guard"):
        collect([[theme(count=100)], *pages])


@pytest.mark.parametrize("failure_tail, error", [
    ([requests.Timeout()] * 3, "HTTP request failed"),
    ([member_page(member("000660", market="KOSDAQ")), [theme(name="Changed")]], "manifest changed"),
])
def test_incomplete_snapshot_preserves_last_publication(db, failure_tail, error):
    """Exercise real job/repository publication with a failing second market or resnapshot."""
    import datetime as dt
    from unittest.mock import Mock
    from sqlalchemy.orm import sessionmaker
    from maps.common.models import ClassificationMember, ClassificationRun
    from maps.common.settings import MapsSettings
    from maps.data.classifications import ClassificationPayload, ClassificationRepository
    from maps.ops.classification_jobs import ClassificationJobs

    day = date(2026, 10, 22)
    repo = ClassificationRepository(db)
    repo.publish(repo.start("sector", "krx", day), ClassificationPayload(
        "sector", "krx", day, ["005930", "000660"], {"S": "Sector"},
        {"005930": ["S"], "000660": ["S"]}))
    old = repo.publish(repo.start("theme", "naver", day), ClassificationPayload(
        "theme", "naver", day, ["005930", "000660"], {"9": "Previous"},
        {"005930": ["9"], "000660": []}))
    old_id = old.id
    clock = Clock()
    session = Session([[theme(count=2)], member_page(member()), *failure_tail], clock)
    adapter = NaverThemeAdapter(session=session, sleep=clock.sleep, monotonic=clock.monotonic)
    job = ClassificationJobs(MapsSettings(_env_file=None, maps_theme_collection_enabled=True),
        sessionmaker(bind=db.get_bind()), Mock(), adapter, now=lambda: dt.datetime(2026, 10, 22, 18))
    result = job.collect(day)
    assert result["status"] == "failed"
    assert error in result["error"]
    db.expire_all()
    assert repo.latest("theme").id == old_id
    assert repo.memberships(repo.latest("theme")) == {"005930": {"9"}, "000660": set()}
    latest_attempt = db.query(ClassificationRun).filter_by(kind="theme").order_by(ClassificationRun.id.desc()).first()
    assert latest_attempt.status == "failed"
    assert latest_attempt.published_at is None
    assert db.query(ClassificationMember).filter_by(run_id=latest_attempt.id).count() == 0


@pytest.mark.parametrize("code", ["../list", "-1", "abc"])
def test_theme_code_must_be_numeric_identifier(code):
    with pytest.raises(DataCollectionError, match="code"):
        collect([[theme(no=code)], member_page(member()), member_page(), [theme(no=code)]])


def test_universe_duplicates_fail_before_network():
    with pytest.raises(DataCollectionError, match="universe"):
        collect([], expected=["005930", "005930"])


def test_numeric_theme_code_is_normalized():
    result, _ = collect([[theme(no=123)], member_page(member()), member_page(), [theme(no=123)]])
    assert result.catalog == {"123": "Theme"}
    assert result.memberships["005930"] == ["123"]


def mobile_page(tickers, *, has_next=False, cursor=None, name="Theme"):
    """Represent the public mobile API's explicit completion contract."""
    return {"isSuccess": True, "result": {
        "sectorInfo": {"sectorName": name},
        "items": [{"itemCode": ticker, "name": "Company"} for ticker in tickers],
        "hasNext": has_next, "cursor": cursor,
    }}


def test_stale_catalog_count_requires_matching_complete_mobile_memberships():
    catalog = [theme(count=3)]
    result, session = collect([catalog, member_page(member(), member("000660")), member_page(),
        mobile_page(["005930"], has_next=True, cursor="next-page"),
        mobile_page(["000660"]), catalog])
    assert result.memberships == {"005930": ["1"], "000660": ["1"]}
    assert result.metrics["source_count_discrepancies"] == [
        {"theme_code": "1", "advertised_count": 3, "verified_count": 2,
         "verification": "mobile_cursor_complete"}]
    assert session.calls[4][1]["params"]["cursor"] == "next-page"


def test_real_mobile_pagination_has_theme_info_only_on_first_page():
    tickers = [f"A{i:05}" for i in range(69)]
    catalog = [theme("284", "SPAC", 70)]
    first = mobile_page(tickers[:50], has_next=True, cursor="next", name="SPAC")
    last = mobile_page(tickers[50:])
    last["result"]["sectorInfo"] = None
    result, _ = collect([catalog, member_page(),
                        member_page(*(member(t, market="KOSDAQ") for t in tickers[:50]),
                                    has_next=True, cursor="desktop-next"),
                        member_page(*(member(t, market="KOSDAQ") for t in tickers[50:])),
                        first, last, catalog],
                        expected=[tickers[0], "000660"])
    assert result.memberships == {tickers[0]: ["284"], "000660": []}
    assert result.metrics["source_relation_count"] == 69


def test_mobile_duplicate_across_pages_is_not_deduplicated():
    with pytest.raises(DataCollectionError, match="duplicate"):
        collect([[theme(count=2)], member_page(member()), member_page(),
            mobile_page(["005930"], has_next=True, cursor="next"),
            mobile_page(["005930"])])


@pytest.mark.parametrize("pages", [
    [mobile_page(["000660"])],  # Same count, different members is not corroboration.
    [mobile_page(["005930"], name="Another theme")],
    [mobile_page(["005930"], has_next=True)],
    [mobile_page(["005930"], has_next="false")],
    [mobile_page(["005930", "005930"])],
    [mobile_page(["005930"], has_next=True, cursor="repeated"),
     mobile_page(["000660"], has_next=True, cursor="repeated")],
    [{"isSuccess": False, "result": {}}],
    [{"isSuccess": True, "result": None}],
    [{"isSuccess": True, "result": {"items": [], "hasNext": False}}],
    [mobile_page([None])],
    [mobile_page(["005930"] * 51)],
])
def test_count_mismatch_still_fails_without_complete_matching_evidence(pages):
    with pytest.raises(DataCollectionError):
        collect([[theme(count=2)], member_page(member()), member_page(), *pages])
