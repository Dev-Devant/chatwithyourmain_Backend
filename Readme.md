# Chat With Your Main — Backend

> Open source backend powering [Chat With Your Main](https://chatwithyourmain.andresrosalez.dev/): real League of Legends data + an AI chat that talks back in character as your most-played champion.

Built with **FastAPI** and async Python, this service integrates the official **Riot Games API**, analyzes match history to build a player profile, and feeds that context into an **OpenAI**-powered in-character chat — with **PostgreSQL** for persistence and **Redis** for chat caching.

---

## ✨ Live API

🔗 **Frontend consuming this backend:** [chatwithyourmain.andresrosalez.dev](https://chatwithyourmain.andresrosalez.dev/)

🔗 **Frontend repository:** [Dev-Devant/ChatWithYourMain](https://github.com/Dev-Devant/ChatWithYourMain)

---

## ✦ What It Does

```text
Riot ID → Account-V1 / Summoner-V4 → Champion-Mastery-V4 → Match-V5
        → Player profile & recent-match context → OpenAI chat (in character)
```

1. **Resolve** a Riot ID (`Name#TAG`) into a `puuid` via Account-V1.
2. **Fetch** summoner info and top champion masteries.
3. **Analyze** the player's last matches (KDA, role, items, win/loss, notable games) into a compact text context.
4. **Persist** every searched summoner in PostgreSQL.
5. **Chat**: an LLM responds as the chosen champion, using the player's real match context, with conversation history cached in Redis (no need for the frontend to resend it on every request).

---

## 🚀 Engineering Highlights

* Riot Games API integration (Account-V1, Summoner-V4, Champion-Mastery-V4, Match-V5)
* Async HTTP with `httpx`, fully non-blocking
* Match history analytics: KDA, CS/min, team totals, lane opponent detection, notable-game flagging
* Prompt engineering for consistent in-character LLM responses
* PostgreSQL persistence with upsert + search-count tracking
* Redis-backed chat history caching with TTL (sliding expiration)
* Graceful degradation: chat keeps working even if Redis is unreachable
* In-memory short-TTL cache for player context (reduces Riot API calls)
* CORS-ready FastAPI app, deployed on Railway with Gunicorn + Uvicorn workers

---

## 🗂️ Project Structure

```text
.
├── main.py            # FastAPI app, routes, request/response models
├── riot.py            # Summoner + mastery orchestration, player-context caching
├── riot_client.py     # Low-level Riot API calls, region/continent routing
├── match_history.py   # Match-V5 processing, KDA/CS/notable-game analytics
├── ia.py               # System prompt building + OpenAI chat completion
├── db.py               # PostgreSQL pool, summoners table, upsert logic
├── redis_client.py    # Redis pool, chat history get/append/clear with TTL
├── requirements.txt
└── railway.json
```

### Architecture

| Layer        | File              | Responsibility                                      |
| ------------ | ----------------- | ---------------------------------------------------- |
| API          | `main.py`         | Routes, validation, orchestration                    |
| Riot domain  | `riot.py` / `riot_client.py` | Summoner & mastery lookups, region routing |
| Analytics    | `match_history.py`| Match parsing, stats, notable-game detection         |
| AI           | `ia.py`           | Prompt construction, OpenAI call                     |
| Persistence  | `db.py`           | PostgreSQL: stores searched summoners                |
| Caching      | `redis_client.py` | Redis: caches chat history per `puuid` + champion    |

---

## 🔧 Local Development

### Requirements

* Python 3.12+
* A Riot Games API key
* An OpenAI API key
* PostgreSQL and Redis instances (local or remote)

### Setup

```bash
git clone https://github.com/Dev-Devant/chatwithyourmain_Backend.git
cd chatwithyourmain_Backend
pip install -r requirements.txt
```

Create a `.env` file (loaded via `python-dotenv`):

```env
RIOT_API_KEY=your-riot-api-key
OPENAI_API_KEY=your-openai-api-key
DB_PASS=your-postgres-password
REDIS_URL=redis://default:password@host:port
```

> The PostgreSQL connection currently targets a fixed host/user (`postgres.railway.internal` / `postgres` / `railway`), matching a Railway-managed Postgres instance. Adjust `db.py` if you're connecting to a different host.

### Run

```bash
uvicorn main:app --reload --port 8000
```

API available at:

```text
http://localhost:8000
```

---

## 📡 API Reference

| Method | Endpoint              | Description                                              |
| ------ | ---------------------- | ---------------------------------------------------------- |
| GET    | `/health`              | Health check                                                |
| GET    | `/api/summoner`        | Look up a summoner by Riot ID + region; persists the result to PostgreSQL |
| POST   | `/api/summoner`        | Same as above, JSON body                                    |
| GET    | `/api/summoners`       | List recently searched summoners (demo/inspection endpoint) |
| POST   | `/api/chat`            | Send a message to the AI champion; reads/writes history in Redis |
| GET    | `/api/chat/history`    | Retrieve cached chat history for a `puuid` + `championId`   |
| DELETE | `/api/chat/history`    | Clear cached chat history                                    |

### Example: search a summoner

```bash
curl "http://localhost:8000/api/summoner?summoner_name=Hide%20on%20bush%23KR1&region=KR"
```

### Example: chat

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
        "championId": "Ahri",
        "championName": "Ahri",
        "championTitle": "the Nine-Tailed Fox",
        "persona": "Playful, sly, a little mischievous.",
        "message": "How did my last game go?",
        "puuid": "PUUID_FROM_SUMMONER_LOOKUP",
        "region": "KR"
      }'
```

---

## 🛢️ Data Stores

### PostgreSQL — `summoners` table

Tracks every summoner ever searched: name, tag, region, icon, level, search count, and timestamps. Created automatically on startup (`db.py`). Used to demonstrate persistence and basic usage analytics.

### Redis — chat cache

Keys formatted as `chat:{puuid}:{championId}`, storing the last messages exchanged with each champion. TTL defaults to 30 minutes and resets on every new message (sliding expiration), so the frontend no longer needs to resend the full conversation history on each request.

---

## ☁️ Deployment

Deployed on [Railway](https://railway.app/) using Nixpacks + Gunicorn with a Uvicorn worker (see `railway.json`):

```json
{
  "deploy": {
    "startCommand": "gunicorn main:app -k uvicorn.workers.UvicornWorker -w 1 --timeout 60"
  }
}
```

PostgreSQL and Redis run as separate Railway services within the same project, referenced via environment variables.

---

## 👤 Author

Made by **[Andres Rosalez](https://andresrosalez.dev/)** — AI Backend Developer.

💼 Portfolio: [andresrosalez.dev](https://andresrosalez.dev/)

💬 LinkedIn: [linkedin.com/in/andres-rosalez](https://www.linkedin.com/in/andres-rosalez)

📸 Instagram: [@andres_rosalez](https://www.instagram.com/andres_rosalez)

📧 Email: [andeliros@yahoo.com.ar](mailto:andeliros@yahoo.com.ar)

---

## ⚖️ Riot Games Disclaimer

Chat With Your Main is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties.

League of Legends and Riot Games are trademarks or registered trademarks of Riot Games, Inc.

This project uses the Riot Games API in compliance with Riot Developer Policies.

---

## 📄 License

**All Rights Reserved.**

This repository is public for portfolio and demonstration purposes only. No permission is granted to copy, modify, redistribute, or use this code — commercially or otherwise — without explicit written permission from the author.

See the [`LICENSE`](./LICENSE) file for details.