# 상한가 V1 모의계좌 automatic 영구 전환 설계

## 목표

운영 서버의 상한가 V1 엔진을 KIS 모의계좌에서 `recommend_only`에서
`automatic`으로 즉시 영구 전환한다. 실계좌 설정과 애플리케이션 코드는 변경하지 않는다.

## 현재 상태와 전제

- 운영 서버는 `/opt/maps`, systemd 서비스는 `maps`다.
- `MAPS_BROKER_MODE=kis`, `KIS_REAL_TRADING=false`다.
- `MAPS_LIVE_TRADING_ENABLED=true`, `MAPS_LIMIT_UP_ENABLED=true`다.
- `automatic_mode_blocked_reason()` 결과는 `None`이다.
- 전환은 장중 즉시 수행하며, 재시작 후부터 모의계좌 주문이 자동 제출될 수 있다.
- 시간외 주문 실검증과 상한가 V1 전용 승격 기준은 아직 미완이다. 이번 전환은 이 제한을
  인지한 사용자의 명시적 승인에 따른다.

## 선택한 방식

`/opt/maps/.env`의 `MAPS_LIMIT_UP_MODE` 한 줄을 `automatic`으로 변경하고 `maps` 서비스를
재시작한다. API의 임시 모드 변경은 사용하지 않는다. 이 방식은 재시작 후에도 설정이
유지되며, 설정 정본이 `.env` 한 곳에 남는다.

대안은 API로만 임시 전환하거나 API 전환 후 장 마감에 영구화하는 방식이다. 전자는
영속성 요구를 충족하지 않고, 후자는 런타임과 설정 파일의 상태가 잠시 달라져 운영 판단을
복잡하게 하므로 채택하지 않는다.

## 실행 절차

1. 서버 시각, 서비스 상태, 현재 HEAD, 상한가 일일 가드와 열린 세션을 읽기 전용으로
   재확인한다.
2. `.env`에 `MAPS_LIMIT_UP_MODE=recommend_only`가 정확히 한 줄 존재하는지 확인한다.
3. `.env`를 타임스탬프가 포함된 파일로 백업한다.
4. 다른 설정을 건드리지 않고 해당 한 줄만 `MAPS_LIMIT_UP_MODE=automatic`으로 바꾼다.
5. `sudo systemctl restart maps`를 실행한다.
6. 서비스가 `active/running`인지, 내부 `/health`가 200인지, 기동 로그가
   `상한가 V1 기동: mode=automatic`인지 확인한다.
7. 실계좌가 아닌 모의계좌 설정이 유지됐는지, 안전 게이트가 통과했는지, 수동 잠금이나
   비상정지가 없는지 확인한다.
8. 재시작 직후 오류, 웹소켓 복구, KIS 호출 제한, `limit_up_v1%` 주문 로그를 관찰한다.

## 실패와 롤백

다음 중 하나라도 발생하면 새 설정을 유지하지 않는다.

- 서비스 기동 실패 또는 health 비정상
- `automatic` 안전 게이트 차단
- `mode=automatic` 기동 로그 부재
- 상한가 엔진이 수동 잠금 또는 비상정지 상태로 기동

이 경우 백업한 `.env`를 복원하고 서비스를 다시 시작한 뒤
`mode=recommend_only`, health 200, 서비스 `active/running`을 확인한다. 원인 확인 전에는
두 번째 automatic 전환을 시도하지 않는다.

## 성공 기준

- `.env`의 영구 설정이 `MAPS_LIMIT_UP_MODE=automatic`이다.
- `KIS_REAL_TRADING=false`가 유지된다.
- 서비스가 재시작 후 `active/running`이며 내부 health가 200이다.
- 기동 로그가 상한가 V1 `mode=automatic`을 명시한다.
- 안전 게이트 차단, 제어 루프 실패, deadman 실패가 없다.
- 변경한 운영 파일은 `.env` 하나뿐이며 백업 경로가 기록된다.
