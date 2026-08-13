# 운영 설정 편집·변경 이력 구현 계획 (OPS-02 · OPS-03)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영 설정 64개를 화면에서 안전하게 바꾸고, 누가 언제 무엇을 바꿨는지 남긴다. SSH 로 `.env` 를 고치는 일을 없앤다.

**Architecture:** 편집 메타데이터(타입·선택지·범위)는 `MapsSettings.model_fields` 에서 파생한다. 검증은 `MapsSettings.model_validate` 에 위임한다. 값 반영은 `.env` 쓰기 + `lru_cache` 설정 객체 갱신이고, `CronTrigger` 에 구워지는 9개만 재시작 필요 배지를 단다.

**Tech Stack:** FastAPI · Pydantic v2 · SQLAlchemy · Alembic · Jinja2 · vanilla JS · pytest

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-13-ops-config-editing-design.md`
- 모든 함수·클래스에 타입 힌트와 docstring (루트 `CLAUDE.md` 규약)
- 설정 접근은 `get_settings()` 로만. `os.getenv` 직접 호출 금지
- **비밀 항목 15개는 감사 로그에 값을 남기지 않는다** (`***` 만)
- **허용목록은 `get_config_status()` 자체다.** 별도 목록을 만들지 않는다
- alembic revision id 는 **32자 이내** (varchar(32) — 8/1 배포 사고)
- 새 마이그레이션을 추가하면 `tests/test_migrations.py` 가 빈 SQLite 전체 업그레이드를 검사한다
- 커밋 메시지는 한국어 본문 + 영어 제목, 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## 파일 구조

| 파일 | 책임 |
|---|---|
| `maps/common/settings.py` | 편집 메타데이터 파생, 위험·재시작·시각 집합, 스케줄 시각 5종 노출 |
| `maps/common/models.py` | `OpsConfigLog` |
| `alembic/versions/0025_ops_config_log.py` | 테이블 생성 |
| `maps/api/schemas.py` | `OpsConfigField` 확장, `OpsConfigUpdate`/`OpsConfigUpdateResponse`/`OpsConfigLogItem` |
| `maps/api/ops_config.py` | `PUT /{env_var}`, `GET /history`, `POST /ai-scoring-mode` 삭제 |
| `static/js/app.js` | 편집 모달, 이력 탭, ai-scoring 호출부 전환 |
| `templates/ops_config.html` | 편집·이력 UI 컨테이너 |

---

### Task 1: 편집 메타데이터 파생 · 스케줄 시각 5종 노출

**Files:**
- Modify: `maps/common/settings.py:284-332` (`ConfigFieldStatus`, `_field`), `:347-` (`get_config_status` runtime 섹션)
- Modify: `maps/api/schemas.py:554-560` (`OpsConfigField`)
- Modify: `maps/api/ops_config.py:59-77` (필드 매핑)
- Test: `tests/test_ops_config_api.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces:
  - `ConfigFieldStatus` 에 `secret: bool`, `widget: str`, `choices: list[str]`, `minimum: float | None`, `maximum: float | None`, `dangerous: bool`, `requires_restart: bool`
  - `settings.DANGEROUS_ENV_VARS`, `settings.RESTART_REQUIRED_ENV_VARS`, `settings.TIME_ENV_VARS` (frozenset[str])
  - `widget` 값은 `"bool" | "enum" | "int" | "float" | "str"` 5종. Task 3·6 이 이 문자열을 쓴다

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_ops_config_api.py` 끝에 추가:

```python
def test_config_exposes_edit_metadata() -> None:
    """편집 위젯 정보가 pydantic 선언에서 파생된다."""
    client = TestClient(main.app)

    fields = {
        f["env_var"]: f
        for section in client.get("/api/v1/ops/config").json()["sections"]
        for f in section["fields"]
    }

    assert fields["MAPS_AI_SCORING_MODE"]["widget"] == "enum"
    assert fields["MAPS_AI_SCORING_MODE"]["choices"] == ["off", "rerank", "replace"]
    assert fields["MAPS_LIVE_TRADING_ENABLED"]["widget"] == "bool"
    assert fields["MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS"]["widget"] == "int"
    assert fields["MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS"]["minimum"] == 0
    assert fields["MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS"]["maximum"] == 60
    assert fields["MAPS_CANDIDATE_MIN_SCORE"]["widget"] == "float"
    assert fields["MAPS_LOG_LEVEL"]["widget"] == "str"


def test_config_marks_secret_dangerous_and_restart() -> None:
    """비밀·위험·재시작 플래그가 응답에 실린다."""
    client = TestClient(main.app)

    fields = {
        f["env_var"]: f
        for section in client.get("/api/v1/ops/config").json()["sections"]
        for f in section["fields"]
    }

    assert fields["KIS_APP_SECRET"]["secret"] is True
    assert fields["MAPS_LOG_LEVEL"]["secret"] is False
    assert fields["MAPS_DB_URL"]["dangerous"] is True
    assert fields["MAPS_LIVE_TRADING_ENABLED"]["dangerous"] is True
    assert fields["MAPS_LOG_LEVEL"]["dangerous"] is False
    assert fields["MAPS_CANDIDATE_TIME"]["requires_restart"] is True
    assert fields["MAPS_CANDIDATE_MIN_SCORE"]["requires_restart"] is False


def test_pipeline_schedule_times_are_exposed() -> None:
    """운영자가 가장 자주 조정하는 스케줄 시각 5종이 목록에 있어야 한다."""
    client = TestClient(main.app)

    names = {
        f["env_var"]
        for section in client.get("/api/v1/ops/config").json()["sections"]
        for f in section["fields"]
    }

    assert {
        "MAPS_DATA_COLLECTION_TIME",
        "MAPS_CANDIDATE_TIME",
        "MAPS_VALIDATION_TIME",
        "MAPS_ORDER_TIME",
        "MAPS_EOD_TIME",
    } <= names
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_ops_config_api.py::test_config_exposes_edit_metadata -v`
Expected: FAIL — `KeyError: 'widget'`

- [ ] **Step 3: 집합 3개와 파생 함수 추가**

`maps/common/settings.py` 상단 import 에 추가:

```python
import types
import typing
from typing import Literal
```

`ConfigFieldStatus` 정의 위에 추가:

```python
# 잘못 바꾸면 되돌리기 어려운 항목. 확인값을 요구한다.
# MAPS_DB_URL 은 화면설계서에 없지만 포함한다 — 잘못 넣으면 다음 기동에서 앱이 아예 안 뜨고
# 화면으로는 복구할 수 없다.
DANGEROUS_ENV_VARS: frozenset[str] = frozenset({
    "MAPS_LIVE_TRADING_ENABLED",
    "KIS_REAL_TRADING",
    "MAPS_BROKER_MODE",
    "MAPS_SCHEDULER_ENABLED",
    "MAPS_STRATEGY_TRADE_ENABLED",
    "MAPS_DB_URL",
})

# 값이 기동 시점에 구워져 캐시 객체를 바꿔도 반영되지 않는 항목.
RESTART_REQUIRED_ENV_VARS: frozenset[str] = frozenset({
    "MAPS_DATA_COLLECTION_TIME",
    "MAPS_CANDIDATE_TIME",
    "MAPS_VALIDATION_TIME",
    "MAPS_ORDER_TIME",
    "MAPS_EOD_TIME",
    "MAPS_STOCK_REPORT_TIME",
    "MAPS_SCHEDULER_TIMEZONE",
    "MAPS_DB_URL",
    "MAPS_LOG_DIR",
})

# pydantic 이 순수 str 로 두는 HH:MM 항목. 정규식을 따로 건다.
TIME_ENV_VARS: frozenset[str] = frozenset({
    "MAPS_DATA_COLLECTION_TIME",
    "MAPS_CANDIDATE_TIME",
    "MAPS_VALIDATION_TIME",
    "MAPS_ORDER_TIME",
    "MAPS_EOD_TIME",
    "MAPS_STOCK_REPORT_TIME",
})
```

`ConfigFieldStatus` 를 확장:

```python
@dataclass(frozen=True)
class ConfigFieldStatus:
    name: str
    env_var: str
    configured: bool
    required: bool
    value: str
    description: str
    secret: bool = False
    widget: str = "str"
    choices: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    dangerous: bool = False
    requires_restart: bool = False
```

`dataclasses.field` 를 import 해야 한다. 파일 상단이 `from dataclasses import dataclass` 라면
`from dataclasses import dataclass, field` 로 바꾼다. **`_field` 함수와 이름이 겹치지 않는다**
(하나는 모듈 함수 `_field`, 하나는 `dataclasses.field`).

파생 함수를 `_field` 위에 추가:

```python
def _unwrap_optional(annotation: object) -> object:
    """`X | None` 에서 X 를 꺼낸다. 아니면 그대로 돌려준다."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return args[0] if args else str
    return annotation


def _derive_widget(attr: str) -> tuple[str, list[str], float | None, float | None]:
    """pydantic 선언에서 (위젯, 선택지, 최소, 최대)를 뽑는다.

    Literal 은 선택지로, Field(ge=/le=/gt=/lt=) 는 범위로 바뀐다. 59행짜리 메타데이터 표를
    손으로 유지하지 않기 위한 지점이다 — 설정을 추가하면 화면이 자동으로 따라온다.
    """
    info = MapsSettings.model_fields[attr]
    annotation = _unwrap_optional(info.annotation)
    if typing.get_origin(annotation) is Literal:
        return "enum", [str(a) for a in typing.get_args(annotation)], None, None

    minimum: float | None = None
    maximum: float | None = None
    for meta in info.metadata or []:
        for lower in ("ge", "gt"):
            bound = getattr(meta, lower, None)
            if bound is not None:
                minimum = float(bound)
        for upper in ("le", "lt"):
            bound = getattr(meta, upper, None)
            if bound is not None:
                maximum = float(bound)

    if annotation is bool:
        return "bool", [], None, None
    if annotation is int:
        return "int", [], minimum, maximum
    if annotation is float:
        return "float", [], minimum, maximum
    return "str", [], None, None
```

`_field` 를 교체:

```python
def _field(
    settings: MapsSettings,
    attr: str,
    env_var: str,
    description: str,
    *,
    required: bool = False,
    secret: bool = False,
) -> ConfigFieldStatus:
    """설정 한 항목의 조회·편집 메타데이터를 만든다."""
    value = getattr(settings, attr)
    widget, choices, minimum, maximum = _derive_widget(attr)
    return ConfigFieldStatus(
        name=attr,
        env_var=env_var,
        configured=bool(value),
        required=required,
        value=mask_config_value(value, secret=secret),
        description=description,
        secret=secret,
        widget=widget,
        choices=choices,
        minimum=minimum,
        maximum=maximum,
        dangerous=env_var in DANGEROUS_ENV_VARS,
        requires_restart=env_var in RESTART_REQUIRED_ENV_VARS,
    )
```

- [ ] **Step 4: 스케줄 시각 5종 노출**

`get_config_status()` 의 `runtime` 섹션에서 `maps_stock_report_time` 줄 바로 위에 5줄 추가:

```python
                _field(s, "maps_data_collection_time", "MAPS_DATA_COLLECTION_TIME", "Daily OHLCV collection time (KST)"),
                _field(s, "maps_candidate_time", "MAPS_CANDIDATE_TIME", "Daily candidate generation time (KST)"),
                _field(s, "maps_validation_time", "MAPS_VALIDATION_TIME", "Daily validation run time (KST)"),
                _field(s, "maps_order_time", "MAPS_ORDER_TIME", "Morning order cycle time (KST)"),
                _field(s, "maps_eod_time", "MAPS_EOD_TIME", "End-of-day broker sync time (KST)"),
```

- [ ] **Step 5: 응답 스키마와 매핑 확장**

`maps/api/schemas.py` — `OpsConfigField` 를 교체:

```python
class OpsConfigField(BaseModel):
    name: str
    env_var: str
    configured: bool
    required: bool
    value: str
    description: str
    secret: bool = False
    widget: str = "str"
    choices: list[str] = []
    minimum: float | None = None
    maximum: float | None = None
    dangerous: bool = False
    requires_restart: bool = False
```

`maps/api/ops_config.py` — `get_ops_config` 안의 `OpsConfigField(...)` 생성부에 새 필드를 넘긴다:

```python
                OpsConfigField(
                    name=field.name,
                    env_var=field.env_var,
                    configured=field.configured,
                    required=field.required,
                    value=field.value,
                    description=field.description,
                    secret=field.secret,
                    widget=field.widget,
                    choices=field.choices,
                    minimum=field.minimum,
                    maximum=field.maximum,
                    dangerous=field.dangerous,
                    requires_restart=field.requires_restart,
                )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_ops_config_api.py -v`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add maps/common/settings.py maps/api/schemas.py maps/api/ops_config.py tests/test_ops_config_api.py
git commit -m "feat: derive ops config edit metadata from pydantic"
```

---

### Task 2: 감사 로그 테이블

**Files:**
- Modify: `maps/common/models.py` (파일 끝)
- Create: `alembic/versions/0025_ops_config_log.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: 없음
- Produces: `OpsConfigLog(env_var, old_value, new_value, changed_by, created_at)`. Task 3·5 가 쓴다

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_migrations.py` 에는 공용 헬퍼가 없다. 기존 테스트
`test_fresh_database_reaches_stock_analysis_history_schema` 가 `command.upgrade(...)` 를
직접 호출하는 방식을 쓴다. **그 테스트가 `revision == "0024_app_user"` 를 단언하고 있어
0025 를 추가하면 깨진다** — 새 테스트를 만들지 말고 기존 테스트를 확장한다.

29행의 단언을 교체:

```python
    assert revision == "0025_ops_config_log"
```

같은 테스트 끝(마지막 단언 뒤)에 추가:

```python
    ops_log_columns = {
        column["name"] for column in inspector.get_columns("ops_config_log")
    }
    assert {
        "env_var",
        "old_value",
        "new_value",
        "changed_by",
        "created_at",
    } <= ops_log_columns
```

`inspector` 는 이미 이 테스트에 있다. `engine.dispose()` 는 27행에서 이미 호출되므로
`inspector.get_columns` 는 그 앞줄에서 하거나, 기존 테스트가 하듯 `inspector` 를 계속
쓴다(SQLite 파일 기반이라 dispose 후에도 조회 가능하지만, 안전하게 `engine.dispose()`
직전으로 옮긴다).

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_migrations.py::test_fresh_database_reaches_ops_config_log_schema -v`
Expected: FAIL — `NoSuchTableError: ops_config_log`

- [ ] **Step 3: 모델 추가**

`maps/common/models.py` 끝에 추가:

```python
class OpsConfigLog(Base):
    """ops_config_log — 운영 설정 변경 감사 로그.

    비밀 항목의 값은 저장하지 않는다(`***`). 감사 로그는 누가 언제 무엇을 건드렸는지를
    남기는 곳이지 자격증명 사본을 만드는 곳이 아니다.
    """

    __tablename__ = "ops_config_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env_var: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    old_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
```

- [ ] **Step 4: 마이그레이션 작성**

`alembic/versions/0025_ops_config_log.py` 생성:

```python
"""add ops_config_log audit table

Revision ID: 0025_ops_config_log
Revises: 0024_app_user
Create Date: 2026-08-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_ops_config_log"
down_revision: Union[str, None] = "0024_app_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """운영 설정 변경 감사 로그 테이블을 만든다."""
    op.create_table(
        "ops_config_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("env_var", sa.String(64), nullable=False),
        sa.Column("old_value", sa.String(500), nullable=True),
        sa.Column("new_value", sa.String(500), nullable=True),
        sa.Column("changed_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ops_config_log_env_var", "ops_config_log", ["env_var"])


def downgrade() -> None:
    """감사 로그 테이블을 제거한다."""
    op.drop_index("ix_ops_config_log_env_var", table_name="ops_config_log")
    op.drop_table("ops_config_log")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_migrations.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add maps/common/models.py alembic/versions/0025_ops_config_log.py tests/test_migrations.py
git commit -m "feat: add ops config audit log table"
```

---

### Task 3: 설정 변경 엔드포인트

**Files:**
- Modify: `maps/api/ops_config.py`
- Modify: `maps/api/schemas.py` (요청·응답 모델 추가)
- Test: `tests/test_ops_config_edit.py` (신규)

**Interfaces:**
- Consumes: Task 1 의 `ConfigFieldStatus` 확장·집합 3개, Task 2 의 `OpsConfigLog`
- Produces: `PUT /api/v1/ops/config/{env_var}` — body `{value, confirm?}`, 응답 `{env_var, value, requires_restart, audit_error}`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_ops_config_edit.py` 생성:

```python
"""운영 설정 편집 엔드포인트 계약 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import maps.common.models  # noqa: F401
from maps.api import ops_config
from maps.common.db import Base
from maps.common.models import OpsConfigLog
from maps.common.settings import get_settings, reload_settings


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """빈 .env 와 인메모리 DB 를 붙인 TestClient."""
    from maps.api.deps import get_db

    env_file = tmp_path / ".env"
    env_file.write_text("MAPS_ENV=test\n", encoding="utf-8")
    monkeypatch.setattr(ops_config, "_ENV_FILE", env_file)
    reload_settings()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app), factory, env_file

    main.app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()
    reload_settings()


def test_unknown_env_var_is_rejected(ctx) -> None:
    """허용목록 밖 이름은 400 — get_config_status() 가 곧 허용목록이다."""
    client, _factory, _env = ctx

    response = client.put("/api/v1/ops/config/MAPS_NOT_A_REAL_SETTING", json={"value": "x"})

    assert response.status_code == 400


def test_enum_rejects_undefined_choice(ctx) -> None:
    client, _factory, _env = ctx

    response = client.put("/api/v1/ops/config/MAPS_AI_SCORING_MODE", json={"value": "bogus"})

    assert response.status_code == 400


def test_number_range_is_enforced(ctx) -> None:
    """Field(le=60) 위반을 pydantic 이 잡는다."""
    client, _factory, _env = ctx

    response = client.put(
        "/api/v1/ops/config/MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS", json={"value": 61}
    )

    assert response.status_code == 400


def test_time_format_is_enforced(ctx) -> None:
    """시각은 순수 str 이라 pydantic 이 못 잡는다 — 정규식이 잡아야 한다."""
    client, _factory, _env = ctx

    response = client.put("/api/v1/ops/config/MAPS_CANDIDATE_TIME", json={"value": "25:99"})

    assert response.status_code == 400


def test_dangerous_change_requires_matching_confirm(ctx) -> None:
    client, _factory, _env = ctx

    without = client.put("/api/v1/ops/config/MAPS_LIVE_TRADING_ENABLED", json={"value": False})
    assert without.status_code == 400

    with_confirm = client.put(
        "/api/v1/ops/config/MAPS_LIVE_TRADING_ENABLED",
        json={"value": False, "confirm": "MAPS_LIVE_TRADING_ENABLED"},
    )
    assert with_confirm.status_code == 200


def test_secret_value_is_not_stored_in_audit_log(ctx) -> None:
    """비밀 항목은 감사 로그에 값을 남기지 않는다."""
    client, factory, _env = ctx

    response = client.put("/api/v1/ops/config/KIS_APP_SECRET", json={"value": "super-secret-1234"})
    assert response.status_code == 200
    assert "super-secret-1234" not in response.text

    db = factory()
    try:
        row = db.query(OpsConfigLog).filter(OpsConfigLog.env_var == "KIS_APP_SECRET").one()
        assert row.new_value == "***"
        assert row.old_value == "***"
    finally:
        db.close()


def test_success_updates_env_file_and_cached_settings(ctx) -> None:
    client, _factory, env_file = ctx

    response = client.put("/api/v1/ops/config/MAPS_CANDIDATE_MIN_SCORE", json={"value": 33.5})

    assert response.status_code == 200
    assert "MAPS_CANDIDATE_MIN_SCORE=33.5" in env_file.read_text(encoding="utf-8")
    assert get_settings().maps_candidate_min_score == 33.5


def test_env_write_failure_leaves_cache_unchanged(ctx, monkeypatch) -> None:
    """파일 쓰기가 실패하면 메모리도 안 바뀐다 — 상태가 갈라지면 안 된다."""
    client, _factory, _env = ctx
    before = get_settings().maps_candidate_min_score

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ops_config, "_set_env_value", boom)
    response = client.put("/api/v1/ops/config/MAPS_CANDIDATE_MIN_SCORE", json={"value": 77.0})

    assert response.status_code == 500
    assert get_settings().maps_candidate_min_score == before


def test_restart_required_flag_is_returned(ctx) -> None:
    client, _factory, _env = ctx

    body = client.put("/api/v1/ops/config/MAPS_CANDIDATE_TIME", json={"value": "16:25"}).json()

    assert body["requires_restart"] is True


def test_audit_failure_returns_200_with_audit_error(ctx, monkeypatch) -> None:
    """감사 로그 실패로 500 을 내면 실패로 읽고 다시 눌러 같은 값을 두 번 쓴다.

    값 변경은 이미 반영됐으므로 200 + audit_error 로 알린다
    (api/stock_analysis.py 의 history_error 와 같은 패턴).
    """
    client, _factory, _env = ctx

    from maps.common import models

    def boom(self, *args, **kwargs):
        raise RuntimeError("audit table gone")

    monkeypatch.setattr(models.OpsConfigLog, "__init__", boom)
    response = client.put("/api/v1/ops/config/MAPS_CANDIDATE_MIN_SCORE", json={"value": 44.0})

    assert response.status_code == 200
    assert response.json()["audit_error"]
    assert get_settings().maps_candidate_min_score == 44.0
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_ops_config_edit.py -v`
Expected: 전부 FAIL — `PUT` 라우트가 없어 405

- [ ] **Step 3: 요청·응답 모델 추가**

`maps/api/schemas.py` 의 `OpsConfigResponse` 아래에 추가:

```python
class OpsConfigUpdate(BaseModel):
    """설정 한 항목 변경 요청. 위험 항목은 confirm 에 env_var 이름을 그대로 넣는다."""

    value: bool | int | float | str | None = None
    confirm: str | None = None


class OpsConfigUpdateResponse(BaseModel):
    env_var: str
    value: str                       # 비밀 항목은 마스킹된 값
    requires_restart: bool
    audit_error: str | None = None


class OpsConfigLogItem(BaseModel):
    id: int
    env_var: str
    old_value: str | None = None
    new_value: str | None = None
    changed_by: str | None = None
    created_at: str
```

- [ ] **Step 4: 엔드포인트 구현**

`maps/api/ops_config.py` — import 를 다음으로 확장:

```python
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from maps.api.auth import current_identity
from maps.api.deps import DbDep
from maps.api.schemas import (
    BrokerHealthResponse,
    OpsConfigField,
    OpsConfigLogItem,
    OpsConfigResponse,
    OpsConfigSection,
    OpsConfigUpdate,
    OpsConfigUpdateResponse,
)
from maps.common.models import OpsConfigLog
from maps.common.settings import (
    TIME_ENV_VARS,
    ConfigFieldStatus,
    MapsSettings,
    describe_trading_mode,
    get_config_status,
    get_missing_required_settings,
    get_settings,
    mask_config_value,
)
```

`_set_env_value` 아래에 헬퍼를 추가:

```python
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _editable_field(settings: MapsSettings, env_var: str) -> ConfigFieldStatus | None:
    """허용목록에서 항목을 찾는다. get_config_status() 가 곧 허용목록이다."""
    for section in get_config_status(settings):
        for field in section.fields:
            if field.env_var == env_var:
                return field
    return None


def _validated_value(settings: MapsSettings, field: ConfigFieldStatus, raw: object) -> object:
    """설정 모델 전체로 검증해 변환된 값을 돌려준다.

    타입·범위·열거값 검증을 pydantic 에 맡긴다. 시각만 순수 str 이라 정규식을 따로 본다.
    """
    if field.env_var in TIME_ENV_VARS and not _TIME_PATTERN.match(str(raw)):
        raise HTTPException(status_code=400, detail="시각은 HH:MM 형식이어야 합니다.")
    try:
        validated = MapsSettings.model_validate({**settings.model_dump(), field.name: raw})
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError 를 400 으로 바꾼다
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return getattr(validated, field.name)


def _dotenv_text(value: object) -> str:
    """dotenv 에 적을 문자열. 불리언은 소문자로 적는다(pydantic 이 읽는 형식)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)
```

`get_broker_health` 아래에 라우트를 추가:

```python
@router.put("/{env_var}", response_model=OpsConfigUpdateResponse)
def update_ops_config(
    env_var: str, body: OpsConfigUpdate, request: Request, db: Session = DbDep
) -> OpsConfigUpdateResponse:
    """설정 한 항목을 바꾼다.

    `.env` 를 먼저 쓰고 성공하면 캐시 객체를 갱신한다. 파일 쓰기가 실패하면 메모리도
    안 바뀌어 상태가 갈라지지 않는다.
    """
    settings = get_settings()
    field = _editable_field(settings, env_var)
    if field is None:
        raise HTTPException(status_code=400, detail=f"편집할 수 없는 설정입니다: {env_var}")
    if field.dangerous and body.confirm != env_var:
        raise HTTPException(
            status_code=400,
            detail=f"위험한 변경입니다. confirm 에 {env_var} 를 그대로 입력하세요.",
        )

    value = _validated_value(settings, field, body.value)
    previous = getattr(settings, field.name)

    _set_env_value(_ENV_FILE, env_var, _dotenv_text(value))
    setattr(settings, field.name, value)

    audit_error: str | None = None
    try:
        db.add(
            OpsConfigLog(
                env_var=env_var,
                old_value="***" if field.secret else _dotenv_text(previous),
                new_value="***" if field.secret else _dotenv_text(value),
                changed_by=current_identity(request).username,
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 값 변경은 이미 반영됐다. 500 이면 재시도로 중복 쓰기
        db.rollback()
        audit_error = str(exc)

    return OpsConfigUpdateResponse(
        env_var=env_var,
        value=mask_config_value(value, secret=field.secret),
        requires_restart=field.requires_restart,
        audit_error=audit_error,
    )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_ops_config_edit.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add maps/api/ops_config.py maps/api/schemas.py tests/test_ops_config_edit.py
git commit -m "feat: add ops config update endpoint with audit log"
```

---

### Task 4: `POST /ai-scoring-mode` 삭제

남겨 두면 `.env` 에 쓰는 경로가 둘이 되고 그중 하나만 감사 로그를 남긴다.

**Files:**
- Modify: `maps/api/ops_config.py:97-104` (라우트 삭제)
- Modify: `maps/api/schemas.py` (`AIScoringModeResponse`, `AIScoringModeUpdate` 삭제)
- Modify: `static/js/app.js:1327-1343` (`saveAIScoringMode`)
- Test: `tests/test_ops_config_edit.py`

**Interfaces:**
- Consumes: Task 3 의 `PUT /api/v1/ops/config/{env_var}`
- Produces: 없음

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_ops_config_edit.py` 끝에 추가:

```python
def test_ai_scoring_mode_endpoint_is_gone(ctx) -> None:
    """.env 에 쓰는 경로는 하나여야 한다 — 감사 로그를 우회하는 문을 남기지 않는다."""
    client, _factory, _env = ctx

    response = client.post("/api/v1/ops/config/ai-scoring-mode", json={"mode": "rerank"})

    assert response.status_code in (404, 405)


def test_ai_scoring_mode_changes_through_generic_endpoint(ctx) -> None:
    client, factory, _env = ctx

    response = client.put("/api/v1/ops/config/MAPS_AI_SCORING_MODE", json={"value": "rerank"})

    assert response.status_code == 200
    assert get_settings().maps_ai_scoring_mode == "rerank"

    db = factory()
    try:
        assert db.query(OpsConfigLog).filter(
            OpsConfigLog.env_var == "MAPS_AI_SCORING_MODE"
        ).count() == 1
    finally:
        db.close()
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_ops_config_edit.py::test_ai_scoring_mode_endpoint_is_gone -v`
Expected: FAIL — 200 이 돌아온다

- [ ] **Step 3: 라우트와 스키마 삭제**

`maps/api/ops_config.py` — `set_ai_scoring_mode` 함수 전체(97~104행)를 삭제하고, import 에서
`AIScoringModeResponse`, `AIScoringModeUpdate` 를 뺀다.

`maps/api/schemas.py` — `AIScoringModeResponse`, `AIScoringModeUpdate` 클래스를 삭제한다.
(`grep -rn "AIScoringMode" maps/ tests/` 로 남은 참조가 없는지 확인한다.)

- [ ] **Step 4: 화면 호출부 전환**

`static/js/app.js` — `saveAIScoringMode` 를 교체:

```javascript
async function saveAIScoringMode() {
  const select = document.getElementById('ai-scoring-mode');
  const button = document.getElementById('ai-scoring-save');
  const result = document.getElementById('ai-scoring-result');
  if (select.value === 'replace' && !confirm('기존 점수를 AI 점수로 대체할까요?')) return;
  button.disabled = true;
  result.textContent = '저장 중...';
  try {
    const data = await apiPut('/ops/config/MAPS_AI_SCORING_MODE', { value: select.value });
    result.textContent = `저장됨: ${data.value}`;
    await loadOpsConfig();
  } catch (e) {
    result.textContent = `오류: ${e.message}`;
  } finally {
    button.disabled = false;
  }
}
```

`apiPut` 헬퍼가 없으면 `apiPost` 옆에 추가한다:

```javascript
async function apiPut(path, body) {
  const res = await fetch(`/api/v1${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_ops_config_edit.py tests/test_ops_config_api.py -v`
Expected: 전부 PASS

Run: `node --check static/js/app.js`
Expected: 출력 없음

- [ ] **Step 6: 커밋**

```bash
git add maps/api/ops_config.py maps/api/schemas.py static/js/app.js tests/test_ops_config_edit.py
git commit -m "refactor: fold ai scoring mode into generic config endpoint"
```

---

### Task 5: 변경 이력 조회 (OPS-03)

**Files:**
- Modify: `maps/api/ops_config.py`
- Test: `tests/test_ops_config_edit.py`

**Interfaces:**
- Consumes: Task 2 의 `OpsConfigLog`, Task 3 의 `OpsConfigLogItem`
- Produces: `GET /api/v1/ops/config/history?limit=` → `list[OpsConfigLogItem]` 최신순

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_history_returns_newest_first(ctx) -> None:
    client, _factory, _env = ctx
    client.put("/api/v1/ops/config/MAPS_CANDIDATE_MIN_SCORE", json={"value": 11.0})
    client.put("/api/v1/ops/config/MAPS_CANDIDATE_MIN_SCORE", json={"value": 22.0})

    rows = client.get("/api/v1/ops/config/history").json()

    assert [r["new_value"] for r in rows[:2]] == ["22.0", "11.0"]
    assert rows[0]["env_var"] == "MAPS_CANDIDATE_MIN_SCORE"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_ops_config_edit.py::test_history_returns_newest_first -v`
Expected: FAIL — `/history` 가 `{env_var}` 로 잡히거나 404

- [ ] **Step 3: 라우트 추가**

`maps/api/ops_config.py` — **`@router.put("/{env_var}")` 보다 위에** 추가한다. 경로 변수
라우트가 먼저 등록되면 `/history` 를 `env_var="history"` 로 삼켜 버린다:

```python
@router.get("/history", response_model=list[OpsConfigLogItem])
def get_ops_config_history(limit: int = 100, db: Session = DbDep) -> list[OpsConfigLogItem]:
    """설정 변경 이력을 최신순으로 반환한다."""
    rows = (
        db.query(OpsConfigLog)
        .order_by(OpsConfigLog.id.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [
        OpsConfigLogItem(
            id=row.id,
            env_var=row.env_var,
            old_value=row.old_value,
            new_value=row.new_value,
            changed_by=row.changed_by,
            created_at=row.created_at.replace(tzinfo=datetime.timezone.utc).isoformat(),
        )
        for row in rows
    ]
```

`import datetime` 을 파일 상단에 추가한다. DB 의 UTC naive 시각을 명시적 UTC 로 보정하지
않으면 브라우저 KST 표시가 9시간 어긋난다(2026-08-12 실제 발생).

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_ops_config_edit.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add maps/api/ops_config.py tests/test_ops_config_edit.py
git commit -m "feat: add ops config change history endpoint"
```

---

### Task 6: 편집 UI (OPS-02) 와 이력 탭 (OPS-03)

**Files:**
- Modify: `templates/ops_config.html`
- Modify: `static/js/app.js:1290-1325` (`loadOpsConfig`)
- Test: `tests/test_ops_config_ui.py` (신규)

**Interfaces:**
- Consumes: Task 1 의 필드 메타데이터, Task 3·5 의 엔드포인트
- Produces: 없음 (마지막 기능 작업)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_ops_config_ui.py` 생성:

```python
"""운영 설정 편집 화면 산출물 검사."""

from __future__ import annotations

from pathlib import Path


def test_ops_config_screen_has_edit_and_history() -> None:
    """편집 버튼·모달과 이력 영역이 화면에 있어야 한다."""
    js = Path("static/js/app.js").read_text(encoding="utf-8")
    html = Path("templates/ops_config.html").read_text(encoding="utf-8")

    assert "ops-edit-modal" in html
    assert "ops-history-area" in html
    assert "openOpsEdit" in js
    assert "loadOpsHistory" in js


def test_ops_edit_requires_confirm_for_dangerous() -> None:
    """위험 항목은 확인 입력 없이 저장 버튼이 열리면 안 된다."""
    js = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "dangerous" in js
    assert "confirm:" in js
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_ops_config_ui.py -v`
Expected: FAIL — `ops-edit-modal` 이 없다

- [ ] **Step 3: 템플릿에 모달·이력 컨테이너 추가**

`templates/ops_config.html` 의 마지막 `{% endblock %}` 앞에 추가:

```html
<section class="card">
  <div class="section-header"><span class="section-title">변경 이력</span><hr></div>
  <div id="ops-history-area" class="text-muted">불러오는 중…</div>
</section>

<div id="ops-edit-modal" class="modal" hidden>
  <div class="modal-body">
    <h3 id="ops-edit-title">설정 변경</h3>
    <p id="ops-edit-desc" class="muted"></p>
    <div id="ops-edit-input"></div>
    <div id="ops-edit-danger" hidden>
      <p class="alert-msg">위험한 변경입니다. 아래에 <b id="ops-edit-confirm-name"></b> 를 그대로 입력하세요.</p>
      <input id="ops-edit-confirm" type="text" autocomplete="off">
    </div>
    <p>
      <button class="topbar-btn" id="ops-edit-save">변경</button>
      <button class="topbar-btn" id="ops-edit-cancel">취소</button>
      <span id="ops-edit-result" class="muted"></span>
    </p>
  </div>
</div>
```

- [ ] **Step 4: 편집·이력 스크립트 추가**

`static/js/app.js` — `loadOpsConfig` 의 `rows` 매핑에 편집 버튼 열을 추가한다. 기존
`<td class="mono">${f.value || '-'}</td>` 뒤에 한 칸 더 넣는다:

```javascript
          <td class="mono">${f.value || '-'}</td>
          <td>${f.requires_restart ? badge('restart', 'warn') : ''}
              <button class="topbar-btn" onclick='openOpsEdit(${JSON.stringify(f)})'>변경</button></td>
```

표 헤더에도 열을 하나 더한다:

```javascript
<table><thead><tr><th>ENV</th><th>Description</th><th>Required</th><th>Status</th><th>Current</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
```

`loadOpsConfig` 끝(`document.getElementById('ops-config-area').innerHTML = ...` 다음)에
이력 로드를 붙인다:

```javascript
    await loadOpsHistory();
```

파일에 함수 3개를 추가한다:

```javascript
let opsEditField = null;

function openOpsEdit(field) {
  opsEditField = field;
  document.getElementById('ops-edit-title').textContent = field.env_var;
  document.getElementById('ops-edit-desc').textContent = field.description;
  document.getElementById('ops-edit-result').textContent = '';

  let input;
  if (field.widget === 'bool') {
    input = `<select id="ops-edit-value"><option value="true">true</option><option value="false">false</option></select>`;
  } else if (field.widget === 'enum') {
    input = `<select id="ops-edit-value">${field.choices.map(c => `<option value="${c}">${c}</option>`).join('')}</select>`;
  } else if (field.widget === 'int' || field.widget === 'float') {
    const step = field.widget === 'int' ? '1' : 'any';
    const min = field.minimum != null ? `min="${field.minimum}"` : '';
    const max = field.maximum != null ? `max="${field.maximum}"` : '';
    input = `<input id="ops-edit-value" type="number" step="${step}" ${min} ${max}>`;
  } else {
    input = `<input id="ops-edit-value" type="text" placeholder="${field.secret ? '새 값 (재열람 불가)' : ''}">`;
  }
  document.getElementById('ops-edit-input').innerHTML = input;

  const danger = document.getElementById('ops-edit-danger');
  danger.hidden = !field.dangerous;
  document.getElementById('ops-edit-confirm-name').textContent = field.env_var;
  document.getElementById('ops-edit-confirm').value = '';
  document.getElementById('ops-edit-modal').hidden = false;
}

async function saveOpsEdit() {
  if (!opsEditField) return;
  const raw = document.getElementById('ops-edit-value').value;
  const result = document.getElementById('ops-edit-result');
  let value = raw;
  if (opsEditField.widget === 'bool') value = raw === 'true';
  else if (opsEditField.widget === 'int') value = parseInt(raw, 10);
  else if (opsEditField.widget === 'float') value = parseFloat(raw);

  const payload = { value };
  if (opsEditField.dangerous) payload.confirm = document.getElementById('ops-edit-confirm').value;

  result.textContent = '저장 중...';
  try {
    const data = await apiPut(`/ops/config/${opsEditField.env_var}`, payload);
    result.textContent = data.requires_restart
      ? `저장됨: ${data.value} — 반영하려면 서비스 재시작이 필요합니다`
      : `저장됨: ${data.value}`;
    if (data.audit_error) result.textContent += ` (감사 로그 실패: ${data.audit_error})`;
    document.getElementById('ops-edit-modal').hidden = true;
    await loadOpsConfig();
  } catch (e) {
    result.textContent = `오류: ${e.message}`;
  }
}

async function loadOpsHistory() {
  try {
    const rows = await apiFetch('/ops/config/history');
    if (!rows.length) { empty('ops-history-area', '변경 이력 없음'); return; }
    document.getElementById('ops-history-area').innerHTML = `
      <table><thead><tr><th>시각</th><th>ENV</th><th>이전</th><th>이후</th><th>변경자</th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td class="mono">${fmt.date(r.created_at)}</td>
        <td class="mono">${r.env_var}</td>
        <td class="mono">${r.old_value ?? '-'}</td>
        <td class="mono">${r.new_value ?? '-'}</td>
        <td>${r.changed_by ?? '-'}</td>
      </tr>`).join('')}</tbody></table>`;
  } catch (e) {
    empty('ops-history-area', `오류: ${e.message}`);
  }
}
```

모달 버튼 두 개를 기존 이벤트 바인딩 자리에 연결한다(`ai-scoring-save` 바인딩 옆):

```javascript
  document.getElementById('ops-edit-save')?.addEventListener('click', saveOpsEdit);
  document.getElementById('ops-edit-cancel')?.addEventListener('click', () => {
    document.getElementById('ops-edit-modal').hidden = true;
  });
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_ops_config_ui.py -v`
Expected: PASS

Run: `node --check static/js/app.js`
Expected: 출력 없음

- [ ] **Step 6: 커밋**

```bash
git add templates/ops_config.html static/js/app.js tests/test_ops_config_ui.py
git commit -m "feat: add ops config edit modal and history view"
```

---

### Task 7: 화면설계서·문서 정합

구현이 설계서와 3곳에서 달라졌다. 문서를 코드에 맞춘다 — `CLAUDE.md` 규약대로 **코드가 정본**이다.

**Files:**
- Modify: `docs/ui-design/maps-auth-screen-design.html` (OPS 절)
- Modify: `tests/test_auth_screen_design_doc.py`
- Modify: `maps/api/CLAUDE.md`, `maps/common/CLAUDE.md`, `alembic/CLAUDE.md`

**Interfaces:**
- Consumes: Task 1~6 전부
- Produces: 없음

- [ ] **Step 1: 실패 확인 (기존 테스트가 깨져 있어야 한다)**

Run: `python -m pytest tests/test_auth_screen_design_doc.py -v`
Expected: FAIL — 항목 수가 59 에서 64 로 늘었다

- [ ] **Step 2: 설계서 갱신**

`docs/ui-design/maps-auth-screen-design.html` 에서 3곳을 고친다:

1. `59개 허용목록으로 고정` → `64개 허용목록으로 고정`
2. **섹션별 항목 수 표를 고친다.** `test_config_sections_and_counts_are_documented` 가
   `<td class="num">{개수}</td>` 를 섹션마다 대조한다. `runtime` 이 21 → **26** 으로 바뀌고
   합계가 59 → **64** 가 된다. 나머지 5개 섹션 수치는 그대로다
3. 위험 항목 `5개` → `6개`, 목록에 `MAPS_DB_URL` 추가 (사유: 잘못 넣으면 다음 기동에서 앱이 뜨지 않고 화면으로 복구할 수 없다)
4. 확인 문구 목업 `LIVE 를 입력하세요` → `MAPS_LIVE_TRADING_ENABLED 를 그대로 입력하세요`
5. OPS-02·OPS-03 의 `서버 미구현` 배지를 `구현됨` 으로 바꾸고, 문서 머리말의 `구현됨 11 · 서버 미구현 7` 을 `구현됨 13 · 서버 미구현 5` 로 고친다

`test_secret_fields_are_documented_with_reread_rule` 은 비밀 항목 수(15)를 대조한다.
이번 작업에서 비밀 항목이 늘지 않으므로 그대로 둔다.

- [ ] **Step 3: 문서 테스트 갱신**

`tests/test_auth_screen_design_doc.py` — `UNBUILT_IDS` 에서 `"OPS-02", "OPS-03"` 을 뺀다:

```python
UNBUILT_IDS = ("NAV-03", "PAY-01", "PAY-02", "PAY-03", "PAY-04")
```

`IMPLEMENTED_IDS`(27행 근처의 `"OPS-01", "OPS-02", "OPS-03"` 목록)는 이미 세 개를 다
담고 있으므로 손대지 않는다.

- [ ] **Step 4: 패키지 문서 갱신**

- `maps/api/CLAUDE.md` — `ops_config.py` 설명에 `PUT /{env_var}` · `GET /history` 추가,
  `POST /ai-scoring-mode` 삭제 사실 기재
- `maps/common/CLAUDE.md` — `models.py` 표에 `ops_config_log` 행 추가,
  `settings.py` 에 `DANGEROUS_ENV_VARS` / `RESTART_REQUIRED_ENV_VARS` / `TIME_ENV_VARS` 기재
- `alembic/CLAUDE.md` — `현재 head: 0025_ops_config_log` 로 갱신

- [ ] **Step 5: 전체 스위트 확인**

Run: `python -m pytest --tb=short -q`
Expected: 전부 PASS

Run: `python -m pytest maps/tests -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add docs/ui-design/maps-auth-screen-design.html tests/test_auth_screen_design_doc.py maps/api/CLAUDE.md maps/common/CLAUDE.md alembic/CLAUDE.md
git commit -m "docs: mark ops config editing as built"
```

---

## 배포

마이그레이션 `0025_ops_config_log` 가 있다.

1. 운영 PostgreSQL custom-format 전체 백업 (`order_log_backup_20260724` 제외)
2. `git pull origin master`
3. `alembic upgrade head`
4. `sudo systemctl restart maps`
5. `systemctl is-active maps`, 내·외부 `/health` 200 확인

**16:00~16:45 KST 배포 금지** (analyze cron). 배포 전 `flock -n /tmp/maps_analyze.lock true` 로 확인한다.

배포 후 확인: `/ops-config` 에서 안전한 항목 하나(`MAPS_CANDIDATE_MIN_SCORE`)를 바꿔 보고
이력에 1행이 남는지, `.env` 가 갱신됐는지 본다. 위험 항목은 건드리지 않는다.
