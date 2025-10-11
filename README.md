# SleepFix

A small local demo that collects simple sleep logs, persists them to disk, and generates AI-powered coaching insights using the Hugging Face Router API.

## Prerequisites
- Python 3.10+
- A Hugging Face access token (set as `HF_TOKEN` or `HF_API_TOKEN`)

## Quickstart (Windows PowerShell)

1) Create and activate a virtual environment (recommended):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2) Install backend dependencies:

```powershell
pip install -r backend\requirements.txt
```

3) Configure the Hugging Face token (choose one):

- Set in the current PowerShell session:

```powershell
$Env:HF_TOKEN = '<your-hf-token-here>'     # or set HF_API_TOKEN
$Env:FLASK_DEBUG = 'true'                  # optional
```

- Or create a `.env` file under `backend/` and add `HF_TOKEN=...` (we load `.env` automatically).

4) Start the backend (the Flask app serves the frontend so you don't need to open the raw HTML file):

```powershell
cd D:\Projects\SleepFix\backend
python app.py
```

- The app will run on http://127.0.0.1:5000.
- Health check: http://127.0.0.1:5000/health

5) Open the demo in your browser:

- Visit http://127.0.0.1:5000 and use the UI to create a user ID, post sleep logs (enter at least 3 entries), and click "Analyze My Sleep".

## Persistence

This project uses a simple file-backed store located at `backend/data/`:

- `backend/data/sleep_logs.json` — stores submitted logs keyed by user ID
- `backend/data/ai_insights.json` — stores generated AI insights keyed by user ID

Files are created automatically when the app first runs and are written to after new logs or insights are produced.

## API (useful for automation / tests)

- POST /log/{user_id}
  - Create a sleep log for `user_id`.
  - Body (JSON):
    - `duration` (float, hours, required)
    - `bedtime` (string `HH:MM`, required)
    - `stress_level` (int 1–10, required)
    - `wake_time` (string `HH:MM`, optional)
    - `caffeine_intake` (float mg, optional)
    - `screen_time` (float hours, optional)

- GET /sleep-logs/{user_id}
  - Retrieve stored logs for `user_id`.

- POST /analyze/{user_id}
  - Trigger the AI analysis for `user_id` using stored logs (requires at least 3 logs).

- GET /latest-insight/{user_id}
  - Retrieve the last generated AI insight for `user_id`.

Example PowerShell automation (after starting the server):

```powershell
# Post 3 sample logs for 'testuser'
$logs = @(
  @{ duration=7.5; bedtime='23:15'; stress_level=4; caffeine_intake=50; screen_time=1.5 },
  @{ duration=6.0; bedtime='00:30'; stress_level=7; caffeine_intake=80; screen_time=2.0 },
  @{ duration=5.75; bedtime='00:45'; stress_level=6; caffeine_intake=40; screen_time=3.5 }
)
foreach ($p in $logs) {
  Invoke-RestMethod -Uri 'http://127.0.0.1:5000/log/testuser' -Method Post -ContentType 'application/json' -Body ($p | ConvertTo-Json)
}

# Trigger AI analysis
Invoke-RestMethod -Uri 'http://127.0.0.1:5000/analyze/testuser' -Method Post

# Fetch latest insight
Invoke-RestMethod -Uri 'http://127.0.0.1:5000/latest-insight/testuser' -Method Get | ConvertTo-Json -Depth 6
```

## Notes & next steps

- The app currently uses file-backed JSON for persistence — for production use, consider migrating to a small database (SQLite or Postgres).
- The AI model is configured via the `HF_MODEL` environment variable if you want to override the default hosted model.
- If you hit Hugging Face errors, ensure your token is valid and that the chosen model supports the Router chat API.

If you'd like, I can add a `requirements.txt` (if missing), small unit tests for validation logic, or migrate the store to SQLite next.
