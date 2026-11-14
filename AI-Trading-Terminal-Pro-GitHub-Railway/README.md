# AI Trading Terminal Pro — Railway Starter (DEMO ONLY)

Starter project for GitHub → Railway. Contains a responsive HTML dashboard and FastAPI backend. The UI includes candlestick display, synthetic technical signals, approval/rejection and simulated orders. API also provides demo multi-account summaries, positions, history and simulated Close All.

**NOT LIVE:** No broker login, no MetaTrader connectivity, no actual AI model or verified fundamentals, no real orders. Demo state is in memory and resets when the server restarts. API has no authentication and MUST NOT be connected to live broker credentials or a trading gateway as-is.

## Local

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://localhost:8000. API docs: http://localhost:8000/docs.

## GitHub

Create an empty GitHub repository, upload all files in this folder to its root (or `git init`, `git add .`, `git commit -m "Initial demo"`, `git branch -M main`, `git remote add origin YOUR_REPO_URL`, `git push -u origin main`). Never commit `.env`, passwords, broker tokens, or account credentials.

## Railway

1. Railway → New Project → Deploy from GitHub Repo → select repository.
2. Railway detects `Dockerfile` and `railway.json`. No variables required for demo.
3. In Networking, generate a public domain.
4. Visit `https://YOUR-DOMAIN/api/status` (should show DEMO / DISCONNECTED), then open the root URL.

Railway pricing/free trial may change; check current limits in your account. This is not a production-grade deployment.

## Demo API

- `GET /api/status`
- `GET /api/candles/XAUUSD`
- `POST /api/analyze/XAUUSD`
- `POST /api/approve`
- `GET /api/orders`
- `GET /api/accounts`
- `GET /api/accounts/mt5-demo/summary`
- `GET /api/accounts/mt5-demo/positions`
- `GET /api/accounts/mt5-demo/history`
- `POST /api/accounts/mt5-demo/close-all-demo` with `{"confirm_demo":true,"side":"ALL"}`

## Before real broker trading

Implement authentication and per-user account authorization, encrypted secret management, PostgreSQL, audit logging, idempotent order commands, account-scoped approval, risk limits, quote freshness checks, secure authenticated EA bridge with broker execution acknowledgements, and paper/demo-account integration testing. MT4 and MT5 require their respective terminal/gateway; broker compatibility is not universal. Never treat an AI score as a calibrated win probability.

Android APK is not included in this starter; responsive web is available on Android browsers. A Capacitor wrapper can be built after the frontend is migrated to a suitable mobile project.


## v4: MT4 / MT5 native technical indicator bridge (read-only)

All existing **demo routes** stay unchanged. New protected dashboard: `/live`.
This gateway does NOT execute orders, close positions, or accept broker passwords.
Real broker login stays inside licensed MetaTrader terminals on your Windows VPS.

### Railway environment variables
Create three Railway **Variables** (never put tokens in GitHub):

- `BRIDGE_TOKEN`: random unique secret 24+ chars, same value in EA Inputs.
- `DASHBOARD_TOKEN`: *different* random secret 24+ chars, used only in the /live dashboard login.
- `GEMINI_API_KEY`: Google AI Studio API key used for live AI read-only technical analysis.
- Optional `GEMINI_MODEL`: default `gemini-3.5-flash-lite`; change to a model enabled on your account.

Generate tokens locally in a secure password manager; never send login passwords or API keys in chat.

### On Windows VPS
1. Install the **official** MT4 or MT5 terminal provided by your broker. Log in **in the terminal**.
2. Open MetaEditor. Copy `bridges/MT4_IndicatorBridge.mq4` into MT4 `MQL4/Experts/` (or `.mq5` into MT5 `MQL5/Experts/`), then compile (F7). The EA sources have not yet been compiler-tested in MetaEditor.
3. In MetaTrader Tools > Options > Expert Advisors, check `Allow WebRequest for listed URL` and add:
   `https://ai-chart-wnalisis-production.up.railway.app` (MetaTrader requires this explicit allowlist).
4. Attach the read-only EA to a chart for each symbol you want to monitor. Input `ApiBase` and `BridgeToken` (same value as Railway). Run on a **demo account first**.
5. Open `https://ai-chart-wnalisis-production.up.railway.app/live`, enter `DASHBOARD_TOKEN`, and select a connected account/symbol/timeframe.
6. Native indicators read from the **last closed candle** (shift 1): EMA20/50/200, RSI14, MACD 12/26/9, ADX14 +/-DI, ATR14, Bollinger Bands 20/2. Available M1, M5, M15, M30, H1, H4, D1, W1, MN1 when bars exist.
7. Click **Analyze Broker Data with AI** to call Gemini. It never sends an order.

### Current limitations / safety
- Gateway data and latest history are **in memory** on Railway. Redeploys/restarts/replicas reset or desynchronize it. For production use a durable PostgreSQL store and one shared cache.
- Single shared bridge/dashboard token per environment; use per-account scoped credentials + authentication before multi-user production deployment.
- `/live` is protected for reading broker data; the HTML itself is public and requests a token. Do not store real passwords anywhere in source code.
- History is capped to recent terminal-visible orders/deals, and MT5 may report deals rather than complete round-trip trades.
- EA data arrival is approximately every 30 seconds; stale after 120 seconds. It is **not** a low-latency quote feed.
- There is **no fundamental news feed**: AI always marks fundamental analysis NOT_CONNECTED.
- No AI confidence means guaranteed probability; no live Close All or auto-entry endpoints.
- Only execute through actual MT4/MT5 terminals with separate controls after a security, risk and integration review.


## v5 — Mobile dashboard PIN, tabs, concise AI

The \`/live\` dashboard now uses **4-digit dashboard PIN authentication** and an 30-day
signed Secure + HttpOnly + SameSite=Strict cookie. This is *read-only convenience
authentication* — a 4-digit PIN is weaker than a full password or MFA.

### Required Railway variables for the new login
- \`DASHBOARD_PIN\`: exactly **four digits**, e.g., a unique PIN that you choose in
  the Railway Variables UI. **Never enter it in GitHub or share it in chat.**
- Keep \`BRIDGE_TOKEN\` unchanged (existing MT4/MT5 EA installations use it).
- Keep \`GEMINI_API_KEY\` and \`GEMINI_MODEL\` unchanged.
- \`DASHBOARD_TOKEN\` continues to work as a long API Bearer token, but the new browser
  dashboard only accepts PIN; you may retain this secret for external read-only
  clients, or omit it if unused. Do not put it in the browser.
- Use exactly **one Railway web replica** for this MVP because PIN sessions,
  lockout counters and broker snapshots are stored in per-process memory.
  Restarting Railway loses recent broker snapshots until the next EA push, but signed dashboard cookies remain valid across restarts (while their signing secret stays unchanged).

### UX changes
- Four top-level tabs: **Overview, Chart, AI Signal, History**.
- History uses five rows per page and Previous/Next buttons.
- AI returns one concise technical BUY/SELL bias + a **confidence alignment score**
  derived from native MetaTrader indicators. It is **not** a calibrated probability
  of profit. A low score (<60) is marked **Low alignment · No entry**.
- Gemini adds just one short rationale and one brief risk.
- No WAIT label, but no forced trade execution: contradictory signals can have a low
  alignment score and always stay read-only.
- Fundamental news remains **NOT_CONNECTED**.
- Existing demo dashboard and native MT4/MT5 EA data bridge stay intact.

### Security considerations
- 4-digit PIN is suitable only for a restricted read-only prototype; it is **not**
  sufficient authentication for future live trade operations. Before enabling
  trading, add per-user accounts, strong passwords/MFA, isolated account access,
  durable PostgreSQL sessions and distributed rate limiting.
- Login attempts are limited in-process (five failed attempts per remote address
  per 15 minutes and an additional global cap). Clearing the process resets those
  limits. HTTPS is required for the session cookie.
- All trading and bulk-close operations are disabled in /live.


## v5.1: Login sekali pada HP yang sama

- Users enter the **4-digit DASHBOARD_PIN** only on first visit.
- The server issues a **30-day HMAC-signed Secure/HttpOnly/SameSite=Strict cookie**.
- Subsequent visits from the same browser automatically restore the dashboard.
- Railway redeploys no longer force a PIN re-login, provided the signing secret
  stays the same.
- Uses \`DASHBOARD_SESSION_SECRET\` if configured, otherwise the existing
  \`DASHBOARD_TOKEN\` as signing key (must be a random secret >=24 characters).
  This key remains ONLY on Railway; it is not typed into the dashboard.
- Server sets \`Cache-Control: no-store\` on the HTML to prevent stale login UI.
- Logging out deletes the local browser session. To revoke all sessions, rotate
  \`DASHBOARD_SESSION_SECRET\` (or \`DASHBOARD_TOKEN\` when using its fallback).
- A stolen signed cookie remains reusable until expiration or key rotation;
  do not use this simplified authentication for real order execution.
- For Chrome mobile if old login appears: open
  \`https://ai-chart-wnalisis-production.up.railway.app/live?v=51\`.
- The only newly required setup remains \`DASHBOARD_PIN\` set in Railway Variables.


## v6 — AI-assisted DEMO auto-entry and Cut & Switch

### What changed
This version prepares a **separate demo-only EA** for MT4 and MT5 that can request
a fresh strategy verdict from Railway. This is **not live-money trading**.

- \`bridges/MT4_AutoSwitch_DEMO.mq4\` and \`bridges/MT5_AutoSwitch_DEMO.mq5\`
  are **new, distinct EAs**. They do not replace the read-only
  \`MT4_IndicatorBridge.mq4\` / \`MT5_IndicatorBridge.mq5\` that send indicator data.
- \`POST /api/bridge/strategy\` requires the \`BRIDGE_TOKEN\` already used by the
  existing read-only EA. Never expose it in the browser.
- A server-side technical planner reads broker-native M15, H1, H4 (and available
  other timeframes). H1/H4 must confirm the BUY/SELL bias; ADX(H1) >= 18,
  ATR(H1) > 0, score >= 51 and fresh frame data are required.
- Gemini is a **veto**, not the trading risk engine: it must agree with the
  technical direction. Invalid/stale/missing data, API failure or disagreement
  blocks entry. Gemini agreement is cached per H1 candle to limit costs.
- 51% means an **indicator alignment score**, *not* an estimated win rate.
- BUY/SELL signal shown in the dashboard remains **read-only**. Cut & Switch is
  handled by the separate opt-in EA, not a dashboard button.

### Cut & Switch rules
1. The EA only manages its own positions (unique EA Magic number), never
   manually opened positions or positions belonging to another EA.
2. If a newly qualified opposite direction is received, the EA attempts to
   close the existing position. A successful close is required. It does not
   open an opposite position immediately in the same timer callback.
3. After at least **120 seconds cooldown**, the EA polls the server **again**;
   the strategy must still qualify, local quote/spread/risk checks must pass,
   and no other position may exist for the symbol.
4. At most two new positions per day by default. One EA position per symbol.
   A stop-loss and take-profit are required on each entry, sized using H1 ATR.
5. Default **FixedLot = 0.01**, estimated risk <= **0.25% equity per trade**,
   daily equity drawdown >= **1%** blocks *new* entries. Risk and order execution
   are checked by the EA using the local broker's contract specifications.

### Installation on a Windows VPS (DEMO ACCOUNT ONLY)
1. Keep your **existing read-only IndicatorBridge EA** on one chart to push
   indicators to Railway every ~30s.
2. Open a *second chart of the same symbol*. Install the new
   \`MT4_AutoSwitch_DEMO.mq4\` (MT4) or \`MT5_AutoSwitch_DEMO.mq5\` (MT5)
   in \`MQL4/Experts/\` or \`MQL5/Experts/\`. Compile through MetaEditor (F7).
   The new source files have **not yet been tested in MetaEditor**.
3. In terminal settings, enable **Allow WebRequest** to the HTTPS URL of Railway
   and, when ready for demo trading, enable the terminal's **AutoTrading**.
4. EA Inputs: set \`BridgeToken\` to the existing \`BRIDGE_TOKEN\` value from
   Railway. It must **not** be the 4-digit dashboard PIN.
5. Start with \`EnableDemoOrders=false\`. Confirm that the EA is seeing fresh
   indicator snapshots, and no trade is sent.
6. On an explicitly chosen **DEMO** account only, after verifying lot size,
   symbol suffix, stop distances, spread, trading hours and account settings,
   set \`EnableDemoOrders=true\` to begin a controlled forward test.
   **Never put this new EA on an account with real funds.** The EA contains a
   hard account-demo check; MT5 also requires a hedging-mode demo account.
7. Keep one execution EA instance per symbol and account. Separate symbols
   need separately tested EA instances and allocation limits.

### Why there is no Entry button in /live
The dashboard is deliberately read-only. The new execution EA sends approved
orders directly from the broker's MetaTrader terminal; the Railway app only
serves filtered signals. A four-digit PIN is inadequate authorization for
sending live orders.

### Known limitations and backtesting
- This strategy has **not been backtested or profitability-validated**.
  A score >=51 is **not** adequate evidence for live trading.
- Need robust broker-specific acceptance tests for volume, stops, margin,
  slippage, trade-server retcodes, swap, hedging/netting and market closures.
- No fundamental news feed is connected, so calendar and high-impact news
  blackout filters are **not available**. Keep real-money execution disabled.
- WebRequest is unavailable in the native MetaTrader Strategy Tester; test
  the signal strategy separately with historical fixtures and run the
  end-to-end gateway as a forward test on a demo VPS account.
- Railway in-memory snapshots/AI cache disappear on restart or scale-out; for
  production replace with database/cache, per-account security and idempotent
  durable command logs.
- MT4/MT5 code needs actual MetaEditor compilation and broker demo testing
  before any claims of operational compatibility.

### Remote kill switch (Railway)

`DEMO_EA_STRATEGY_ENABLED` defaults to **false**. All EA strategy replies remain `BLOCK` unless this Railway Variable is explicitly set to `true`. It is a second permission gate independent of the EA's `EnableDemoOrders` input. Only enable after verifying all demo safety checks; set it back to `false` to stop *new* EA entries. Existing EA positions are not automatically closed when disabled; their broker stop-loss and take-profit remain active.


## v7 — DEMO fundamental calendar + M5 scalping + risk-based lot sizing

This is a research prototype, **not yet MetaEditor-compiled or backtested**.
Never use v7 EAs on an account with real funds.

### Trading Economics macro calendar
- Railway environment: TRADING_ECONOMICS_API_KEY (requires a key with economic
  calendar access; usage may be paid). Do not commit secrets.
- Official docs: https://docs.tradingeconomics.com/economic_calendar/country/
- The fundamental_gate.py module fetches high-impact events for currencies
  corresponding to a broker symbol, e.g. XAUUSD.h => US, EURUSD => US + Euro Area.
- Standard event blackout: 30 minutes before to 30 minutes after.
  Major CPI / NFP / PCE / Fed events: 60 minutes before to 45 after.
  Approximate-time releases block broadly (up to a day).
- No API key, invalid/missing records, provider outage, or unsupported
  symbol-country mapping always BLOCK new trades.
- Recent official release calendar entries with actual/forecast are passed
  to Gemini as a **fundamental compatibility veto**. This does not provide
  calibrated directional macro predictions. CALENDAR_CLEAR is NOT a guarantee
  of future price movement.
- MT5 has a native economic-calendar API too, but the v7 implementation uses
  the same provider cloud adapter for MT4 and MT5. Native MT5 backup is
  reserved for future integration.

### M5 scalping rules
- Indicator direction of M1, M5, M15, H1 must agree; H4 cannot contradict.
- M15 ADX >= 20, M5 ATR > 0, M5 RSI 52–72 (BUY) / 28–48 (SELL).
- Last CLOSED M5 bar is the entry-bar deduplication key.
- Confidence >= 51 is only an **indicator-alignment screen**, not a
  51% probability of winning.
- Gemini must agree with the technical side and mark fundamental context
  compatible; missing model / news feed or conflicting data causes BLOCK.
- Cut & Switch remains cut -> confirm closed -> cooldown (180s) ->
  re-evaluate fresh broker technical and news conditions -> consider switch.

### New, distinct DEMO-only EAs
- bridges/MT4_Scalper_Fundamental_DEMO.mq4
- bridges/MT5_Scalper_Fundamental_DEMO.mq5

Both require the separate existing IndicatorBridge EA to publish candles to
Railway; do not replace that EA. Put only one scalping EA on a separate chart.

### Dynamic lots with a hard cap
- Default risk per trade: 0.25% equity.
- MaxLot default = 0.05 lot; it is a **cap**, not a guaranteed entry volume.
- Lots are rounded DOWN to broker volume step based on M5 ATR stop-loss
  and account equity. If the broker's minimum volume exceeds risk cap, skip.
- MT5 uses OrderCalcProfit + OrderCalcMargin; MT4 estimates with broker
  tick size and value (which may differ by contract and account).
- Default SL = ATR(M5)*1.5, TP = ATR(M5)*2.0. Extra guard against spreads
  >20% of SL distance; additional modeled 20% risk buffer (not a guarantee).
- Max 3 new trades/day, max 1 open account-wide market position, max daily
  equity drawdown 1%, and reversal cooldown 180 seconds. No martingale,
  no averaging down, and only positions with this EA's magic are cut.
- Actual losses can exceed estimates due to gaps, slippage and commissions.

### VPS setup
1. Keep old MT4_IndicatorBridge.mq4 or MT5_IndicatorBridge.mq5 attached.
2. Copy and compile appropriate new v7 EA in MetaEditor (F7) on a **DEMO**
   account, add Railway to Tools > Options > Expert Advisors WebRequest URLs.
3. Enter the existing BRIDGE_TOKEN in EA Inputs. It is not the dashboard PIN.
4. Check fundamental status and API permissions before attempting a demo
   order. TRADING_ECONOMICS_API_KEY must have access to calendar endpoints.
5. Leave BOTH independent permissions OFF initially:
   Railway DEMO_EA_STRATEGY_ENABLED=false and EA EnableDemoOrders=false.
6. After backtest, MetaEditor compilation and supervised forward testing only,
   explicitly enable the DEMO gates. The EA hard-refuses real accounts.

### Validation before considering a future live redesign
Test on broker-quality historical quote and spread data plus dated events,
commissions, swaps and realistic slippage. Check walk-forward expectancy
after costs, drawdowns, profit factor, spread impact and reversal failures.
Reject settings with negative expectancy. Railway roundtrip + Gemini AI
latency make this unsuitable for tick-speed scalping; the horizon is M5.
