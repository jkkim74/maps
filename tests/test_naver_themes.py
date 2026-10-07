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


def member(ticker="005930", name="Company"):
    return {"itemcode": ticker, "itemname": name}


def collect(responses, expected=None, **kwargs):
    clock = Clock()
    session = Session(responses, clock)
    adapter = NaverThemeAdapter(session=session, sleep=clock.sleep,
                                monotonic=clock.monotonic, **kwargs)
    return adapter.collect(date(2026, 10, 7), expected or ["005930", "000660"]), session


def test_multiple_memberships_verified_empty_and_spacing():
    catalog = [theme(), theme("2", "Second", 2)]
    result, session = collect([catalog, [member()], [member(), member("ABC123")], catalog])
    assert result.memberships == {"005930": ["1", "2"], "000660": []}
    assert result.catalog == {"1": "Theme", "2": "Second"}
    assert result.kind == "theme"
    assert all(b[2] - a[2] >= .25 for a, b in zip(session.calls, session.calls[1:]))


def test_actual_wire_catalog_uses_decimal_string_counts():
    catalog = [theme(no="27", name="자동차부품", count="2")]
    result, _ = collect([catalog, [member(), member("000660")], catalog])
    assert result.memberships == {"005930": ["27"], "000660": ["27"]}


def test_catalog_and_members_use_page_number_beyond_200():
    catalog = [theme(str(i), count=201 if i == 0 else 0) for i in range(201)]
    members = [member("005930")] + [member(f"A{i}") for i in range(200)]
    responses = [catalog[:200], catalog[200:], members[:200], members[200:]]
    responses += [[]] * 200 + [catalog[:200], catalog[200:]]
    result, session = collect(responses)
    assert len(result.catalog) == 201
    catalog_pages = [c[1]["params"]["startIdx"] for c in session.calls if c[0].endswith("/list")]
    member_pages = [c[1]["params"]["startIdx"] for c in session.calls if "/theme/0/" in c[0]]
    assert catalog_pages == [0, 1, 0, 1]
    assert member_pages == [0, 1]


@pytest.mark.parametrize("responses", [
    [[]], [None], [{"error": "failed"}], [[None]],
    [[theme(), theme()]], [[theme(name=" ")]], [[theme(no=None)]],
    [[theme(count=-1)]], [[theme(count=True)]], [[theme(count="1.0")]],
    [[theme(count="-1")]], [[theme(count=1.5)]], [[theme(count="nan")]],
    [[theme()], []], [[theme()], [member(), member("000660")], {}],
    [[theme()], {"error": "member source failed"}], [[theme()], [None]],
    [[theme(count=2)], [member(), member()]],
    [[theme()], [member(ticker=" ")]], [[theme()], [member(ticker=None)]],
    [[theme()], [member(ticker="A B")]], [[theme()], [member(ticker="A\x00")]],
    [[theme()], [member(ticker="A" * 17)]], [[theme()], [member(name="")]],
    [[theme()], [member("OUTSIDE")], [theme()]],
    [[theme()], [member()], [theme(name="Changed")]],
    [[theme()], [member()], [theme(count=2)]],
    [ValueError("bad JSON")],
])
def test_invalid_or_incomplete_source_fails(responses):
    with pytest.raises(DataCollectionError):
        collect(responses)


def test_timeout_retry_recovers_and_is_bounded():
    result, session = collect([requests.Timeout(), [theme()], [member()], [theme()]])
    assert result.memberships["005930"] == ["1"]
    assert len(session.calls) == 4
    with pytest.raises(DataCollectionError):
        collect([requests.Timeout()] * 3)


def test_deadline_during_spacing_prevents_next_request():
    with pytest.raises(DataCollectionError, match="deadline"):
        collect([[theme()], [member()], [theme()]], deadline_seconds=.2)


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
        result, _ = collect([error, [theme()], [member()], [theme()]])
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
    members = [member(f"A{i}") for i in range(200)]
    with pytest.raises(DataCollectionError, match="duplicate"):
        collect([[theme(count=201)], members, [member("A0")]])


def test_exact_page_boundary_requires_terminal_page():
    members = [member("005930")] + [member(f"A{i}") for i in range(199)]
    result, session = collect([[theme(count=200)], members, [], [theme(count=200)]])
    assert result.memberships["005930"] == ["1"]
    assert session.calls[2][1]["params"]["startIdx"] == 1


@pytest.mark.parametrize("code", ["../list", "-1", "abc"])
def test_theme_code_must_be_numeric_identifier(code):
    with pytest.raises(DataCollectionError, match="code"):
        collect([[theme(no=code)], [member()], [theme(no=code)]])


def test_universe_duplicates_fail_before_network():
    with pytest.raises(DataCollectionError, match="universe"):
        collect([], expected=["005930", "005930"])


def test_numeric_theme_code_is_normalized():
    result, _ = collect([[theme(no=123)], [member()], [theme(no=123)]])
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
    result, session = collect([catalog, [member(), member("000660")],
        mobile_page(["005930"], has_next=True, cursor="next-page"),
        mobile_page(["000660"]), catalog])
    assert result.memberships == {"005930": ["1"], "000660": ["1"]}
    assert result.metrics["source_count_discrepancies"] == [
        {"theme_code": "1", "advertised_count": 3, "verified_count": 2,
         "verification": "mobile_cursor_complete"}]
    assert session.calls[3][1]["params"]["cursor"] == "next-page"


def test_real_mobile_pagination_has_theme_info_only_on_first_page():
    tickers = [f"A{i:05}" for i in range(69)]
    catalog = [theme("284", "SPAC", 70)]
    first = mobile_page(tickers[:50], has_next=True, cursor="next", name="SPAC")
    last = mobile_page(tickers[50:])
    last["result"]["sectorInfo"] = None
    result, _ = collect([catalog, [member(t) for t in tickers], first, last, catalog],
                        expected=[tickers[0], "000660"])
    assert result.memberships == {tickers[0]: ["284"], "000660": []}
    assert result.metrics["source_relation_count"] == 69


def test_mobile_duplicate_across_pages_is_not_deduplicated():
    with pytest.raises(DataCollectionError, match="duplicate"):
        collect([[theme(count=2)], [member()],
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
        collect([[theme(count=2)], [member()], *pages])
