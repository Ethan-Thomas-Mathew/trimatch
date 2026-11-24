# Deploying TriMatch

This is a **checklist for a real deployment**. Nothing here has been done: there is no hosted demo, because publishing needs your hosting account and your decisions about cost and exposure.

## Before anything is exposed to a network
1. **Put TLS in front.** The reviewer login is HTTP Basic; over plain HTTP the password is readable on the wire. Use a platform load balancer or a reverse proxy with a certificate.
2. **Set real secrets** (never commit them): `REVIEWER_PASSWORD` (long and random), `POSTGRES_PASSWORD`, and the Langfuse secrets if you use it. The defaults in `.env.example` are for localhost only.
3. **Use a managed Postgres** with backups, and set `DATABASE_URL` to it. Do not publish port 5432.
4. **Keep the API private.** Only the frontend (nginx) should be reachable; the backend has no published port in `docker-compose.yml`.
5. **Do not use the generated password mode.** It prints the password to the logs, which is acceptable on a laptop and not in a shared logging system.
6. **Decide the model.** Options: a model server you run privately (set `OLLAMA_HOST` and, deliberately, `ALLOW_REMOTE_LLM=true`), or nothing (uploads then queue for human review with the reason "language model unavailable"). A public upload endpoint backed by paid inference lets strangers run up your bill; add rate limiting and a spend cap first.
7. **Seed data.** `SEED_DEMO_DATA=true` loads synthetic vendors and POs only into an empty database; set it to `false` for real data.

## Suggested hosts
Any platform that runs Docker images and offers managed Postgres works (Render, Railway, Fly.io). Build the images with the commands in `.github/workflows/ci.yml` (`docker build -f backend/Dockerfile .` and `docker build frontend`), set the environment variables above, and point the frontend's `/api` proxy (`frontend/nginx.conf`, `proxy_pass`) at the backend service name on that platform.

## After deploying
- `GET /health` (liveness) and `GET /ready` (database) need no login and are for health checks.
- Logs are one JSON object per line with a `request_id`.
- Add the live URL and a demo video to the README; both are marked as missing today.
