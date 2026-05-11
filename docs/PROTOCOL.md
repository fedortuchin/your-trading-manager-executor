# Executor Protocol

The protocol is intentionally zero-secret.

## Enrollment

```text
POST /api/executor/enroll
```

Request contains:

- `enrollmentToken`;
- `clientVersion`;
- `capabilities`;
- `allowedEgress`.

Response contains:

- executor metadata;
- one machine `accessToken`.

The enrollment token is one-time. The access token authenticates future executor requests to YTM
Cloud. It is not a broker credential.

## Heartbeat

```text
POST /api/executor/heartbeat
Authorization: Bearer <executor access token>
```

Heartbeat may report non-secret local credential metadata:

```json
{
  "clientVersion": "0.6.0",
  "heartbeatStatus": "online",
  "capabilities": {
    "leases": true,
    "localCredentials": [
      {
        "name": "main",
        "provider": "tbank"
      }
    ],
    "localRiskPolicy": {
      "allowedOrderTypeCount": 1,
      "allowedSymbolCount": 2,
      "configured": true,
      "enabled": true,
      "killSwitch": false,
      "limits": {
        "maxDailyLoss": true,
        "maxOrderNotional": true,
        "maxPositionNotional": true
      },
      "paperOnly": true
    }
  }
}
```

It must not report actual broker tokens, API secrets, or full local risk files. Risk heartbeat data
is a sanitized summary only.

## Lease

```text
POST /api/executor/commands/lease
Authorization: Bearer <executor access token>
```

YTM returns either `item: null` or one command for the executor's broker account.

The executor repeats local checks after leasing. A provider-backed command is rejected locally when
the local risk policy is missing, disabled, incomplete, kill-switched, paper-only for `real`, or the
command violates local market, margin mode, instrument, order type, notional, projected position,
per-symbol exposure, daily loss, leverage, position mode, or reduce-only limits.

After risk preflight, the executor normalizes the command into a broker adapter order request. The
request must include provider, symbol, side, position effect, order type, quantity or notional, and
a deterministic `clientOrderId`. If YTM did not provide `clientOrderId`, the executor derives a
bounded id from provider plus command id. The current foundation build uses a disabled adapter and
reports `order_placement_skipped`.

For Binance `real`, a command may explicitly request
`commandPayload.adapter=binance_usdm_futures_mainnet_order_test`. The executor then uses the local
Binance credential and the official `binance-sdk-derivatives-trading-usds-futures` package to call
USD-M Futures mainnet `exchangeInfo`, normalize price/quantity to symbol filters, check min/max
quantity, price, and notional, and call `test_order`. This is a validation-only broker call and
does not place an order; after the validate-only call, the executor still rejects placement with
`real_execution_disabled` until real order adapters are explicitly enabled.

For OKX `external_paper` or `real`, a command may explicitly request
`commandPayload.adapter=okx_swap_mainnet_order_precheck`. The executor then uses the local OKX
credential and `python-okx` to read SWAP instrument rules, normalize contract size and price, and
call `POST /api/v5/trade/order-precheck`. Plain USDT symbols such as `BTCUSDT` are mapped to OKX
SWAP ids such as `BTC-USDT-SWAP`; if `quantity` is absent, contract size is derived from
`orderNotional` and `priceReference`. This is a validation-only broker call and does not place an
order. `external_paper` returns `acknowledged` with `order_placement_skipped`; `real` still
rejects with `real_execution_disabled` after this validate-only adapter. OKX documents
`order-precheck` as Trade-permission and applicable to multi-currency margin mode and portfolio
margin mode.

For OKX `real`, a command may explicitly request
`commandPayload.adapter=okx_swap_mainnet_order`. This adapter is disabled unless the executor was
started with `--enable-real-orders`. When enabled, the executor still repeats local risk checks,
normalizes the order, calls OKX `order-precheck`, and only then calls `POST /api/v5/trade/order`.
The sanitized result uses `executorAction=order_submitted` and includes `providerOrderId`; broker
secrets and raw authorization data are never uploaded to YTM.

## Result

```text
POST /api/executor/commands/{commandId}/result
Authorization: Bearer <executor access token>
```

Result payload must be sanitized. Secret-like keys are rejected by both executor client and YTM
server.

## Reconciliation Snapshot

```text
POST /api/executor/reconciliation/snapshots
Authorization: Bearer <executor access token>
```

Request contains:

- `snapshotType`: `account`, `orders`, `fills`, `positions`, or `full`;
- `status`: `ok`, `drift`, `reconciliation_required`, or `error`;
- `executionMode`: optional override, otherwise YTM uses the executor's broker account mode;
- `providerSnapshotId`: optional provider-side snapshot id;
- `payload`: sanitized provider state.

Broker secrets, API keys, Authorization headers, and token-like fields are rejected before upload.
The executor can upload a caller-provided JSON snapshot or capture an OKX SWAP read-only snapshot
itself with `ytm-executor reconciliation capture-okx`. OKX capture calls `account/balance`,
`account/positions`, and `trade/orders-pending`, normalizes balances, positions, and open orders,
and uploads that sanitized state to YTM. `ytm-executor run --reconcile-okx` performs the same
capture periodically. The foundation records snapshots and drift status; full provider fill
ingestion remains later work.
