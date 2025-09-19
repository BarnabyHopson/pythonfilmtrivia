import os
import re
import logging
from flask import Flask, render_template, request, jsonify
import requests
from dotenv import load_dotenv

# Load .env in local/dev (Render will use actual env vars)
load_dotenv()

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("movie-trivia-app")

# Config / env
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Try to import and instantiate Anthropic client if available
anthropic_client = None
try:
    # Use the official modern import; if it fails we'll log and keep going
    from anthropic import Anthropic
    if ANTHROPIC_API_KEY:
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        logger.info("Anthropic client created.")
    else:
        logger.warning("ANTHROPIC_API_KEY not set; Anthropic disabled.")
except Exception as e:
    anthropic_client = None
    logger.exception("Could not initialize Anthropic client (will run without): %s", e)


# Flask app
app = Flask(__name__, template_folder="templates")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search_movies")
def search_movies():
    """Search TMDB for movies by query string (autocomplete)."""
    query = request.args.get("query", "").strip()
    if not query or len(query) < 1:
        return jsonify([])

    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not set.")
        return jsonify([])

    url = "https://api.themoviedb.org/3/search/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "query": query,
        "language": "en-US",
        "include_adult": "false",
        "page": 1,
    }

    try:
        resp = requests.get(url, params=params, timeout=6)
        if resp.status_code != 200:
            logger.error("TMDB search error: %s", resp.text)
            return jsonify([])

        data = resp.json()
        results = []
        for movie in data.get("results", []):
            results.append(
                {
                    "id": movie.get("id"),
                    "title": movie.get("title") or movie.get("original_title"),
                    "year": (movie.get("release_date") or "")[:4] if movie.get("release_date") else "",
                    "poster": f"https://image.tmdb.org/t/p/w200{movie['poster_path']}" if movie.get("poster_path") else None,
                }
            )
        return jsonify(results)
    except Exception as e:
        logger.exception("Exception during TMDB search: %s", e)
        return jsonify([])


def _clean_fact_line(line: str) -> str:
    """Strip common list markers and whitespace from a line."""
    # remove leading bullets / numbers like "1. " or "• " or "- "
    cleaned = re.sub(r'^\s*(?:\d+[\.\)]\s*|[-–•\u2022\*]\s*)', '', line)
    return cleaned.strip()


@app.route("/get_movie_facts")
def get_movie_facts():
    """
    Get movie details from TMDB, then generate short trivia facts via Anthropic.
    Returns JSON:
    { title, year, poster, facts: [ ... ] }
    """
    movie_id = request.args.get("movie_id", "").strip()
    if not movie_id:
        return jsonify({"error": "Missing movie_id"}), 400

    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not set.")
        return jsonify({"error": "Server not configured (TMDB missing)"}), 500

    # 1) Fetch movie details
    tmdb_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    try:
        tmdb_resp = requests.get(tmdb_url, params={"api_key": TMDB_API_KEY, "language": "en-US"}, timeout=6)
        if tmdb_resp.status_code != 200:
            logger.error("TMDB details error for id %s: %s", movie_id, tmdb_resp.text)
            return jsonify({"error": "Failed to fetch movie details"}), 500

        movie = tmdb_resp.json()
        title = movie.get("title") or movie.get("original_title") or "Unknown Title"
        year = (movie.get("release_date") or "")[:4] if movie.get("release_date") else ""
        poster = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get("poster_path") else None
    except Exception as e:
        logger.exception("Exception fetching TMDB details: %s", e)
        return jsonify({"error": "Failed to fetch movie details"}), 500

    # 2) If no Anthropic client, return basic info (so UI still works)
    if not anthropic_client:
        logger.warning("Anthropic client not available; returning placeholder message.")
        return jsonify({
            "title": title,
            "year": year,
            "poster": poster,
            "facts": [f"Anthropic not configured on server. Movie: {title} ({year})."]
        })

    # 3) Build prompt and call Anthropic
    prompt = (
        f"Provide 3 concise, verifiable trivia facts about the movie \"{title}\" ({year}).\n"
        "Return the facts as a simple list, one fact per line. Do not include extra explanation or numbering.\n"
        "Keep each fact short (1-2 sentences) and avoid speculation."
    )

    try:
        # Use the Messages API style
        response = anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )

        # Try several ways to extract text from the response (robust across SDK versions)
        raw_text = None

        # 1) response.content[0].text (common)
        try:
            cont = getattr(response, "content", None)
            if cont:
                if isinstance(cont, (list, tuple)) and len(cont) > 0:
                    first = cont[0]
                    raw_text = getattr(first, "text", None) or (first.get("text") if isinstance(first, dict) else None)
        except Exception:
            raw_text = None

        # 2) response.content as list of dicts
        if not raw_text:
            try:
                if isinstance(response, dict) and "content" in response and isinstance(response["content"], list) and len(response["content"]) > 0:
                    raw_text = response["content"][0].get("text")
            except Exception:
                raw_text = None

        # 3) fallback: response.text or str(response)
        if not raw_text:
            raw_text = getattr(response, "text", None) or getattr(response, "completion", None) or str(response)

        raw_text = (raw_text or "").strip()
        logger.info("Anthropic raw response: %s", raw_text[:1000])

        # 4) split into lines and clean them
        lines = [l.strip() for l in re.split(r'\r?\n', raw_text) if l.strip()]
        facts = []
        for ln in lines:
            cleaned = _clean_fact_line(ln)
            if cleaned:
                facts.append(cleaned)

        # 5) if no facts found, try sentence-splitting as a fallback
        if not facts:
            # naive sentence split
            sentences = [s.strip() for s in re.split(r'(?<=[\.\?\!])\s+', raw_text) if s.strip()]
            for s in sentences:
                cleaned = _clean_fact_line(s)
                if cleaned:
                    facts.append(cleaned)
                if len(facts) >= 3:
                    break

        if not facts:
            logger.warning("No facts parsed from Anthropic response.")
            return jsonify({"error": "No facts could be generated"}), 500

        # Trim to top 6 just in case
        facts = facts[:6]

        return jsonify({"title": title, "year": year, "poster": poster, "facts": facts})
    except Exception as e:
        logger.exception("Anthropic request/parse error: %s", e)
        return jsonify({"error": "Problem generating trivia"}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "anthropic": bool(anthropic_client), "tmdb_key": bool(TMDB_API_KEY)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
