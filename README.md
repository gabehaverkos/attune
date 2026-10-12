# Attune

Attune is a personal assistant you talk to. It remembers what you tell it, shows
you what it has learned and where it learned it, and helps plan your week on a
calendar. Nothing it proposes happens until you approve it.

Try it at [attune.haverkos.dev](https://attune.haverkos.dev): the demo needs no
account, and starts either empty or as an example person a couple of weeks in.

## In this repository

- [backend/](backend/) is the API, the agent that replies in chat, and
  memory: Python, FastAPI, and Postgres with pgvector.
- [web/](web/) is the app in the browser, and sign-in: SvelteKit and
  Better Auth.
- [dev/](dev/) is the development stack: the script that runs it, and what it
  lays over `docker-compose.yml`.
- [backup/](backup/) is the service that dumps a server's database daily.

`docker-compose.yml` at the root is the whole stack, and what a server runs.

## Running it in development

Everything needs Docker Compose 2.24.4 or later. Copy `.env.example` to `.env`
and fill in every value: the file says what each one is and where it comes from.

```powershell
.\dev\attune.ps1 dev           # build and start
.\dev\attune.ps1 dev status
.\dev\attune.ps1 dev logs
.\dev\attune.ps1 dev stop      # stop, keeping the containers
.\dev\attune.ps1 dev down      # remove the containers, keeping the data
.\dev\attune.ps1 dev wipe      # remove the containers and the data
```

That is the root `docker-compose.yml` with `dev/docker-compose.dev.yml` over it:
Postgres with pgvector, the API, the memory worker, the web app and Caddy, at
`https://localhost`. The source is mounted, so Python and frontend changes
reload; a change to dependencies or a Dockerfile is picked up the next time
`dev` is run. The backend applies its migrations as it starts. The first time,
the browser asks to accept the certificate Caddy issued itself, which `wipe`
keeps.

The web app, the API and Postgres are also published on loopback (ports 5173,
8000 and 5432) for tools on the same machine.

- To sign in with Google, set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and
  authorize `https://localhost/api/auth/callback/google` on the OAuth client.
- Without `RESEND_API_KEY`, the links that verify an email or reset a password
  are not sent: the web service logs each one, to be opened from `dev logs`.
- Tracing goes to the Langfuse Cloud project the two `LANGFUSE_*` keys name, and
  is off without them. In development it includes what was said; on a server it
  never does.

Which model does which job is decided in `backend/app/config.py`, not in the
environment. Prices are in `MODEL_PRICES` beside the choices: traces show each
call's cost from them and the daily allowance charges by them, so change both
together. Embedding settings are in `backend/app/memory/graph.py`; changing them
leaves memory's stored vectors made by the old ones, and nothing makes them
again.
