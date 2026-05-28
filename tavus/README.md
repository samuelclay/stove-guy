# Stove Guy ↔ Tavus persona wiring

A Tavus persona drives the virtual-camera cooking guide through **one generic
server-webhook tool**. Auto slides advance on their own timers; the deck **holds**
at each hands-on step (pour / flip / veggies / cheese / close). The persona calls
the tool to release each gate, and the tool's response tells the persona what to
coach next and when.

```
persona  --(calls advance_stove)-->  Tavus RQH  --(POST, delivery.api)-->
   https://<tunnel>/api/tavus/advance  -->  camera server  -->  presentation.next()
   <--(now_showing, pan_temp_f, next_action, next_action_in_seconds, coach)--
persona narrates the result (on_resolve = generate_response)
```

## Pieces

- **Webhook receiver** — `camera/app/server.py`
  - `POST /api/tavus/advance` — release the current gate (or start on first call);
    no-op if mid auto-segment so an early call can't skip a step. Returns the next
    gate + ETA + a ready-to-speak `coach` line.
  - `GET  /api/tavus/state` — same payload, read-only (debugging).
  - `POST /api/tavus/reset` — reload the deck, park on the start step.
- **Gate model** — `camera/decks/omelette/deck.json`. Manual slides are the gates;
  each carries a `cue` (the coaching line). Gates: Hot oil (pour), Mostly-cooked
  eggs (flip), Flipped eggs (veggies), Veggies slightly cooked (cheese), Cheese
  melted (close).
- **The tool** — `tavus/advance_stove.tool.json` (`POST /v2/tools` body shape).
  `delivery.api.url` must point at the **current** tunnel URL.
- **Starter system prompt** — `tavus/system_prompt.md` (edit in the builder).

## Live resources (PROD, created via the CLI)

| What | ID |
|------|----|
| Persona "Stove Guy" | `p349e7709c23` |
| Tool `advance_stove` | `tafd00c13fca6` |
| Replica (stock "Charlie", swap freely) | `rf4703150052` |

Edit the persona at `https://persona-builder.tavus-preview.io` (or the dev portal).
If the tunnel URL changes, re-patch the tool's `delivery.api.url`:

```bash
# edit tavus/advance_stove.tool.json with the new url, then:
tavus/tavus-prod.sh tool patch tafd00c13fca6 --file /Users/sclay/projects/stove-guy/tavus/advance_stove.tool.json
```

## The tunnel (cloudflared)

The webhook needs a public HTTPS URL. A quick tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

⚠️ Quick-tunnel URLs are **ephemeral** — a new one each run. When it changes,
update `delivery.api.url` in `advance_stove.tool.json` and re-patch the tool
(`tavus tool patch`). For a stable URL, use a named cloudflared tunnel or ngrok
with a reserved domain.

Current tunnel: `https://size-promote-treasury-roll.trycloudflare.com`

## Build it (CLI, against production)

`tavus/tavus-prod.sh` runs the CLI against prod (overrides tavus-mcp/.env's
localhost config; ignores any stale `TAVUS_API_KEY`).

```bash
# 1. one-time auth (opens persona-builder.tavus-preview.io to mint a prod key)
tavus/tavus-prod.sh auth login
tavus/tavus-prod.sh persona list        # verify

# 2. create the webhook tool  -> note the tool_id
tavus/tavus-prod.sh tool create --file tavus/advance_stove.tool.json

# 3. create the persona  -> note the persona_id
tavus/tavus-prod.sh persona create \
  --name "Stove Guy" \
  --system-prompt "$(cat tavus/system_prompt.md)" \
  --replica-id <stock_replica_id>

# 4. attach the tool to the persona
tavus/tavus-prod.sh persona tools attach <persona_id> <tool_id>
```

Then edit the persona at `persona-builder.tavus-preview.io` and start a
conversation. Point a call app at the **OBS Virtual Camera** so the persona sees
the stovetop.
