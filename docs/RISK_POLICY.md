# Local Fail-Safe Policy

Provider-backed execution has a mandatory local fail-safe inside the executor. The file lives on the executor host, not in YTM Cloud.

YTM is the source of truth for trading limits: allowed symbols, notional, leverage, max open trades, approval mode, and per-strategy overrides. The executor must not duplicate those fast-changing controls on the VPS.

## Fail-Closed Defaults

The executor rejects leased provider-backed commands when:

- `risk-policy.json` is missing;
- policy parsing fails;
- `enabled=false`;
- `killSwitch=true`;
- `executionMode=real` while `paperOnly=true`;
- a `real` command has no YTM risk attestation;
- a futures command has unsupported margin/position/reduce-only semantics;
- optional local daily or total drawdown stops are reached.

`paperOnly=false` is necessary but not sufficient for real orders. OKX real order placement also requires the exact `okx_swap_mainnet_order` adapter in the leased command and `ytm-executor run --enable-real-orders`.

## Local Drawdown Stops

The only user-configurable local limits are account-level drawdown stops:

```bash
ytm-executor risk init --kill-switch-off \
  --allow-real \
  --max-daily-loss 250 \
  --max-total-drawdown 1000
```

Both limits are optional. They are updated from executor-side reconciliation snapshots. If a configured local drawdown stop has no live state yet, real commands fail closed until reconciliation runs.

Docker install equivalent:

```bash
cd /opt/ytm-executor
sudo docker compose run --rm ytm-executor risk init --kill-switch-off \
  --allow-real \
  --max-daily-loss 250 \
  --max-total-drawdown 1000
```

Inspect:

```bash
ytm-executor risk show
```

## Cloud Cannot Relax It

YTM Cloud sends an approved command with sanitized `riskControls.source=ytm`. The executor independently loads its local fail-safe policy and local risk state before any broker adapter can run. A cloud-side command cannot disable the local kill switch, paper-only mode, or local drawdown stops.

Heartbeat reports only a sanitized summary: configured/enabled/kill-switch/paper-only flags and drawdown-limit presence. It does not upload broker secrets, the full policy file, or local risk state.
