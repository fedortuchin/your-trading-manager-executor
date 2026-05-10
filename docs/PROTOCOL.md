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
  "clientVersion": "0.1.0",
  "heartbeatStatus": "online",
  "capabilities": {
    "leases": true,
    "localCredentials": [
      {
        "name": "main",
        "provider": "tbank"
      }
    ]
  }
}
```

It must not report actual broker tokens or API secrets.

## Lease

```text
POST /api/executor/commands/lease
Authorization: Bearer <executor access token>
```

YTM returns either `item: null` or one command for the executor's broker account.

## Result

```text
POST /api/executor/commands/{commandId}/result
Authorization: Bearer <executor access token>
```

Result payload must be sanitized. Secret-like keys are rejected by both executor client and YTM
server.
