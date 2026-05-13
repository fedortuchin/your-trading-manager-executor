# Local Risk Policy

Provider-backed command execution has a mandatory local risk gate inside the executor. The policy is
stored on the executor host, not in YTM Cloud.

## Fail-Closed Defaults

The executor rejects leased provider-backed commands when:

- `risk-policy.json` is missing;
- policy parsing fails;
- `enabled=false`;
- `killSwitch=true`;
- a command violates the local market, margin mode, symbol, order type, notional, projected
  position, per-symbol position, daily loss, leverage, position mode, or reduce-only rules;
- `executionMode=real` while `paperOnly=true`.

`paperOnly=false` is necessary but not sufficient for real orders. OKX real order placement also
requires the exact `okx_swap_mainnet_order` adapter in the leased command and
`ytm-executor run --enable-real-orders`; without that runtime flag, `real` remains fail-closed.

## Optional Local Limits

When `killSwitch=false`, the policy can include local allowlists and caps. If an allowlist or cap is
omitted, the executor does not enforce that specific local restriction. YTM Cloud still has to pass
server-side trading profile risk before manual approval, and the executor still blocks malformed
commands, unsupported futures position mode, reduce-only mistakes, paper-only real commands, and
any limit that is configured locally.

The Docker installer has a `--wizard` mode that asks for broker credentials and a short local risk
flow. It does not ask for per-symbol local allowlists; symbols and daily limits are configured in
the YTM trading profile before approval. Press Enter on optional local notional/daily-loss caps to
skip that local cap.

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

OKX SWAP equivalent uses `okx_swap`. If `allowedSymbols` is configured manually, it must match the
command symbol from YTM. The OKX adapter maps plain USDT pairs like `BTCUSDT` to native OKX ids such as
`BTC-USDT-SWAP` before `order-precheck`. If a command contains `quantity`, OKX treats it as
contract size; otherwise the adapter can derive contract size from `orderNotional` plus
`priceReference`:

For futures commands, `leverage` is treated as an integer execution setting. The local risk gate
rounds command leverage up before comparing it with `maxLeverage`; the OKX real adapter sends the
same rounded value as `lever` to `account/set-leverage` before `order-precheck` and `trade/order`.

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
risk state before any future broker adapter can run. When a local allowlist or cap is configured,
command payloads cannot raise that limit, disable the kill switch, add markets, add margin modes,
change position mode, add symbols, add order types, or turn off paper-only mode.

If local risk passes, the executor still has to normalize the command into a broker adapter order
request with deterministic `clientOrderId`. Invalid adapter requests fail closed before any broker
adapter can run.

Heartbeat reports only a sanitized summary: configured/enabled/kill-switch/paper-only flags, limit
presence, and counts. It does not upload the full policy or local risk state.
