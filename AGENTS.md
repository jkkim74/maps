# 저장소 기여 가이드

## 프로젝트 구조 및 모듈 구성

MAPS는 검증을 우선하는 한국 주식 트레이딩 플랫폼입니다. `main.py`가 FastAPI 애플리케이션을 실행합니다. 백엔드 패키지는 `maps/`에 있습니다. `api/`는 라우터, `common/`은 설정과 데이터베이스 모델을 관리하며, `strategy/`, `backtest/`, `validation/`, `execution/`, `risk/`, `ops/`는 매매 흐름을 구현합니다.

웹 템플릿은 `maps/templates/`, 브라우저 정적 파일은 `static/`, React/TypeScript 기반 Capacitor 클라이언트는 `apps/mobile/`에 있습니다. 데이터베이스 마이그레이션은 `alembic/versions/`, 운영 유틸리티는 `scripts/`에 있습니다. 코드를 탐색할 때는 `index.md` → 해당 패키지의 `CLAUDE.md` → 소스 순서로 읽으세요. 코드 변경 시 관련 문서도 갱신하세요.

## 빌드·테스트·개발 명령어

Python 3.11 이상과 가상환경을 사용하세요. 백엔드 명령어는 저장소 루트에서 실행합니다.

- `python -m pip install -r requirements.txt`: 백엔드 및 테스트 의존성을 설치합니다.
- `Copy-Item .env.example .env`: 로컬 설정 파일을 생성합니다. 실행 전에 설정값을 수정하세요.
- `python -m uvicorn main:app --reload`: 8000번 포트에서 API와 대시보드를 실행합니다.
- `alembic upgrade head`: 아직 적용하지 않은 데이터베이스 마이그레이션을 적용합니다.
- `python -m pytest --tb=short`: 기본 백엔드 테스트를 실행합니다.

모바일 작업은 `apps/mobile/`에서 `npm install`로 의존성을 설치한 뒤 진행합니다. `npm run dev`는 개발 서버 실행, `npm run build`는 TypeScript 검사와 프로덕션 빌드, `npm test`는 Vitest 실행, `npm run cap:sync`는 빌드와 네이티브 프로젝트 동기화에 사용합니다.

## 코딩 스타일 및 이름 규칙

Python 들여쓰기는 공백 4칸을 사용합니다. 함수와 모듈은 `snake_case`, 클래스는 `PascalCase`로 작성하세요.
타입 힌트와 함수·클래스의 독스트링을 추가하세요.
예외는 `maps/common/exceptions.py`에 정의된 것을 사용하고, 기능 모듈에서 환경변수를 직접 읽지 말고 `maps.common.settings.get_settings()`로 설정을 불러오세요.
TypeScript/React 코드는 주변 코드의 형식을 따르세요.
프로젝트 설정에는 별도의 포매터나 린트 명령어가 정의되어 있지 않습니다.
불필요한 대규모 리팩터링을 하지 않는다.
요청 범위를 벗어난 코드를 수정하지 않는다.
null 가능성이 있는 데이터는 명시적으로 처리한다.
예외를 무시하거나 빈 catch 블록을 만들지 않는다.

## Working Style
작업 시작 전 관련 코드를 먼저 조사한다.
작은 수정:
1. 관련 코드 확인
2. 수정
3. 테스트
4. 변경 내용 요약
복잡한 기능 또는 대규모 리팩터링:
1. 현재 구조 조사
2. 구현 계획 작성
3. 영향 범위 확인
4. 구현
5. 테스트
6. 최종 diff 검토

## Completion Criteria
작업 완료 전 다음을 확인한다.
- 요구사항 구현 여부
- 컴파일 오류 여부
- 관련 테스트 실행 여부
- 기존 기능 회귀 가능성
- 불필요한 변경 포함 여부
- 보안 정보 포함 여부
완료 보고에는 다음을 포함한다.
1. 변경 내용
2. 변경 파일
3. 테스트 결과
4. 남아 있는 위험 또는 TODO

## Execution Plans

For complex features or significant refactors,
create and maintain an ExecPlan following `.agent/PLANS.md`.

Use an ExecPlan when the task:
- touches multiple modules
- changes architecture
- changes database schema
- requires migration
- is expected to span multiple Codex sessions

## 테스트 지침

백엔드는 pytest와 pytest-asyncio를 사용하며 asyncio 모드는 `auto`입니다. 테스트 파일은 `test_*.py`, 함수는 `test_*`로 작성하세요. 기본 테스트 탐색 경로는 `tests/`이며, 추가 패키지 테스트는 `python -m pytest maps/tests`로 실행합니다. 모바일 테스트 파일은 `*.test.ts`, `*.test.tsx`를 사용합니다. 변경된 동작에 대한 회귀 테스트를 추가하고, 격리된 데이터베이스와 모의 외부 서비스를 사용하세요. 수치 기반 커버리지 기준은 설정되어 있지 않습니다.

## 커밋 및 풀 리퀘스트 지침

기존 커밋은 `feat:`, `fix:`, `docs:` 등의 접두사와 간결한 명령형 제목을 사용합니다. PR에는 동작 변경 내용, 관련 이슈 링크, 검증 명령어와 결과를 작성하세요. UI 변경에는 스크린샷을 포함하고, 스키마 마이그레이션과 설정 변경 사항을 명시하세요.

## 보안 및 매매 안전 수칙

`.env`, 인증정보, 서명 키, 계좌 데이터를 커밋하지 마세요. 개발 시 `MAPS_BROKER_MODE=mock`을 사용하고 실거래를 비활성화하세요. 검증 실패나 정보 부족 시 신규 진입과 전략 승격을 차단하는 안전장치, 기준일 이후 데이터를 사용하지 않는 원칙, 감사 기록을 유지하세요. 기존 보유 포지션을 청산하려면 사용자의 명시적인 승인이 필요합니다.
