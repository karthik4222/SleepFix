import os
import json
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
import threading
from datetime import datetime
import requests
from flask_cors import CORS
import statistics
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

app = Flask(__name__)
CORS(app)

# File-backed stores (simple JSON files until a DB is added)
DATA_DIR = Path(__file__).resolve().parent / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
SLEEP_LOGS_FILE = DATA_DIR / 'sleep_logs.json'
AI_INSIGHTS_FILE = DATA_DIR / 'ai_insights.json'

# Thread lock for simple concurrent safety
_store_lock = threading.Lock()


def _load_json(path: Path):
    try:
        if not path.exists():
            return {}
        with path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path: Path, data):
    # atomic-ish write
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


# Ensure initial files exist on disk (empty dicts)
with _store_lock:
    if not SLEEP_LOGS_FILE.exists():
        _save_json(SLEEP_LOGS_FILE, {})
    if not AI_INSIGHTS_FILE.exists():
        _save_json(AI_INSIGHTS_FILE, {})


# Load stores into memory
sleep_logs = _load_json(SLEEP_LOGS_FILE)
ai_insights = _load_json(AI_INSIGHTS_FILE)


# Helper utilities

def _parse_hhmm(value: str):
    try:
        return datetime.strptime(value, '%H:%M').time()
    except Exception:
        return None


def _coerce_number(n, typ=float):
    try:
        return typ(n)
    except Exception:
        return None


def validate_and_build_log_entry(data):
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object."

    required = ['duration', 'bedtime', 'stress_level']
    if not all(k in data for k in required):
        return None, "Missing required fields: duration, bedtime, stress_level"

    duration = _coerce_number(data.get('duration'), float)
    stress_level = _coerce_number(data.get('stress_level'), int)
    bedtime_raw = data.get('bedtime')
    wake_time_raw = data.get('wake_time')
    caffeine_intake = data.get('caffeine_intake', 0)
    screen_time = data.get('screen_time', 0)

    if duration is None or duration <= 0 or duration > 24:
        return None, "Field 'duration' must be a number in hours between 0 and 24."

    if stress_level is None or stress_level < 1 or stress_level > 10:
        return None, "Field 'stress_level' must be an integer between 1 and 10."

    if not isinstance(bedtime_raw, str) or _parse_hhmm(bedtime_raw) is None:
        return None, "Field 'bedtime' must be a string in 'HH:MM' format."
    bedtime = bedtime_raw

    wake_time = None
    if wake_time_raw is not None:
        if not isinstance(wake_time_raw, str) or _parse_hhmm(wake_time_raw) is None:
            return None, "Field 'wake_time' must be a string in 'HH:MM' format if provided."
        wake_time = wake_time_raw

    if caffeine_intake is None:
        caffeine_intake = 0
    caffeine_intake = _coerce_number(caffeine_intake, float)
    if caffeine_intake is None or caffeine_intake < 0:
        return None, "Field 'caffeine_intake' must be a non-negative number if provided."

    if screen_time is None:
        screen_time = 0
    screen_time = _coerce_number(screen_time, float)
    if screen_time is None or screen_time < 0:
        return None, "Field 'screen_time' must be a non-negative number if provided."

    log_entry = {
        "date": datetime.utcnow().strftime('%Y-%m-%d'),
        "duration": float(duration),
        "bedtime": bedtime,
        "wake_time": wake_time,
        "caffeine_intake": float(caffeine_intake),
        "stress_level": int(stress_level),
        "screen_time": float(screen_time)
    }

    return log_entry, None


def calculate_metrics(logs):
    if not logs:
        return {}
    durations = [log.get('duration', 0) for log in logs if isinstance(log.get('duration'), (int, float))]
    bedtimes = []
    for log in logs:
        bt = log.get('bedtime')
        if isinstance(bt, str):
            try:
                bedtimes.append(datetime.strptime(bt, '%H:%M'))
            except Exception:
                pass
    stress_levels = [log.get('stress_level', 0) for log in logs if isinstance(log.get('stress_level'), (int, float))]

    avg_duration = round(statistics.mean(durations), 2) if durations else 0
    if len(bedtimes) > 1:
        bedtime_minutes = [bt.hour * 60 + bt.minute for bt in bedtimes]
        bedtime_std_dev = round(statistics.stdev(bedtime_minutes), 2)
    else:
        bedtime_std_dev = 0
    avg_stress = round(statistics.mean(stress_levels), 2) if stress_levels else 0

    return {
        "average_sleep_duration": avg_duration,
        "bedtime_consistency_minutes": bedtime_std_dev,
        "average_stress_level": avg_stress,
        "total_logs": len(logs)
    }


# Hugging Face router chat helper
def call_hf_chat_model(messages, model_name):
    api_url = "https://router.huggingface.co/v1/chat/completions"
    # Accept HF_API_TOKEN or HF_TOKEN for compatibility with different .envs
    hf_token = os.environ.get("HF_API_TOKEN") or os.environ.get("HF_TOKEN")
    if not hf_token:
        print("Missing Hugging Face API token (HF_API_TOKEN or HF_TOKEN).")
        return None
    headers = {"Authorization": f"Bearer {hf_token}", "Content-Type": "application/json"}
    payload = {"messages": messages, "model": model_name}
    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        choices = result.get("choices") or []
        if not choices:
            return None
        return choices[0].get("message", {}).get("content")
    except Exception as e:
        # If we have a response object, print its status and body for debugging
        try:
            if 'resp' in locals() and resp is not None:
                try:
                    print(f"Router error (status {resp.status_code}):", resp.json())
                except Exception:
                    print(f"Router error (status {resp.status_code}):", resp.text)
        except Exception:
            pass
        print(f"Error calling Hugging Face chat: {e}")
        return None


# AI analysis - modular multi-step reasoning
def analyze_sleep_patterns_with_ai(user_id, user_logs):
    if not user_logs or len(user_logs) < 3:
        return {"error": "Insufficient data for analysis. Please log at least 3 days of sleep.", "code": "insufficient_data"}

    metrics = calculate_metrics(user_logs)
    # Default to a known hosted model (DeepSeek) which supports router chat
    model_name = os.environ.get("HF_MODEL", "deepseek-ai/DeepSeek-V3.2-Exp:novita")

    prompt_factors = f"""
You are a sleep data analyst. Given the following sleep logs and metrics, identify the top 1-2 factors most likely impacting sleep quality. For each factor, provide a confidence level (High, Medium, Low). Respond ONLY with a JSON array of objects: {{"factor": string, "confidence": string}}.

Sleep Logs:
{json.dumps(user_logs, indent=2)}

Metrics:
{json.dumps(metrics, indent=2)}
"""
    messages_factors = [{"role": "user", "content": prompt_factors}]
    factors_text = call_hf_chat_model(messages_factors, model_name)
    if factors_text is None:
        return {"error": "Failed to identify impact factors.", "code": "ai_provider_error"}
    try:
        identified_factors = json.loads(factors_text)
    except Exception:
        identified_factors = [{"factor": "Unknown", "confidence": "Low", "raw": factors_text}]

    prompt_recommend = f"""
You are a sleep coach. Given these impact factors and metrics, generate a single, personalized, empathetic coaching tip focusing on the most critical area for improvement, and predict a sleep improvement score (1-10, 1=poor, 10=excellent). Respond ONLY with a JSON object: {{"coaching_tip": string, "sleep_improvement_score": int}}.

Impact Factors:
{json.dumps(identified_factors, indent=2)}

Metrics:
{json.dumps(metrics, indent=2)}
"""
    messages_recommend = [{"role": "user", "content": prompt_recommend}]
    recommend_text = call_hf_chat_model(messages_recommend, model_name)
    if recommend_text is None:
        return {"error": "Failed to generate recommendation.", "code": "ai_provider_error"}
    try:
        recommend_data = json.loads(recommend_text)
    except Exception:
        recommend_data = {"coaching_tip": recommend_text, "sleep_improvement_score": 5}

    insight = {
        "user_id": user_id,
        "generated_at": datetime.utcnow().isoformat(),
        "metrics": metrics,
        "ai_analysis": {
            "identified_factors": identified_factors,
            "coaching_tip": recommend_data.get("coaching_tip", "No tip generated."),
            "sleep_improvement_score": recommend_data.get("sleep_improvement_score", 5)
        }
    }
    ai_insights[user_id] = insight
    return insight


# Routes
@app.route('/log/<user_id>', methods=['POST'])
def post_sleep_log(user_id):
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON payload."}), 400

    log_entry, error = validate_and_build_log_entry(data)
    if error:
        return jsonify({"error": error}), 400

    user_logs = sleep_logs.setdefault(user_id, [])
    user_logs.append(log_entry)
    # persist to disk
    try:
        with _store_lock:
            _save_json(SLEEP_LOGS_FILE, sleep_logs)
    except Exception as e:
        print(f"Warning: failed to persist sleep_logs to disk: {e}")

    return jsonify({"message": "Log entry added.", "log_entry": log_entry}), 201


@app.route('/sleep-logs/<user_id>', methods=['GET'])
def get_sleep_logs(user_id):
    """Return the list of sleep logs for a user."""
    logs = sleep_logs.get(user_id)
    if not logs:
        return jsonify({"message": "No logs found"}), 404
    return jsonify({"logs": logs})


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    """Serve the frontend files from ../frontend directory."""
    frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
    if path == '' or path == 'index.html':
        return send_from_directory(frontend_dir, 'index.html')
    # Serve other static files if requested
    return send_from_directory(frontend_dir, path)


@app.route('/analyze/<user_id>', methods=['POST'])
def trigger_ai_analysis(user_id):
    user_logs = sleep_logs.get(user_id, [])
    if not user_logs or len(user_logs) < 3:
        return jsonify({"error": "Insufficient data for analysis. Please log at least 3 days of sleep."}), 400
    insight = analyze_sleep_patterns_with_ai(user_id, user_logs)
    # persist insights after generation
    try:
        with _store_lock:
            ai_insights[user_id] = insight
            _save_json(AI_INSIGHTS_FILE, ai_insights)
    except Exception as e:
        print(f"Warning: failed to persist ai_insights to disk: {e}")
    return jsonify(insight), 200


@app.route('/latest-insight/<user_id>', methods=['GET'])
def get_latest_insight(user_id):
    insight = ai_insights.get(user_id)
    if not insight:
        return jsonify({"message": "No AI insight found for this user. Please generate one first."}), 404
    return jsonify(insight)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() in {"1", "true", "yes"}
    app.run(debug=debug_mode, port=5000)
