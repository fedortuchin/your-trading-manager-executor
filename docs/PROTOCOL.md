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
  "clientVersion": "0.2.0",
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
command violates local instrument, order type, notional, projected position, daily loss, or leverage
limits.

After risk preflight, the executor normalizes the command into a broker adapter order request. The
request must include provider, symbol, side, position effect, order type, quantity or notional, and
a deterministic `clientOrderId`. If YTM did not provide `clientOrderId`, the executor derives a
bounded id from provider plus command id. The current foundation build uses a disabled adapter and
reports `order_placement_skipped`.

For Binance `external_paper`, a command may explicitly request
`commandPayload.adapter=binance_spot_testnet_order_test`. The executor then uses the local Binance
credential and the official `binance-sdk-spot` package to call Spot Testnet `order_test`. This is a
validation-only broker call and does not place an order.

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
The foundation records snapshots and drift status; full provider fill ingestion remains later work.
