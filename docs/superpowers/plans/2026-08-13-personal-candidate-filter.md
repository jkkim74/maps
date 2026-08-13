# 개인 후보 필터 연결 · 알림 설정 제거 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/settings` 에 저장만 되고 동작하지 않던 개인 설정을 없앤다 — 후보 필터 2개는 실제로 연결하고, 알림 3개는 삭제한다.

**Architecture:** `GET /api/v1/candidates` 가 요청자의 개인 설정을 읽어 SQL `WHERE` 에 두 조건을 더한다. `.limit(200)` 보다 앞에서 건다. 집계 필드와 응답 스키마는 바꾸지 않고, 화면에 필터 배지만 추가한다.

**Tech Stack:** FastAPI · SQLAlchemy · Pydantic v2 · Jinja2 · vanilla JS · pytest

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-13-personal-candidate-filter-design.md`
- 모든 함수·클래스에 타입 힌트와 docstring (루트 `CLAUDE.md` 규약)
- 설정은 `maps.common.settings.get_settings()` 로만 접근. `os.getenv` 직접 호출 금지
- `pytest` `asyncio_mode = "auto"`
- **화면 필터는 주문 게이트가 아니다.** `ops/order_preview.py`·`ops/scheduler.py` 의 전역 `maps_candidate_min_score` 사용은 절대 건드리지 않는다
- 마이그레이션 없음. DB 스키마 변경 없음
- 커밋 메시지는 한국어 본문 + 영어 제목(기존 이력과 동일), 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

### Task 1: 알림 설정 3개 제거

`notify_push`, `notify_telegram`, `telegram_chat_id` 는 발송 경로를 사용자별로 분리해야 동작하는데 그건 이 작업의 범위 밖이다. 스키마와 화면에서 삭제한다. 운영 `app_user` 2계정 모두 `preferences IS NULL` 이라 마이그레이션·정리 스크립트가 필요 없다.

**Files:**
- Modify: `maps/api/schemas.py:1112-1126` (`UserPreferences`)
- Modify: `templates/settings.html:28-33`
- Modify: `static/js/settings.js:30-32,41-43`
- Test: `tests/test_users.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces: `UserPreferences` 가 `landing_screen: str`, `candidate_min_score: float | None`, `candidate_markets: list[str]` 3필드만 갖는다. Task 2 가 이 모델을 읽는다

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_users.py` 는 인증을 켠 `client` 픽스처를 쓰고 **PUT 전에 `login()` 이 필요**하다.

먼저 **기존 테스트를 고친다.** `test_preferences_round_trip_and_reject_unknown_keys`
(139~152행)가 지금 `notify_push` 를 단언하고 있어 필드를 지우면 깨진다. 다음으로 교체:

```python
def test_preferences_round_trip_and_reject_unknown_keys(client: TestClient) -> None:
    login(client, "member", _USER_PW)

    saved = client.put(
        "/api/v1/users/me/preferences",
        json={
            "landing_screen": "candidates",
            "candidate_min_score": 42.5,
            "candidate_markets": ["KOSPI"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["preferences"]["landing_screen"] == "candidates"
    assert saved.json()["preferences"]["candidate_min_score"] == 42.5

    fetched = client.get("/api/v1/users/me").json()["preferences"]
    assert fetched["candidate_markets"] == ["KOSPI"]
    rejected = client.put("/api/v1/users/me/preferences", json={"maps_broker_mode": "kis"})
    assert rejected.status_code == 422
```

그다음 파일 끝에 회귀 테스트를 추가한다:

```python
def test_removed_notification_prefs_are_rejected(client: TestClient) -> None:
    """삭제한 알림 키는 extra=forbid 로 거절된다 — 조용히 무시되면 안 된다."""
    login(client, "member", _USER_PW)

    response = client.put(
        "/api/v1/users/me/preferences",
        json={"landing_screen": "candidates", "notify_push": True},
    )
    assert response.status_code == 422


def test_preferences_no_longer_expose_notification_keys(client: TestClient) -> None:
    """응답에서도 알림 키가 사라져야 한다."""
    login(client, "member", _USER_PW)

    prefs = client.get("/api/v1/users/me").json()["preferences"]
    assert "notify_push" not in prefs
    assert "notify_telegram" not in prefs
    assert "telegram_chat_id" not in prefs
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_users.py::test_removed_notification_prefs_are_rejected -v`
Expected: FAIL — 현재는 `notify_push` 가 유효한 키라 200 이 돌아온다

- [ ] **Step 3: 스키마에서 3필드 삭제**

`maps/api/schemas.py` — `UserPreferences` 를 다음으로 교체:

```python
class UserPreferences(BaseModel):
    """개인 설정 — 전 필드 기본값이 있고, 없는 키는 저장하지 않는다.

    여기 정의된 값은 **자기 화면에만** 적용된다. 주문 게이트·스케줄러 같은 운영값은
    전역 `.env` 가 정본이며 사용자 설정으로 덮이지 않는다.
    """

    model_config = {"extra": "forbid"}

    landing_screen: str = "stock-analysis"
    candidate_min_score: float | None = None      # 후보 화면 표시 필터 (주문 게이트 아님)
    candidate_markets: list[str] = []             # 빈 목록 = 전체
```

- [ ] **Step 4: 화면에서 알림 블록 삭제**

`templates/settings.html` — 28~33행(`<h3>알림</h3>` 부터 `</label>` 까지) 전체 삭제.

`static/js/settings.js` — 30~32행 삭제:

```javascript
  $('pref-notify-push').checked = !!prefs.notify_push;
  $('pref-notify-telegram').checked = !!prefs.notify_telegram;
  $('pref-telegram-chat').value = prefs.telegram_chat_id || '';
```

같은 파일 41~43행 삭제:

```javascript
    notify_push: $('pref-notify-push').checked,
    notify_telegram: $('pref-notify-telegram').checked,
    telegram_chat_id: $('pref-telegram-chat').value.trim() || null,
```

- [ ] **Step 5: 화면설계서에서 삭제한 필드 제거**

`docs/ui-design/maps-auth-screen-design.html` 의 SET-01(내 설정) 절에서
`<code>notify_push</code>`, `<code>notify_telegram</code>`, `<code>telegram_chat_id</code>`
항목 행을 삭제한다.

`tests/test_auth_screen_design_doc.py:146` 은 `UserPreferences.model_fields` 를 순회하며
**문서에 없는 필드**를 찾는다. 필드를 지우기만 해도 테스트는 통과하지만, 문서에 존재하지
않는 설정이 남으면 다음 사람이 구현된 줄 안다. 코드가 정본이므로 문서를 맞춘다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_users.py tests/test_auth_screen_design_doc.py -v`
Expected: 전부 PASS

Run: `node --check static/js/settings.js`
Expected: 출력 없음(문법 정상)

- [ ] **Step 7: 커밋**

```bash
git add maps/api/schemas.py templates/settings.html static/js/settings.js \
        docs/ui-design/maps-auth-screen-design.html tests/test_users.py
git commit -m "refactor: drop unwired notification preferences"
```

---

### Task 2: 후보 API 에 개인 필터 적용

**Files:**
- Modify: `maps/common/user_prefs.py:24-38` (`resolve` 의 전역값 채움 삭제)
- Modify: `maps/api/candidates.py:19-60`
- Test: `tests/test_candidates_api.py`

**Interfaces:**
- Consumes: Task 1 의 `UserPreferences` (3필드)
- Produces: `maps/api/candidates.py` 에 `_viewer_prefs(request: Request, db: Session) -> UserPreferences | None`. `None` 이면 필터를 걸지 않는다

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_candidates_api.py` 는 로컬 `ctx` 픽스처(`yield client, factory`)를 쓴다. 그 규약을 그대로 따른다.

파일 상단 import 에 추가:

```python
from maps.api import candidates as candidates_api
from maps.api.auth import Identity
from maps.common.models import AppUser
from maps.common.passwords import hash_password
```

`ctx` 픽스처 아래에 헬퍼 두 개를 추가한다. 테스트는 인증이 꺼진 상태로 돌기 때문에
(`tests/conftest.py` 의 autouse fixture) `current_identity` 가 `ANONYMOUS_ADMIN`(`id=None`)
을 돌려준다. 필터 경로를 태우려면 요청 주체를 직접 바꿔야 한다:

```python
def _snapshot(ticker: str, score: float, market: str) -> CandidateSnapshot:
    """테스트용 후보 스냅샷 한 행 (기준일 고정)."""
    return CandidateSnapshot(
        ref_date=dt.date(2026, 8, 13),
        strategy_id="pullback_v3",
        ticker=ticker,
        name=f"종목{ticker}",
        market=market,
        factor_score=score,
        trend_strength=50.0,
        ts_bucket="S3",
        final_score=score,
        weekly_pass=True,
    )


def _seed_user(factory, monkeypatch, username: str, preferences: dict) -> None:
    """개인 설정을 가진 계정을 만들고 요청 주체를 그 계정으로 고정한다."""
    db = factory()
    try:
        user = AppUser(
            username=username,
            password_hash=hash_password("pw12345678"),
            role="user",
            status="active",
            preferences=preferences,
        )
        db.add(user)
        db.commit()
        identity = Identity(id=user.id, username=user.username, role=user.role)
    finally:
        db.close()
    monkeypatch.setattr(candidates_api, "current_identity", lambda request: identity)


def _seed_snapshots(factory, snapshots: list[CandidateSnapshot]) -> None:
    """후보 스냅샷을 커밋한다."""
    db = factory()
    try:
        db.add_all(snapshots)
        db.commit()
    finally:
        db.close()
```

파일 끝에 테스트 5건을 추가한다:

```python
def test_personal_min_score_filters_list(ctx, monkeypatch) -> None:
    """개인 최소 점수 미만 후보는 목록에서 빠진다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000002", 20.0, "KOSPI"),
    ])
    _seed_user(factory, monkeypatch, "filteruser", {"candidate_min_score": 50.0})

    tickers = [c["ticker"] for c in client.get("/api/v1/candidates").json()["candidates"]]
    assert tickers == ["000001"]


def test_personal_market_filters_list(ctx, monkeypatch) -> None:
    """선택한 시장만 남는다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000003", 90.0, "KOSDAQ"),
    ])
    _seed_user(factory, monkeypatch, "marketuser", {"candidate_markets": ["KOSPI"]})

    tickers = [c["ticker"] for c in client.get("/api/v1/candidates").json()["candidates"]]
    assert tickers == ["000001"]


def test_auth_disabled_returns_everything(ctx) -> None:
    """인증이 꺼진 환경(로컬·테스트 기본)에서는 필터가 걸리지 않는다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000002", 1.0, "KOSDAQ"),
    ])

    body = client.get("/api/v1/candidates").json()
    assert len(body["candidates"]) == 2


def test_filter_runs_before_limit(ctx, monkeypatch) -> None:
    """필터가 .limit(200) 앞에서 걸린다 — 뒤에 걸리면 저점수 200행에 밀려 고점수가 사라진다."""
    client, factory = ctx
    rows = [_snapshot(f"1{i:05d}", 10.0, "KOSPI") for i in range(210)]
    rows.append(_snapshot("999999", 95.0, "KOSPI"))
    _seed_snapshots(factory, rows)
    _seed_user(factory, monkeypatch, "limituser", {"candidate_min_score": 90.0})

    tickers = [c["ticker"] for c in client.get("/api/v1/candidates").json()["candidates"]]
    assert tickers == ["999999"]


def test_counts_stay_pipeline_values(ctx, monkeypatch) -> None:
    """집계는 파이프라인 통계다 — 개인 필터로 줄어들지 않는다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000002", 20.0, "KOSPI"),
    ])
    _seed_user(factory, monkeypatch, "countuser", {"candidate_min_score": 50.0})

    body = client.get("/api/v1/candidates").json()
    assert len(body["candidates"]) == 1
    assert body["final_count"] == 2
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_candidates_api.py::test_personal_min_score_filters_list -v`
Expected: FAIL — 현재 `get_candidates` 는 필터가 없어 2건 다 반환한다

- [ ] **Step 3: `resolve()` 의 전역값 채움 삭제**

`maps/common/user_prefs.py` — `resolve()` 끝의 다음 3줄을 삭제한다:

```python
    if prefs.candidate_min_score is None:
        prefs.candidate_min_score = settings.maps_candidate_min_score
    return prefs
```

교체:

```python
    return prefs
```

전역 주문 게이트 값(`maps_candidate_min_score`)을 화면 필터 기본값으로 채우면, 설정한 적 없는 사용자에게도 필터가 걸리고 **주문 게이트와 화면 필터가 한 값으로 묶인다.** 설계가 분리하라고 한 지점이다. `static/js/settings.js` 는 이미 `?? ''` 로 null 을 처리하므로 화면에는 빈칸(= 미설정)으로 보인다.

`settings` 파라미터가 더 이상 쓰이지 않으면 시그니처에서 빼지 말고 그대로 둔다 — 호출부(`api/users.py`)가 넘기고 있고, 지우면 그 파일까지 고쳐야 한다.

- [ ] **Step 4: 후보 API 에 필터 적용**

`maps/api/candidates.py` — import 에 추가:

```python
from fastapi import APIRouter, Depends, Query, Request

from maps.api.auth import current_identity, load_user
from maps.api.schemas import UserPreferences
from maps.common.user_prefs import resolve
```

파일 하단(라우터 함수 앞)에 헬퍼 추가:

```python
def _viewer_prefs(request: Request, db: Session) -> UserPreferences | None:
    """요청자의 개인 표시 설정. 필터를 걸지 않아야 하면 None.

    인증이 꺼진 환경은 `ANONYMOUS_ADMIN`(id=None)이라 필터 대상이 아니다.
    계정을 못 찾는 경우도 조회 화면이므로 fail-safe 로 전체를 보여 준다.
    """
    identity = current_identity(request)
    if identity.id is None:
        return None
    user = load_user(db, identity.username)
    return resolve(user) if user is not None else None
```

`get_candidates` 시그니처에 `request: Request` 를 첫 파라미터로 추가하고, `rows` 조회를 다음으로 교체한다(51~60행):

```python
    query = db.query(CandidateSnapshot).filter(
        CandidateSnapshot.strategy_id == strategy_id,
        CandidateSnapshot.ref_date == latest_date,
    )
    prefs = _viewer_prefs(request, db)
    if prefs is not None:
        if prefs.candidate_min_score is not None:
            query = query.filter(CandidateSnapshot.final_score >= prefs.candidate_min_score)
        if prefs.candidate_markets:
            query = query.filter(CandidateSnapshot.market.in_(prefs.candidate_markets))
    rows = (
        query.order_by(CandidateSnapshot.final_score.desc(), CandidateSnapshot.ticker.asc())
        .limit(200)
        .all()
    )
```

`final_count` 계산(42~50행)은 **건드리지 않는다.** 파이프라인 통계다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_candidates_api.py tests/test_users.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add maps/api/candidates.py maps/common/user_prefs.py tests/test_candidates_api.py
git commit -m "feat: apply personal candidate filters to the list"
```

---

### Task 3: 후보 화면에 필터 배지

필터가 걸려 있는데 화면에 표시가 없으면 "후보가 왜 이것뿐이지" 로 읽힌다. 집계(`final_count`)와 목록 개수가 다른 이유를 화면이 스스로 설명해야 한다.

**Files:**
- Modify: `static/js/app.js:420-510` (`loadCandidates`)
- Test: `tests/test_candidates_ui.py`

**Interfaces:**
- Consumes: Task 2 의 필터 동작. `GET /api/v1/users/me` 의 `preferences`
- Produces: 없음 (마지막 작업)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_candidates_ui.py` 끝에 추가:

```python
from pathlib import Path


def test_candidates_screen_renders_filter_badge() -> None:
    """개인 필터가 걸리면 화면이 그 사실과 해제 경로를 보여 준다."""
    source = Path("static/js/app.js").read_text(encoding="utf-8")
    assert "candidates-filter-badge" in source
    assert "/settings" in source
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_candidates_ui.py::test_candidates_screen_renders_filter_badge -v`
Expected: FAIL — `candidates-filter-badge` 문자열이 없다

- [ ] **Step 3: 배지 렌더 추가**

`static/js/app.js` 의 `loadCandidates` 안, `document.getElementById('candidates-area').innerHTML = ...` (491행) 직전에 추가:

```javascript
    let filterBadge = '';
    try {
      const me = await apiFetch('/users/me');
      const p = me.preferences || {};
      const parts = [];
      if (p.candidate_min_score != null) parts.push(`점수 ≥ ${p.candidate_min_score}`);
      if (p.candidate_markets && p.candidate_markets.length) parts.push(p.candidate_markets.join('·'));
      if (parts.length) {
        filterBadge = `<div id="candidates-filter-badge" class="text-muted mb-16" style="font-size:12px">
          내 필터 적용 중: ${parts.join(' / ')} · <a href="/settings">해제</a>
          <span> — 위 집계는 필터 이전의 파이프라인 값입니다</span>
        </div>`;
      }
    } catch (e) {
      filterBadge = '';   // ponytail: 배지 실패로 후보 목록을 막지 않는다
    }
```

그리고 같은 줄의 대입을 다음으로 바꾼다:

```javascript
    document.getElementById('candidates-area').innerHTML = filterBadge + `
```

(기존 템플릿 리터럴 시작 부분에 `filterBadge + ` 만 덧붙인다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_candidates_ui.py -v`
Expected: PASS

Run: `node --check static/js/app.js`
Expected: 출력 없음

- [ ] **Step 5: 전체 스위트 확인**

Run: `python -m pytest --tb=short -q`
Expected: 전부 PASS (기준: 이 계획 착수 시점 875 passed + 신규 7건)

- [ ] **Step 6: 커밋**

```bash
git add static/js/app.js tests/test_candidates_ui.py
git commit -m "feat: show personal candidate filter badge"
```

---

## 배포

마이그레이션이 없다. `git pull` + `systemctl restart maps` 로 끝난다. DB 백업 불필요.
16:00~16:45 KST 배포 금지(analyze cron).
