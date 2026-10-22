# UI screenshot evidence — offline synthetic fixtures

All four PNGs were captured in cached Chromium headless shell (Playwright browser
revision 1228) against a loopback-only fixture server and individually opened with
`view_image` for visual inspection. Existing Jinja templates, static app.js/main.css
and the existing production mobile React build were rendered by a real browser.
No product source, tests, build artifacts or dependencies were changed.

| Image | Viewport | Observed behavior |
| --- | --- | --- |
| [orders-blocked.png](orders-blocked.png) | 1600×1200 | Global OFF and stale reconciliation reasons, separate strategy kill, UTC-derived KST check time, null actual slippage/sample 0, pending requested 10/filled 4/remaining 6, cancelled partial cumulative fills. |
| [limit-up-runtime-refused.png](limit-up-runtime-refused.png) | 1600×1000 | Actual mode off despite configured automatic, engine stopped/start refused/live_trading_disabled, entry block, degraded feed age 120s, queue 255, stale drops 12, overflow 45. |
| [mobile-readiness-blocked.png](mobile-readiness-blocked.png) | 430×1400 | Actual production mobile home component shows global OFF, stale account evidence and separate strategy reason. |
| [mobile-orders-readiness-blocked.png](mobile-orders-readiness-blocked.png) | 430×1600 | Actual mobile orders component shows readiness reasons, cumulative order-created-day collection basis, sample absence, partial remainder 6 and cumulative fill 4. |

The screenshots total about 304 KiB. They are viewport captures, not full-page
exports. The web orders table continues below the bottom of its viewport; all
changed readiness/quantity cards are visible. Browser-default system fonts replace
external Google fonts because network fonts and Plotly CDN are blocked/removed in
the capture fixture. Styling otherwise uses shipped CSS. Low-contrast muted web
copy reflects existing stylesheet colors and was not changed for the image.

Every image includes an explicit **OFFLINE FIXTURE / synthetic / no order execution**
marker. Startup refusal/configured automatic is a representative test case, not
an observation of production. Test account/order names and quantities are synthetic.
The cancelled partial fixture has unknown fill price, so null actual slippage and
sample 0 remain consistent. Production readiness, profitable results, actual
holdings or native Android rendering are not established by these captures.

`fixture_server.py` serves existing files on 127.0.0.1:8875. It strips external
font imports and CDN tags, enforces same-origin CSP, and routes the existing mobile
bundle's fetch calls to loopback fixtures. Its local synthetic auth marker is not
a credential and is never sent to production. No browser/computer-use connection
or new dependency was required. The server is stopped after capture.

Capture command pattern:

```powershell
& '<cached chromium_headless_shell-1228>/chrome-headless-shell-win64/chrome-headless-shell.exe' --no-first-run --disable-background-networking --disable-component-update --no-proxy-server --hide-scrollbars --window-size=1600,1200 --virtual-time-budget=5000 --screenshot='<this directory>/orders-blocked.png' http://127.0.0.1:8875/orders
```

For mobile, use `/mobile` (home) or `/mobile?orders` (the fixture selects the actual
orders navigation button), the mobile viewport, and 6000ms virtual-time budget.
No production reads/writes, broker calls, notifications, restart or trading setting
change was performed for these screenshots. The original fixture helper remains
git-ignored under `.superpowers/sdd/.../ui/`; these four images and this explanation
are copied into the tracked handoff assets for review.
