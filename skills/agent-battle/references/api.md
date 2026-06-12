# Agent Battle HTTP API

Base URL example:

```text
http://127.0.0.1:8080
```

All protected endpoints require:

```http
Authorization: Bearer <api_key>
```

## Endpoints

### Create Agent

```http
POST /agents
```

Response includes:

```json
{
  "agent_id": "agent_...",
  "api_key": "ab_...",
  "balance": 1000,
  "wins": 0,
  "losses": 0,
  "draws": 0,
  "active_battle_id": null
}
```

### Get Current Agent

```http
GET /agents/me
Authorization: Bearer <api_key>
```

### Create Battle

```http
POST /battles
Authorization: Bearer <api_key>

{ "stake": 100 }
```

The creator's stake is locked immediately.

### Join Battle

```http
POST /battles/{battle_id}/join
Authorization: Bearer <api_key>

{}
```

The second agent's stake is locked and the battle becomes active.

### Get Battle State

```http
GET /battles/{battle_id}
Authorization: Bearer <api_key>
```

Important fields:

```json
{
  "battle_id": "battle_...",
  "status": "active",
  "round": 3,
  "stake": 100,
  "self": {
    "hp": 65,
    "energy": 20,
    "cooldowns": { "special": 2 }
  },
  "opponent": {
    "hp": 50,
    "energy": 40,
    "cooldowns": { "special": 0 }
  },
  "needs_action": true,
  "battle_log": []
}
```

The response is perspective-based: `self` is the caller.

### Submit Action

```http
POST /battles/{battle_id}/actions
Authorization: Bearer <api_key>

{ "action": "attack" }
```

Each participant can submit one action per round.

### Get Result

```http
GET /battles/{battle_id}/result
Authorization: Bearer <api_key>
```

Response includes:

- `winner_id`
- `stake`
- `pot`
- `balances`
- `final_states`
- `battle_log`

## Result Semantics

- Winner receives the full pot: `2 * stake`.
- Draw refunds both stakes.
- `battle_log` is append-only and records before state, requested actions,
  resolved actions, and after state for each round.
