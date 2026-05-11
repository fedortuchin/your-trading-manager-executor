# Local Risk Policy

Provider-backed command execution has a mandatory local risk gate inside the executor. The policy is
stored on the executor host, not in YTM Cloud.

## Fail-Closed Defaults

The executor rejects leased provider-backed commands when:

- `risk-policy.json` is missing;
- policy parsing fails;
- `enabled=false`;
- `killSwitch=true`;
- required limits are missing;
- a command violates the local market, margin mode, symbol, order type, notional, projected
  position, per-symbol position, daily loss, leverage, position mode, or reduce-only rules;
- `executionMode=real` while `paperOnly=true`.

`paperOnly=false` is necessary but not sufficient for real orders. OKX real order placement also
requires the exact `okx_swap_mainnet_order` adapter in the leased command and
`ytm-executor run --enable-real-orders`; without that runtime flag, `real` remains fail-closed.

## Required Local Limits

When `killSwitch=false`, the policy must include:

- `allowedSymbols`;
- `allowedOrderTypes`;
- `allowedMarkets`;
- for futures/swap markets: `allowedMarginModes`, `positionMode=one_way`, and
  `maxSymbolNotional` for every allowed symbol;
- `maxOrderNotional`;
- `maxPositionNotional`;
- `maxDailyLoss`;
- `maxLeverage`.

Example:

```json
{
  "allowedMarkets": ["usdm_futures"],
  "allowedMarginModes": ["cross"],
  "allowedOrderTypes": ["limit"],
  "allowedSymbols": ["BTCUSDT"],
  "enabled": true,
  "killSwitch": false,
  "maxDailyLoss": "250",
  "maxLeverage": "1",
  "maxOrderNotional": "1000",
  "maxPositionNotional": "5000",
  "maxSymbolNotional": {
    "BTCUSDT": "5000"
  },
  "paperOnly": true,
  "positionMode": "one_way",
  "version": 1
}
```

Create it through the CLI:

```bash
ytm-executor risk init --kill-switch-off \
  --allow-market usdm_futures \
  --allow-margin-mode cross \
  --allow-symbol BTCUSDT \
  --allow-order-type limit \
  --max-order-notional 1000 \
  --max-position-notional 5000 \
  --max-symbol-notional BTCUSDT=5000 \
  --max-daily-loss 250 \
  --max-leverage 1 \
  --position-mode one_way
```

Docker install equivalent:

```bash
cd /opt/ytm-executor
sudo docker compose run --rm ytm-executor risk init --kill-switch-off \
  --allow-market usdm_futures \
  --allow-margin-mode cross \
  --allow-symbol BTCUSDT \
  --allow-order-type limit \
  --max-order-notional 1000 \
  --max-position-notional 5000 \
  --max-symbol-notional BTCUSDT=5000 \
  --max-daily-loss 250 \
  --max-leverage 1 \
  --position-mode one_way
```

OKX SWAP equivalent uses `okx_swap`. `allowedSymbols` must match the command symbol from YTM.
The OKX adapter maps plain USDT pairs like `BTCUSDT` to native OKX ids such as
`BTC-USDT-SWAP` before `order-precheck`. If a command contains `quantity`, OKX treats it as
contract size; otherwise the adapter can derive contract size from `orderNotional` plus
`priceReference`:

```bash
ytm-executor risk init --kill-switch-off \
  --allow-market okx_swap \
  --allow-margin-mode cross \
  --allow-symbol BTCUSDT \
  --allow-order-type limit \
  --max-order-notional 1000 \
  --max-position-notional 5000 \
  --max-symbol-notional BTCUSDT=5000 \
  --max-daily-loss 250 \
  --max-leverage 1 \
  --position-mode one_way
```

Inspect:

```bash
ytm-executor risk show
```

## Cloud Cannot Relax It

YTM Cloud sends an approved command. The executor independently loads its local policy and local
risk state before any future broker adapter can run. Command payloads cannot raise limits, disable
the kill switch, add markets, add margin modes, change position mode, add symbols, add order types,
or turn off paper-only mode.

If local risk passes, the executor still has to normalize the command into a broker adapter order
request with deterministic `clientOrderId`. Invalid adapter requests fail closed before any broker
adapter can run.

Heartbeat reports only a sanitized summary: configured/enabled/kill-switch/paper-only flags, limit
presence, and counts. It does not upload the full policy or local risk state.
