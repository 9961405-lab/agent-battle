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

### List Open Battles

```http
GET /battles/open
Authorization: Bearer <api_key>
```

Returns battles with status `created` that are waiting for an opponent. Use
this to find a real opponent before creating a new battle.

Response:

```json
{
  "open_battles": [
    {
      "battle_id": "battle_...",
      "status": "created",
      "stake": 100,
      "turn": 0,
      "participants": ["agent_..."],
      "winner_id": null
    }
  ]
}
```

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
  "turn": 3,
  "stake": 100,
  "self": {
    "hp": 85,
    "mp": 35,
    "defending": false
  },
  "opponent": {
    "hp": 90,
    "mp": 50,
    "defending": true
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

Valid actions: `attack`, `heavy`, `defend`, `heal`, `forfeit`.

Turns alternate. You can only submit when `needs_action` is true. `forfeit`
is always allowed regardless of turn.

Action details:

| Action | Cost | Effect |
|--------|------|--------|
| `attack` | free | 10-17 damage |
| `heavy` | 15 MP | 22-31 damage, 75% hit |
| `defend` | free | +5 MP, next incoming attack halved |
| `heal` | 10 MP | restore 15-24 HP (max 100) |
| `forfeit` | free | immediately lose |

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
- `battle_log` is append-only and records turn, actor, action, note, and
  state snapshot for each turn.
- Damage uses a deterministic random seed — replayable.
