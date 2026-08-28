# Security posture

Written 2026-08-28, from what the machine was actually doing rather than from
what the compose file appeared to say. This is the "decided, not deferred"
record the release goal asked for.

## What was wrong

Every service published its port on `0.0.0.0` — reachable from every device on
whatever network this machine joined:

| Service | Port | Auth in front of it | What reaching it gets you |
|---|---|---|---|
| core-logic | 5003 | none | drive the FSM, write course content |
| rag-engine | 5002 | none | course CRUD, delete courses, the review queue |
| research | 5006 | none | trigger outbound fetches |
| tts / stt | 5005 / 5001 | none | synthesis and transcription |
| **sqlcheck** | **55432** | password `x` | **a live PostgreSQL** |

The last one is the worst and was also the most pointless: `sql_ground_truth.py`
reaches that engine through `docker exec ... psql`, so the published port was
never used by anything.

## What was done

**Internal services bound to loopback.** `127.0.0.1:5002`, `:5003`, `:5005`,
`:5006`, `:5001`. Services reach each other over the compose network by
container name, so the app does not need host mappings at all; host-side tooling
(`main.py` health checks, `tools/night_audit.py`, `tools/tier_probe.py`) does,
and that runs on this machine. Verified after the change: every page still 200s
and the review queue still serves.

**The Postgres port is gone entirely**, and the `docker run` line the code
prints when the container is missing no longer includes `-p`.

## What is deliberately still exposed

**web-ui on `0.0.0.0:5050`.** This is the page a person opens, and reaching it
from a phone on the same network is a real use — the UI is built and tested for
375px. It carries no authentication, so on an untrusted network anyone who can
reach the machine can read and delete courses.

If this machine will be on a shared network, change the mapping in
`docker-compose.yml` to `127.0.0.1:5050:5000` and browse only from the machine
itself. That is a one-line change and costs the phone use.

## What is NOT solved

Authentication. Nothing in this system authenticates a request; the loopback
binding removes the network reach but not the underlying absence. Anything with
local code execution on this machine can still call every API. For a
single-user, single-machine tutor that is a reasonable place to stop; it would
not be if this were ever hosted for more than one person.
