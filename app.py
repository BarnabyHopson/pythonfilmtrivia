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

# Try to import and instantiate Anthropic client
anthropic_client = None
try:
    from anthropic import Anthropic
    if ANTHROPIC_API_KEY:
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        logger.info("Anthropic client created.")
    else:
        logger.warning("ANTHROPIC_API_KEY not set; Anthropic disabled.")
except Exception as e:
    anthropic_client = None
    logger.exception("Could not initialize Anthropic client: %s", e)

# Flask app
app = Flask(__name__, template_folder="templates")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search_movies")
def search_movies():
    """Search TMDB for movies by query string (autocomplete)."""
    query = request.args.get("query", "").strip()
    if not query:
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
                    "year": (movie.get("release_date") or "")[:4],
                    "poster": f"https://image.tmdb.org/t/p/w200{movie['poster_path']}"
                    if movie.get("poster_path")
                    else None,
                }
            )
        return jsonify(results)
    except Exception as e:
        logger.exception("Exception during TMDB search: %s", e)
        return jsonify([])


def _clean_fact_line(line: str) -> str:
    """Strip list markers and whitespace from a line."""
    cleaned = re.sub(r'^\s*(?:\d+[\.\)]\s*|[-–•\u2022\*]\s*)', '', line)
    return cleaned.strip()


@app.route("/get_movie_facts")
def get_movie_facts():
    """Get movie details from TMDB, then trivia from Anthropic."""
    movie_id = request.args.get("movie_id", "").strip()
    if not movie_id:
        return jsonify({"error": "Missing movie_id"}), 400

    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not set.")
        return jsonify({"error": "Server not configured (TMDB missing)"}), 500

    # 1) Fetch movie details
    tmdb_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    try:
        tmdb_resp = requests.get(
            tmdb_url,
            params={"api_key": TMDB_API_KEY, "language": "en-US"},
            timeout=6,
        )
        if tmdb_resp.status_code != 200:
            logger.error("TMDB details error for id %s: %s", movie_id, tmdb_resp.text)
            return jsonify({"error": "Failed to fetch movie details"}), 500

        movie = tmdb_resp.json()
        title = movie.get("title") or movie.get("original_title") or "Unknown Title"
        year = (movie.get("release_date") or "")[:4]
        poster = (
            f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
            if movie.get("poster_path")
            else None
        )
    except Exception as e:
        logger.exception("Exception fetching TMDB details: %s", e)
        return jsonify({"error": "Failed to fetch movie details"}), 500

    # 2) If no Anthropic client, return placeholder
    if not anthropic_client:
        logger.warning("Anthropic client not available.")
        return jsonify({
            "title": title,
            "year": year,
            "poster": poster,
            "facts": [f"Anthropic not configured on server. Movie: {title} ({year})."]
        })

    # 3) Build prompt and call Anthropic
    prompt = (
        f"Give me between 6 and 12 absolutely fascinating, little-known trivia facts "
        f"about the movie \"{title}\" ({year}).\n\n"
        "⚡ Requirements:\n"
        "- Focus on surprising, mind-blowing, or unusual facts.\n"
        "- Avoid obvious info (like cast, director, or sequels).\n"
        "- Use behind-the-scenes, production quirks, cultural impact, or rare trivia.\n"
        "- Keep each fact short (1–3 sentences).\n"
        "- Return as a clean list, one fact per line, no extra commentary."
    )

    try:
        response = anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
        )

        raw_text = ""
        if hasattr(response, "content") and response.content:
            first = response.content[0]
            raw_text = getattr(first, "text", None) or (
                first.get("text") if isinstance(first, dict) else ""
            )
        raw_text = (raw_text or "").strip()
        logger.info("Anthropic raw response: %s", raw_text[:400])

        # Split into lines
        lines = [l.strip() for l in re.split(r'\r?\n', raw_text) if l.strip()]
        facts = [_clean_fact_line(l) for l in lines if _clean_fact_line(l)]

        if not facts:
            return jsonify({"error": "No facts generated"}), 500

        # Cap at 12
        facts = facts[:12]

        return jsonify({"title": title, "year": year, "poster": poster, "facts": facts})
    except Exception as e:
        logger.exception("Anthropic request/parse error: %s", e)
        return jsonify({"error": "Problem generating trivia"}), 500


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "anthropic": bool(anthropic_client),
        "tmdb_key": bool(TMDB_API_KEY)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
