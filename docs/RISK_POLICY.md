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
- a command violates the local symbol, order type, notional, projected position, daily loss, or
  leverage limits;
- `executionMode=real` while `paperOnly=true`.

The current foundation build still rejects all `real` commands after risk preflight because real
broker order adapters are not enabled yet.

## Required Local Limits

When `killSwitch=false`, the policy must include:

- `allowedSymbols`;
- `allowedOrderTypes`;
- `maxOrderNotional`;
- `maxPositionNotional`;
- `maxDailyLoss`;
- `maxLeverage`.

Example:

```json
{
  "allowedOrderTypes": ["limit"],
  "allowedSymbols": ["BTCUSDT"],
  "enabled": true,
  "killSwitch": false,
  "maxDailyLoss": "250",
  "maxLeverage": "1",
  "maxOrderNotional": "1000",
  "maxPositionNotional": "5000",
  "paperOnly": true,
  "version": 1
}
```

Create it through the CLI:

```bash
ytm-executor risk init --kill-switch-off \
  --allow-symbol BTCUSDT \
  --allow-order-type limit \
  --max-order-notional 1000 \
  --max-position-notional 5000 \
  --max-daily-loss 250 \
  --max-leverage 1
```

Docker install equivalent:

```bash
cd /opt/ytm-executor
sudo docker compose run --rm ytm-executor risk init --kill-switch-off \
  --allow-symbol BTCUSDT \
  --allow-order-type limit \
  --max-order-notional 1000 \
  --max-position-notional 5000 \
  --max-daily-loss 250 \
  --max-leverage 1
```

Inspect:

```bash
ytm-executor risk show
```

## Cloud Cannot Relax It

YTM Cloud sends an approved command. The executor independently loads its local policy and local
risk state before any future broker adapter can run. Command payloads cannot raise limits, disable
the kill switch, add symbols, add order types, or turn off paper-only mode.

If local risk passes, the executor still has to normalize the command into a broker adapter order
request with deterministic `clientOrderId`. Invalid adapter requests fail closed before any broker
adapter can run.

Heartbeat reports only a sanitized summary: configured/enabled/kill-switch/paper-only flags, limit
presence, and counts. It does not upload the full policy or local risk state.
