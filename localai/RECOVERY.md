# LocalAI — Recovery Runbook (no AI required)

This is a standalone runbook for when you can't reach Claude or Robot. It assumes you're
on **dees-workbench** with a shell. Everything below is plain `docker` / `curl`.

- **Service:** LocalAI (OpenAI-compatible local model server), running as Docker container `local-ai`.
- **Endpoint:** `http://192.168.226.183:8080` on the LAN (or `http://127.0.0.1:8080` on the box).
- **Auth:** required. The key lives in `/cloud-mirror/toadies/localai/.env` as `LOCALAI_API_KEY=...`.
- **Compose file:** `/cloud-mirror/toadies/localai/docker-compose.yml`.
- **Models stored in:** `/cloud-mirror/toadies/localai/models/`.

> The `.env` file is **gitignored** — the key is NOT on GitHub. It exists only on this box.
> If you lose `.env`, the key is gone; just set a new one (see "Rotate the key").

---

## See your API key

```bash
cat /cloud-mirror/toadies/localai/.env
```

## Make an authenticated call (sanity check)

```bash
cd /cloud-mirror/toadies/localai
set -a; . ./.env; set +a          # loads LOCALAI_API_KEY into your shell

curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LOCALAI_API_KEY" \
  -d '{"model":"llama-3.2-1b-instruct:q4_k_m","messages":[{"role":"user","content":"say ok"}]}'
```

A call **without** the `Authorization` header returns `401 Unauthorized` — that's expected.

## Point a program / Toadie at it

Anything OpenAI-compatible: base URL `http://192.168.226.183:8080/v1`, the model id above,
and the bearer key. The Toadies' own client (`toadies/localai.py`) reads `LOCALAI_API_KEY`
from the environment automatically, so export it (or `set -a; . localai/.env; set +a`).

---

## "I'm locked out" — three escalating fixes

### 1. Rotate the key (you have shell, just want a fresh key)

```bash
cd /cloud-mirror/toadies/localai
NEWKEY=$(openssl rand -hex 32)
printf 'LOCALAI_API_KEY=%s\n' "$NEWKEY" > .env
chmod 600 .env
docker compose up -d        # recreates the container with the new key
echo "new key: $NEWKEY"
```

### 2. Recreate a lost .env

If `.env` is missing, `docker compose up` will fail on purpose with a message about
`LOCALAI_API_KEY`. Just do step 1 — it writes a new `.env` and brings the service up.

### 3. Turn auth OFF entirely (nuclear option)

Edit `docker-compose.yml` and delete (or comment out) this line under `environment:`:

```yaml
      - API_KEY=${LOCALAI_API_KEY:?set LOCALAI_API_KEY in localai/.env (see RECOVERY.md)}
```

Then recreate:

```bash
cd /cloud-mirror/toadies/localai && docker compose up -d
```

The endpoint is now open to anyone on the LAN with no key. Re-add the line later to lock it again.

---

## Container management

```bash
docker ps --filter name=local-ai                 # is it running?
docker logs --tail 50 local-ai                    # recent logs
cd /cloud-mirror/toadies/localai
docker compose restart                            # restart
docker compose down                               # stop & remove container (models persist)
docker compose up -d                              # start again
```

First call after a (re)start takes ~5s while the model loads into RAM; warm calls ~1s.

## Reinstall the model (if models/ got wiped)

```bash
cd /cloud-mirror/toadies/localai
set -a; . ./.env; set +a
curl -s http://127.0.0.1:8080/models/apply \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LOCALAI_API_KEY" \
  -d '{"id":"llama-3.2-1b-instruct:q4_k_m"}'
# watch /v1/models until it appears:
curl -s http://127.0.0.1:8080/v1/models -H "Authorization: Bearer $LOCALAI_API_KEY"
```

## Go back to loopback-only (un-expose from the LAN)

In `docker-compose.yml`, change the port line back to:

```yaml
    ports:
      - "127.0.0.1:8080:8080"
```

Then `docker compose up -d`. Now only this box can reach it.
