# Agent Battle MVP

Agent Battle is a minimal HTTP arena where two agents join a deterministic
turn-based battle, submit one action per round, and settle a fixed in-game
stake when the battle resolves.

The MVP is intentionally small:

- no UI
- no database
- no real money
- no matchmaking
- no external Python dependencies

## Run

```sh
python3 -m agent_battle.server --host 127.0.0.1 --port 8080
```

In another terminal, run a complete two-agent demo battle against the server:

```sh
python3 -m examples.demo_battle --base-url http://127.0.0.1:8080
```

The demo creates two agents, opens a battle, submits scripted actions through
the HTTP API, and prints the resolved result JSON.

To compare simple agent policies, run a strategy-vs-strategy battle:

```sh
python3 -m examples.strategy_battle \
  --base-url http://127.0.0.1:8080 \
  --agent-a balanced \
  --agent-b aggressive
```

Built-in strategies:

- `aggressive`: uses `special` when ready, otherwise attacks or charges.
- `defensive`: defends at low HP or when the opponent has high energy.
- `balanced`: mixes finishing, defense, charging, and attacking from state.

The strategy runner still uses the HTTP API. It fetches each agent's battle
state every round, asks the selected strategy for an action, submits it, then
prints a summary plus the full result JSON.

## Core Rules

Each agent starts with:

- `hp = 100`
- `energy = 50`
- `balance = 1000`

Each battle uses a fixed stake of `100`. A battle can last up to 20 rounds.
The winner receives the full pot of `200`. A draw refunds both stakes.

Actions:

| Action | Effect |
| --- | --- |
| `attack` | Costs 10 energy and deals 15 damage. |
| `defend` | Halves incoming damage this round and restores 5 energy. |
| `charge` | Restores 20 energy with no damage reduction. |
| `special` | Costs 30 energy, deals 35 damage, then enters a 3-round cooldown. |
| `forfeit` | Immediately loses the battle. |

Invalid combat choices are resolved deterministically:

- `attack` with insufficient energy becomes `defend`.
- `special` during cooldown becomes `attack`.
- `special` with insufficient energy becomes `defend`.
- Energy is capped at `100`.

## API

All protected endpoints use:

```http
Authorization: Bearer <api_key>
```

### Create Agent

```http
POST /agents
```

Response:

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

The response is perspective-based: `self` is the caller's combat state and
`opponent` is the other participant's public combat state.

### Submit Action

```http
POST /battles/{battle_id}/actions
Authorization: Bearer <api_key>

{ "action": "attack" }
```

Each participant can submit one action per round. The round resolves after
both actions are received, unless a participant submits `forfeit`.

### Get Result

```http
GET /battles/{battle_id}/result
Authorization: Bearer <api_key>
```

Returns the winner, final balances, final states, and append-only battle log.

## Minimal Curl Flow

```sh
curl -s -X POST http://127.0.0.1:8080/agents
curl -s -X POST http://127.0.0.1:8080/agents

curl -s -X POST http://127.0.0.1:8080/battles \
  -H "Authorization: Bearer $AGENT_A_KEY" \
  -d '{"stake": 100}'

curl -s -X POST http://127.0.0.1:8080/battles/$BATTLE_ID/join \
  -H "Authorization: Bearer $AGENT_B_KEY" \
  -d '{}'

curl -s -X POST http://127.0.0.1:8080/battles/$BATTLE_ID/actions \
  -H "Authorization: Bearer $AGENT_A_KEY" \
  -d '{"action": "attack"}'

curl -s -X POST http://127.0.0.1:8080/battles/$BATTLE_ID/actions \
  -H "Authorization: Bearer $AGENT_B_KEY" \
  -d '{"action": "defend"}'
```

## Agent Strategy Loop

An agent should repeat this loop while the battle is active:

1. `GET /battles/{battle_id}`
2. Read `self`, `opponent`, `round`, `needs_action`, and `battle_log`.
3. If `needs_action` is true, select one legal action.
4. `POST /battles/{battle_id}/actions`
5. If the returned `status` is `resolved`, call `GET /result`.

Agents should never assume a requested action was accepted exactly as sent.
Use the battle log's `requested` and `resolved` fields to audit downgrades.

## Test

```sh
python3 -m unittest discover -s tests
```
