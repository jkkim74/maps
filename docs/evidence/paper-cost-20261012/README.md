# Paper cost screen verification — 2026-10-12

These are **synthetic local fixtures**, not an operational account or evidence of
broker expense availability. The temporary HTTP server imported only stdlib/Jinja,
rendered the repository's `risk.html`/`base.html` and served the shipped CSS and JS.
External CDN/font references were removed from the fixture response. It did not
import the application, read credentials, connect to a database or submit orders.
POST was disabled; API/commit behavior is covered by the isolated regression suite.

- [Account view](account-screen.png): latest residual -280 is distinct from the
  historical -120 marker and confirmed -80 adjustment. Costs are not prefilled.
- [Historical order view](order-only-screen.png): switching modes disables account
  amount/source/hash inputs, shows the original paid interval/evidence ID, and
  leaves commit disabled before a reviewed preview. Input labels, two-column
  layout and action buttons are visible without overlap.

Chrome rendered the real templates at its native desktop viewport. The existing
application-wide fixed desktop viewport was retained; this is not a separate
mobile-responsive acceptance claim. The fixture tab and server were closed after
verification. No production setting, account state or strategy activation changed.
