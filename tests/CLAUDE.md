# tests/

`pytest` 스위트. 파일은 대체로 테스트 대상 모듈과 1:1로 대응한다.

## 실행

```powershell
pytest                                    # 전체
pytest tests/test_walk_forward.py         # 파일 하나
pytest tests/test_walk_forward.py::test_x -v
pytest --tb=short                         # 배포 전 표준
```

`pyproject.toml` 설정: `testpaths = ["tests"]`, `asyncio_mode = "auto"`
(async 테스트에 `@pytest.mark.asyncio` 를 붙이지 않는다).

## ⚠️ 테스트가 두 군데 있다

| 위치 | 개수 | 비고 |
|---|---|---|
| `tests/` | 90여 개 | `pytest` 기본 대상 (`testpaths`) |
| `maps/tests/` | 8개 | **`testpaths` 밖이다** — 명시적으로 경로를 줘야 돌아간다 |

`maps/tests/` 에는 `test_market_regime.py`, `test_strategy_scoring.py`,
`test_valuation_margin.py`, `test_sector_selector.py`, `test_fundamental_repo.py`,
`test_naver_fundamental.py`, `test_kostolany_steps6_12.py` 와 **자체 `conftest.py`** 가 있다.
시장·점수 쪽을 고쳤다면 `pytest maps/tests -q` 도 함께 돌린다.

## conftest.py

| fixture | 범위 | 설명 |
|---|---|---|
| `_auth_disabled_by_default` | **autouse** | 인증을 끈다. 운영 `.env` 가 인증을 켜도 스위트가 깨지지 않는다 |
| `db` | function | 인메모리 SQLite 세션. 테스트 간 상태 공유 없음 |

## 규칙

- 파일 `test_<기능>.py`, 함수 `test_<기대_동작>`
- 버그 수정에는 회귀 테스트를 함께 넣는다
- **실 브로커 자격증명·네트워크를 요구하지 않는다.** KIS·Bedrock·pykrx 는 mock 또는 override
- 점수·승격 게이트·거래 규칙·주문 상태 전이는 경계값을 덮는다
- 마이그레이션을 추가하면 `tests/test_migrations.py` 가 빈 DB 전체 업그레이드를 검사한다
- 새 전략을 추가하면 `tests/test_strategy_catalog.py` 가 카탈로그 등록을 강제한다
- 문서 지도가 코드와 어긋나면 `tests/test_docs_index.py` 가 실패한다

> ⚠️ Alembic `fileConfig` 가 기존 로거를 비활성화해 **테스트 순서 의존성**이 생긴 적이 있다
> (2026-08-11). 마이그레이션 관련 테스트를 만질 때는 전체 스위트로 확인한다.
