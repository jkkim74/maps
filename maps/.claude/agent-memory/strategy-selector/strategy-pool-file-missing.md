---
name: strategy-pool-file-missing
description: config/strategy_pool.yaml 파일이 레포에 존재하지 않음 — strategy-selector 에이전트의 핵심 데이터 소스 누락
metadata:
  type: project
---

`config/strategy_pool.yaml` 파일이 D:\workspace2\maps\maps\maps 저장소 어디에도 존재하지 않음 (2026-06-23 Glob + 직접 경로 탐색으로 확인).

**Why:** 이 파일은 strategy-selector 에이전트가 각 전략의 regime_fit, historical_mdd, win_rate 등 메타데이터를 읽는 유일한 데이터 소스다. 파일이 없으면 전략 선정이 불가능하다.

**How to apply:** 파일이 없으면 시스템 프롬프트 규칙에 따라 selected_strategies: [], cash_ratio: 1.0 을 반환해야 한다. 절대 추측으로 가짜 전략을 생성하지 말 것.

파일이 생성된다면 예상 경로: `D:\workspace2\maps\maps\maps\config\strategy_pool.yaml`

관련: [[strategy-pool-schema]] (아직 작성 전 — 파일이 생기면 스키마 기록 필요)
