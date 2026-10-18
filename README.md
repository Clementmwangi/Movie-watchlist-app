# CinePair

Private movie/TV assignment app for two people. Django + Tailwind (CDN) + Alpine.js + TMDB.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TMDB_API_KEY=your_tmdb_key      # v3 key or v4 token
export TIME_ZONE=Africa/Nairobi        # decides when "this week" rolls over
python manage.py migrate
python manage.py runserver
python manage.py test                  # 15 tests
```
Sign up, create a space, send the invite code to your partner, who signs up and joins with it.

## Deploy (Render / Railway)
Build: `./build.sh` · Start: `gunicorn config.wsgi` · Set the variables in `.env.example`.
**Use a managed Postgres (`DATABASE_URL`).** SQLite on these hosts lives on an ephemeral disk and will be wiped on redeploy.
