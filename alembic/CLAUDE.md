# alembic/

DB 스키마 마이그레이션. `alembic/versions/` 에 리비전 33개가 있고 **head 는 하나**다.

## 명령

```powershell
alembic current                                  # 현재 리비전
alembic upgrade head                             # 전체 적용
alembic revision --autogenerate -m "description" # 새 리비전
alembic downgrade -1                             # 한 단계 되돌리기
```

## 파일명이 두 가지다

| 형태 | 예 | 비고 |
|---|---|---|
| 번호식 | `0023_score_readiness_feeds.py` | 손으로 만든 주류 형식 |
| 해시식 | `c8a1f2b3d4e5_add_security_fundamental.py` | `--autogenerate` 가 만든 것 |

**둘은 한 줄로 이어져 있다** — 파일명 정렬 순서가 곧 적용 순서가 아니다. 순서를 알고 싶으면
파일명이 아니라 각 파일의 `down_revision` 을 따라간다. 예: `efca8676041a` 의
`down_revision` 은 `0005_stock_report_runs` 다.

현재 head: **`0028_holding_regime_audit`**.

## 규칙

- `Base.metadata.create_all()` 이 기동 시 테이블을 만들지만, **스키마 변경은 반드시
  Alembic 을 거친다.** create_all 이 먼저 만들어 버린 신규 테이블이 있으면 0행을 확인하고
  제거한 뒤 Alembic 으로 다시 만든다(2026-08-12 배포에서 실제로 필요했다)
- 감사 로그 4종(`promotion_history`, `universe_quality_log`, `order_log`, `kill_switch_log`)은
  day 1부터 존재해야 한다
- 마이그레이션에 임의 backfill 을 넣지 않는다. 데이터 이전은 `scripts/` 의 별도 스크립트로 한다
- 새 리비전을 추가하면 `tests/test_migrations.py` 가 빈 SQLite 에서 전체 업그레이드를 검사한다

> 🔴 **배포에 마이그레이션이 있으면 `alembic upgrade head` 를 반드시 포함한다.**
> 루트 CLAUDE.md 의 원라이너 배포에는 빠져 있다 — 빠뜨리면 새 컬럼 없이 기동해 런타임에서
> 깨진다. 운영은 PostgreSQL 이므로 적용 전에 custom-format 전체 백업을 만든다.

> ⚠️ `env.py` 의 `fileConfig` 는 `disable_existing_loggers=False` 로 둔다. 기본값이면
> 앱 로거를 죽여 테스트 순서 의존성이 생긴다(2026-08-11 실제 발생).
