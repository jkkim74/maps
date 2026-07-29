# HANDOFF

> 작성일: 2026-07-29 (수, KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제 ①: **KRX 로그인 회로차단기** — 7/27 계정 잠금의 재발 방지. 구현·배포 완료.
> 주제 ②: **손절가 규칙 통일** — 경로마다 달랐다. 사이징이 포지션을 2배로 잡고 있었다.
> 주제 ③: **전략관리 화면** — 식별자만 뜨던 화면에 설명·가이드·복사 버튼 추가.
> 주제 ④: **도메인 이전** `magable.kr` → `maps.magable.kr` — 완료. DNS·인증서는 사용자 작업,
>          웹훅·모바일 재빌드·`ads.txt`·HSTS 는 이 세션에서 처리.
> 이전 핸드오프(UTC/KST 경계·블로그 자동화·KRX 계정 잠금, 7/27): git `9f17af9` 참고.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`.
**URL 이 바뀌었다 → `https://maps.magable.kr`** (구 `magable.kr` 은 이제 남의 서버다. 주제 ④)
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.

**중요(계속 유효)**: 운영 DB `TimeZone=Etc/UTC`인데 서버 OS는 KST다. `order_log.created_at`은
**UTC naive 저장** — 08:55 KST 주문은 `2026-07-28 23:55:15`처럼 전날로 찍힌다. psql로
"오늘 주문"을 `WHERE created_at >= '오늘'`로 조회하면 **0 rows가 나온다**.
`ORDER BY id DESC LIMIT n`이 안전하고, 코드에서는 `order_manager.kst_day_bounds_utc()`를 쓸 것.

오늘 커밋 7개: `1b98004` `29eae95` `fb6a789` `7bd1514` `6d755cd` `d36a08f` `0fd872a`.
서버 배포는 `6d755cd` 까지(이후는 문서·모바일이라 서버 반영이 불필요). 테스트 **513 passed** + 모바일 **18 passed**.

---

# 주제 ① KRX 로그인 회로차단기 (커밋 `1b98004`, 배포 11:06)

7/27 핸드오프의 🔴 재발 방지 항목. **해소됨.**

## 원인 — 무한 재시도의 위치

pykrx `webio.py` 는 요청마다 `get_auth_session()` 을 부르고, 세션이 없으면 **그 자리에서
재로그인**한다(`auth.py:217`). 자격증명이 만료(CD010)되면 이 재시도가 매 요청마다 돌고,
누적 실패가 계정을 잠근다(CD007). 잠긴 뒤에는 올바른 비밀번호도 CD007을 받는다.

## `maps/data/krx_auth.py`

pykrx 로그인 진입점 두 개(`login_krx`, `build_krx_session`)를 감싼다.

| 상황 | 동작 |
|---|---|
| `CD007`(잠금) · `CD010`(변경필요) | **1회 실패로 즉시 차단**, 6시간 |
| 네트워크 등 일시 오류 | 3회 연속 실패 후 차단 |
| 재차단 | 쿨다운 2배씩 (30분 → 6시간 상한) |
| 차단 중 | **HTTP 요청 자체를 안 보냄** → KRX 실패 카운터가 안 늘고, 상위는 폴백으로 계속 동작 |

핵심은 **치명 코드와 일시 오류를 구분**한 것이다. 단순 N회 카운터로는 부족하다 —
CD010은 몇 번을 해도 성공할 수 없고, 그 시도 자체가 잠금의 원인이었다.

pykrx 가 bool 로 뭉개 삼키는 원본 오류 코드도 살려 로그에 남긴다(7/27 진단을 막은 지점).

**한계**: `webio` 는 임포트 시점에 로그인을 1회 시도하고, 가드는 그보다 앞설 수 없다
(가드를 설치하려면 pykrx 를 임포트해야 한다). 프로세스당 1회는 남는다.
`install_krx_login_guard()` 가 `_auth_session is None` 을 보고 그 실패를 회로에 반영한다.

## 검증

계정이 정상이라 실물 재현이 불가능해서, 운영 서버에서 `KRX_ID`/`KRX_PW` 를 비운 별도
프로세스로 확인했다(**KRX 에 접속하지 않는다**).

```
login_krx patched : True   build_session patched : True
CD007 기록 후 allow() : False   build_krx_session() : None (HTTP 없음)
```

가드는 **pykrx 를 실제로 쓰는 시점에 지연 설치**된다. `broker_sync` 는 KIS 실시간 시세가
채워지면 pykrx 폴백을 안 타므로 로그가 안 뜬다 — 정상이다. 16:40 데이터 수집에서 처음 뜬다.

---

# 주제 ② 손절가 규칙 통일 (커밋 `fb6a789`, 배포 13:20)

7/27 핸드오프 Next Steps 3번. **해소됨.** 조사해 보니 2곳이 아니라 **3곳**이었다.

## 실제 불일치

| 경로 | 방식 | |
|---|---|---|
| 백테스트 청산 `portfolio_replay._resolve_stop` | `min(고정, ATR)` | ✅ |
| 백테스트 사이징 | 위와 동일 | ✅ |
| 매매계획 `scheduler:1455` | `min(...)` | ✅ |
| 실거래 청산 `scheduler:1968` | `atr or fixed` | ❌ |
| 화면 표시 `api/risk.py:199` | `atr or fixed` | ❌ |
| **실거래 사이징** `scheduler:2402` | **고정%만** | ❌ |

두 방향으로 어긋나고 있었다.

1. **저변동성 종목** — `atr or fixed` 는 ATR 이 고정%보다 *좁을 때도* ATR 을 쓴다.
   백테스트는 고정%를 지키는데 실거래만 일찍 털렸다.
2. **고변동성 종목** — 사이징이 손절폭을 절반으로 과소평가해 포지션이 2배가 됐다. (금액이 큰 쪽)

## 7/27 실거래로 확인한 숫자

```
donchian_v2  진입 79,500  ATR14 8,316
  고정 손절 71,550 (-10.0%)   ATR 손절 62,867 (-20.9%)  ← 실제 청산은 여기서

수정 전(고정%로 사이징)  qty=54  손절 시 -898,182원 = 계좌 1.04%
수정 후(정본으로 사이징)  qty=25  손절 시 -415,825원 = 계좌 0.48%
설정한 1회 계좌위험(0.5%)                        430,841원
```

실제 주문은 52주, 실현손실 972,400원 — **설정값의 2배가 넘었다.**

## 조치

`live_rules.effective_stop_price(strategy_id, entry_price, atr14)` 가 정본.
고정%와 ATR 중 **넓은(가격이 낮은) 쪽**. 근거는 두 규칙의 역할이 다르다는 것 —
고정%는 *넘지 못하는 하한선*, ATR 은 *잔진동 방지 완충*. 그래서 ATR 은 고정%를
느슨하게만 만들 수 있고 조이지는 못해야 한다.

`_order_qty` 에 `atr14` 를 넘겨야 했는데, 주문 경로에 이미 `signal.atr14` 가 있어서 그대로 연결됐다.

**백테스트는 안 건드렸다.** `_resolve_stop` 은 이미 정본과 같고, 전략 신호의 `stop_price` 와
미등록 전략용 `_ATR_STOP_MULTIPLIER` 폴백이라는 백테스트 전용 입력이 둘 더 있다.

## 배포 후 실측 (보유 2종목)

| 종목 | 고정 | ATR | 수정 전 | 수정 후 |
|---|---|---|---|---|
| 082640 `donchian_v1` | 7,526 (-8.0%) | 7,377 (-9.8%) | 7,377 | 7,377 (동일) |
| 002810 `multi_asset_trend_v1` | 21,476 (-8.0%) | 21,547 (-7.7%) | 21,547 | **21,476** |

두 번째가 고쳐진 버그다. 71원이라 사소하지만, 저변동성 종목이 조기 손절되던 경로가
실제로 살아 있었다는 증거다.

> ⚠️ **사이징 변화는 아직 실측 못 했다.** 7/30 08:55 주문이 첫 적용이다.
> ATR 이 넓은 종목이 걸리면 수량이 눈에 띄게 작아진다. 자본 투입 속도가 느려지고
> `mock_months` 누적 거래 빈도에도 영향이 있다.

---

# 주제 ③ 전략관리 화면 (커밋 `29eae95`·`7bd1514`, 배포 14:51)

## 문제

화면이 `strategy_id` 만 보여줬다. `_to_strategy_item` 이 `name=strategy_id` 로 복사해서,
`donchian_v2` 가 무슨 전략인지 화면 안에서 알 방법이 없었다.

## 설계 — 숫자는 코드에서, 산문만 문서에서

`maps/strategy/catalog.py` 에는 **산문만** 둔다(한글명·요약·아이디어·진입/청산 서술).
손절%·ATR 배수·파라미터·선호 장세·MDD 는 요청 시점에 코드에서 읽는다.

```
손절%·ATR 배수 → live_rules (stop_loss_pct / atr_multiplier 접근자 신설)
파라미터·선호장세 → 전략 클래스
MDD            → constants.ALLOWED_MDD
```

**이 원칙이 바로 값을 했다.** 화면이 선호 장세를 코드에서 읽자마자,
`multi_asset_trend_v1` 이 약세장 포함 **전 장세**로 선언돼 있는데 가이드 원고에는
"강세·혼조"로 적힌 게 드러났다. 숫자를 문서에 복사했다면 아무도 몰랐다.

## 구성

- 탭 `운용 현황` — 기존 표 + 한글명·선호장세 컬럼, 행 클릭 → 상세
- 탭 `전략 설명` — 카드 그리드
- 상세 패널 — 아이디어 → KPI 4장 → 진입/청산 2단 → **가이드 원고 전문 + [전체 복사]**
- `GET /api/v1/strategies/guide/{id}` — 파일명은 카탈로그가 정하므로 요청값이 경로에 안 닿는다

## 블로그 원고

`docs/strategy_guides/` 에 전략 1개 = 글 1편(+공통 도입부). 네이버 스마트에디터는
마크다운을 렌더링하지 않아 **`##`·`**`·표를 쓰지 않았다** — 구분선·이모지·들여쓰기만 썼다.
파일 전체를 복사해 붙여넣으면 그대로 발행된다.

## 재발 방지

`tests/test_strategy_catalog.py` 가 `STRATEGY_GROUP_MAP` 의 모든 전략에 산문·클래스·가이드
파일이 있는지, 응답 숫자가 `live_rules` 와 같은지 검사한다. 설명 없이 전략을 추가하면 빌드가 깨진다.

> ⚠️ **브라우저로 눈으로 본 적은 없다.** HTTP 응답·JS 문법(`node --check`)·API 페이로드까지만
> 확인했다. 탭 전환·카드 클릭·복사 버튼 실동작은 미확인.

---

# 주제 ④ 도메인 이전 `magable.kr` → `maps.magable.kr` — **완료**

**DNS·nginx·인증서는 사용자가 오늘 13:05~14:35에 직접 처리했다.** 세션 중에 바뀌어서,
배포 검증을 구 도메인으로 하다가 WordPress 404 를 받고 발견했다.
나머지(웹훅·모바일·ads.txt·HSTS)는 이 세션에서 처리했다.

| | 오전 11:07 | 현재 |
|---|---|---|
| `magable.kr` / `www` | 3.37.117.246 | **54.180.179.20** (WordPress, 남의 서버) |
| `maps.magable.kr` | 없음 | **3.37.117.246** |
| 인증서 | `magable.kr`+`www` | `maps.magable.kr` (만료 2026-10-27) |
| nginx | `sites-enabled/maps` | `sites-enabled/maps.magable.kr` |

구 vhost `/etc/nginx/sites-available/maps` 는 비활성으로 남아 있다.

| 항목 | 상태 |
|---|---|
| DNS · nginx · 인증서 | ✅ 사용자 작업 |
| 텔레그램 웹훅 | ✅ `6d755cd` (아래) |
| 모바일 앱 | ✅ `0fd872a` + 재빌드·설치 (아래) |
| `ads.txt` | ✅ vhost 복구 |
| HSTS | ✅ vhost 복구 |
| 문서 | ✅ `d36a08f` |

## vhost 복구 — `ads.txt` 와 HSTS

certbot 이 새로 만든 vhost 에 구 설정의 두 가지가 빠져 있었다.

**`ads.txt`** — 복구 전 `303` 이었다. 요청이 앱으로 프록시돼 로그인 게이트에 걸리고 있었다.
크롤러는 리다이렉트를 받으면 파일을 못 읽는다. `location = /ads.txt` 를 443 블록에 복구
(퍼블리셔 ID 는 구 vhost·블로그와 동일한 `pub-6163734207162127`).
검증: `status=200 type=text/plain redirects=0`.

**HSTS** — `add_header Strict-Transport-Security "max-age=31536000" always;`
**`includeSubDomains` 는 일부러 넣지 않았다.** 켜면 `magable.kr` 의 모든 서브도메인이
HTTPS 강제되는데 루트는 이제 남의 서버라 통제할 수 없다. 구 설정도 이 값이었다.

> 두 작업 모두 앵커를 조심해야 한다. `server_name maps.magable.kr;` 은 443 블록과 80
> 리다이렉트 블록에 **각각 있다**(첫 번째가 443). HSTS 는 `ssl_certificate_key` 라인을
> 앵커로 잡아 443 전용임을 보장했다. 백업: `maps.magable.kr.bak.{adstxt,hsts}.*`.

`add_header` 는 하위 `location` 이 자체 `add_header` 를 가지면 상속이 끊긴다.
`location = /ads.txt` 에는 없어서 HSTS 가 정상 상속됐다(확인함).

> 📌 **앱에 광고 코드가 없다.** `templates/`·`static/`·모바일 어디에도 `adsbygoogle`/
> `ca-pub` 스크립트가 없다. `ads.txt` 는 판매자 선언일 뿐 광고를 띄우지 않는다.
> 구 도메인에서도 `ads.txt` 뿐이었으니 애드센스는 실질적으로 블로그 쪽에서만 돌았을 것이다.
> 게재하려면 애드센스 콘솔에 `maps.magable.kr` 사이트 등록이 필요하다(사용자 작업).
> 다만 대시보드는 로그인 벽 뒤라 게재 자체가 정책상 제한될 수 있다.

## 모바일 앱 — 재빌드·설치 완료 (커밋 `0fd872a`)

`PROD_DEFAULT` 는 **APK 안에 컴파일되어 박힌다.** 서버를 재배포해도 설치된 앱은 구 도메인을
계속 호출한다. 도메인 이전 후 앱은 남의 서버에 API 를 요청하고 있었다.

빌드: `npm test`(18 passed) → `npm run cap:sync` → `assembleDebug`.
**`JAVA_HOME` 이 jdk-17 을 가리키고 있어서** 21 을 명시해야 한다(PATH 의 `java` 는 21이라
`java -version` 만 보면 속는다).

```bash
JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot" ./gradlew assembleDebug
```

산출물 `apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk`.
검증은 dist → android assets → **APK 내부(unzip)** 3단계로 문자열을 확인했다(구 도메인 0건).
`google-services.json` 은 이미 `android/app/` 에 있어 7/22 이월 이슈는 해당 없었다.

**설치 후 실측**: nginx 접속 로그에 폰(`106.101.195.128`)에서 온 요청이 전부 `200`.
CORS preflight(`OPTIONS`)도 통과. 오늘 모바일 요청 45건.
(`499` 한 건은 응답 전 클라이언트가 끊은 것 — 화면 전환 시 나는 정상값이다.)

## 텔레그램 웹훅 — 재배포로는 안 잡히는 종류 (커밋 `6d755cd`, 처리 완료)

웹훅 URL은 **텔레그램 서버에 저장**된다. 도메인이 바뀌어도 우리가 재배포한다고 갱신되지 않는다.
발견 당시 `getWebhookInfo` 의 `ip_address` 가 `54.180.179.20` — 콜백이 WordPress 로 가고 있었다.
URL 문자열만 보면 멀쩡해 보인다. **`ip_address` 를 봐야 잡힌다.**

유실은 없었다(`pending 0`, 도메인 이전 후 버튼 입력이 없었다). 재등록 후 `3.37.117.246` 확인.
엔드포인트 도달도 확인: POST 시크릿없음 403 / 잘못된시크릿 403 / GET 405
(303 이 나오면 로그인 게이트에 막힌 것이다).

유효한 시크릿으로 위조 콜백은 **보내지 않았다** — 실제 주문 승인/거부가 트리거될 수 있다.
**실제 버튼 1회 눌러 확인 필요.**

---

## 7/28·29 주문 실측 (7/27 Next Steps 5번 — 확인 완료)

| 날짜 | 전략 | 종목 | 수량 | 지정가 | 체결가 |
|---|---|---|---|---|---|
| 7/28 08:55 | `donchian_v1` | 082640 | 636 | 8,370 | 8,180 |
| 7/29 08:55 | `multi_asset_trend_v1` | 002810 | 226 | 23,450 | 23,344 |

**체결 반영이 이번엔 자동으로 됐다**(7/27은 배포 후 수동 확인이었다).
7/28 09:01 `updated_orders: 1`, 7/29 는 09:01·09:03·09:04 세 번 — 분할체결이 순차 반영됐고
현금 감소 누계 5,276,490원이 `226 × 23,344` 와 일치했다.

7/29 14시 기준 총자산 약 85.1M, 보유 2종목, 미체결 0, `sync_errors: 0`.

---

## What Worked

- **로그 대신 코드로 확인한 것.** "가드 설치 로그가 안 뜬다"에서 멈추지 않고 호출 경로를
  따라가니, KIS 실시간 시세가 채워지면 pykrx 폴백을 안 타서 정상이라는 게 나왔다.
- **HANDOFF 를 그대로 믿지 않은 것.** ATR 불일치가 2곳으로 적혀 있었지만 실제로는 사이징까지
  3곳이었고, 금액이 큰 쪽은 적혀 있지 않던 사이징이었다.
- **숫자를 코드에서 렌더링하게 한 것.** 켜자마자 문서 오류를 스스로 잡아냈다.
- **운영에서 자격증명을 비운 프로세스로 검증**한 것. KRX 를 건드리지 않고 가드를 확인했다.

## What Didn't Work / 주의

- **구 도메인으로 배포 검증을 했다.** 세션 중에 DNS 가 바뀌는 건 예상 밖이었다.
  검증 전에 `dig` 로 현재 A 레코드를 확인하는 편이 빠르다.
- **`date.today()` + UTC 저장 컬럼은 여전히 상습 함정.** 새 쿼리는 `kst_day_bounds_utc()`.
- `order_log` 에 `price` 컬럼은 없다 — `order_price` / `fill_price`. `qty` 다(`order_qty` 아님).
- `journalctl` 에 `broker_sync` 가 60초마다 찍힌다 → `| grep -v broker_sync` 필수.
- **테스트 스텁을 `SimpleNamespace` 로 만들지 말 것.** 프로덕션이 `signal.atr14` 를 읽기
  시작하자 스텁에만 필드가 없어 3건이 깨졌다. 실제 dataclass 를 쓰면 안 깨진다.
- **`java -version` 만 보고 JDK 를 판단하지 말 것.** PATH 는 21 인데 `JAVA_HOME` 은 17 이었다.
  Gradle 은 `JAVA_HOME` 을 따르므로 모바일 빌드 시 명시해야 한다.
- **nginx 앵커 주의.** `server_name maps.magable.kr;` 은 443 블록과 80 리다이렉트 블록에
  각각 있다. 80 쪽에 넣으면 HTTPS 요청에는 적용되지 않는다.
- **`analyze` 픽 0건과 스케줄러 주문은 다른 파이프라인이다** (혼동 금지):
  - `analyze`(cron 16:00) → `analysis_pick` → 워치리스트. 게이트 R:R ≥ 2.0.
  - 스케줄러(16:50 후보생성 → 08:55 주문) → `candidate_snapshot` → `order_log`.

---

## Next Steps

> 도메인 이전은 **전부 끝났다**(주제 ④). 남은 건 사용자 계정 작업 하나뿐:
> 애드센스 콘솔에 `maps.magable.kr` 사이트 등록. 단, 앱에 광고 코드가 없고
> 대시보드가 로그인 벽 뒤라 게재 자체를 다시 판단할 필요가 있다.

### 관측/확인 (이 세션의 변경이 실제로 도는지)

1. **7/30 08:55 주문 수량** — 정본 사이징 첫 적용. ATR 이 넓은 종목이 걸리면 수량이
   눈에 띄게 작아진다. 주제 ② 참고. **아직 실측 못 한 유일한 변경이다.**
   ```
   sudo journalctl -u maps --no-pager | grep -v broker_sync | grep "order_cycle: success" | tail -1
   ```
2. **16:40 데이터 수집 로그** — `KRX 로그인 회로차단기 설치 완료` 첫 출력.
   성공 시엔 이후 조용하다(성공은 로그를 남기지 않는다).
3. **텔레그램 인라인 버튼 1회** 눌러 콜백 도달 확인 (위조 콜백은 안 보냈다).
4. **전략관리 화면 눈으로 확인** — 탭 전환·카드 클릭·[전체 복사] 버튼.
   HTTP·JS 문법·API 페이로드까지만 확인했다.
5. **~2026-10월말 `mock_months ≥ 3`**. 단 **점수 34.7 < 임계값 75**라 승격은 여전히 안 된다 —
   Live Small 차단만 풀린다. 점수 개선은 별도 과제.

### 판단 필요

6. **업종 필터 활성화** — 점수 가중치 7개 중 `_score_from_db` 가 채우는 건 3개(0.50)뿐이고,
   단일 최대 가중치 `earnings_revision` 0.25 가 통째로 자리표시자다. 활성화 전
   **레거시 선택기의 임계값 부재**부터 손볼 것(7/24 관측에서 "강세업종" 5개 중 하위 2개가
   마이너스 수익률이었다). `MAPS_SECTOR_FILTER_ENABLED` 는 꺼져 있어도 기록은 쌓인다.
7. **애드센스 게재 여부** — 콘솔에 `maps.magable.kr` 등록이 필요하고, 앱에 광고 코드가 없다.
   대시보드는 로그인 벽 뒤라 정책상 제한될 수 있다. 블로그(`magable.kr`) 쪽이 실효가 클 것이다.
   `docs/strategy_guides/` 원고 8편이 마침 그쪽 콘텐츠다.
8. 이월: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리,
   네트워크 테스트 mock 화, 서명 릴리스 APK. `order_log_backup_20260724`(42행) DROP 가능.

---

## 핵심 파일 맵

- **KRX 인증**: `maps/data/krx_auth.py` — 로그인 회로차단기. 설치 지점 4곳
  (`data/krx_adapter.py:__init__`, `market/regime.py:_krx_index_weekly`,
  `ops/scheduler.py` ×2, `stock_analysis/analyzer.py`).
  벤더 원본은 `.venv/.../pykrx/website/comm/auth.py`.
- **손절 정본**: `maps/strategy/live_rules.py` — `effective_stop_price()`,
  `stop_loss_pct()`, `atr_multiplier()`. 소비처: `scheduler._submit_exit_orders`,
  `scheduler._order_qty`, `api/risk.py`. 백테스트만 `backtest/portfolio_replay._resolve_stop`.
- **전략 설명**: `maps/strategy/catalog.py`(산문), `maps/api/strategies.py`(조합·가이드 API),
  `templates/strategies.html`, `static/js/app.js`(`loadStrategies` 이하), `static/css/main.css`(말미).
  원고는 `docs/strategy_guides/`.
- **날짜 경계**: `maps/execution/order_manager.py` — `kst_day_bounds_utc()`.
- **장중 시세**: `maps/ops/scheduler.py` — `_fetch_intraday_prices`(브로커 우선),
  `_fetch_intraday_prices_pykrx`(폴백).
- **블로그**: `maps/ops/daily_digest.py`, `maps/api/{daily_digest,blog}.py`,
  `scripts/run_blog_cron.sh`(`BLOG_DENY`), `scripts/verify_blog_numbers.py`.
  출력 `/opt/maps/blog/`, cron `/etc/cron.d/maps-blog`.
- **승격**: `maps/promotion/gate.py`(`_MIN_MOCK_MONTHS_FOR_LIVE_SMALL=3`),
  `scheduler`(`_order_candidates`, `_mock_track_months`), `settings.is_paper_account`.
- **테스트**: `tests/test_krx_login_guard.py`, `tests/test_effective_stop_price.py`,
  `tests/test_strategy_catalog.py`, `tests/test_order_qty.py`, `tests/test_scheduler.py`.
- **analyze 자동화(서버)**: `/etc/cron.d/maps-analyze`, `scripts/run_analyze_cron.sh`,
  `.claude/commands/analyze.md`, `scripts/load_analysis_picks.py`.
- **운영 접속**: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
