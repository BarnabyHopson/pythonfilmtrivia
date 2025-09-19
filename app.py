from flask import Flask, render_template, request, jsonify
import requests
import os
from dotenv import load_dotenv
import anthropic

# Load environment variables
load_dotenv()

app = Flask(__name__)

# API Keys from environment variables
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Anthropic client
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search_movies")
def search_movies():
    """Search TMDB for movies by query string"""
    query = request.args.get("query")
    if not query:
        return jsonify([])

    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query, "language": "en-US"}
    resp = requests.get(url, params=params)

    if resp.status_code != 200:
        print("TMDB search error:", resp.text)
        return jsonify([])

    data = resp.json()
    results = []
    for movie in data.get("results", []):
        results.append(
            {
                "id": movie.get("id"),
                "title": movie.get("title"),
                "year": movie.get("release_date", "")[:4],
                "poster": f"https://image.tmdb.org/t/p/w200{movie['poster_path']}"
                if movie.get("poster_path")
                else None,
            }
        )

    return jsonify(results)


@app.route("/get_movie_facts")
def get_movie_facts():
    """Fetch movie details + generate trivia facts"""
    movie_id = request.args.get("movie_id")
    if not movie_id:
        return jsonify({"error": "Missing movie_id"}), 400

    # Step 1: Get movie details from TMDB
    tmdb_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    tmdb_params = {"api_key": TMDB_API_KEY, "language": "en-US"}
    tmdb_resp = requests.get(tmdb_url, params=tmdb_params)

    if tmdb_resp.status_code != 200:
        print("TMDB details error:", tmdb_resp.text)
        return jsonify({"error": "Failed to fetch movie details"}), 500

    movie = tmdb_resp.json()
    title = movie.get("title")
    year = movie.get("release_date", "")[:4]
    poster = (
        f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
        if movie.get("poster_path")
        else None
    )

    # Step 2: Generate trivia using Anthropic
    try:
        prompt = f"Give me 3 fascinating trivia facts about the movie '{title}' ({year}). " \
                 "Keep each fact short, fun, and unique. Return them as a simple list."

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        # ✅ Fix: properly extract text from Anthropic response
        raw_text = ""
        if response.content:
            raw_text = response.content[0].text.strip()
        print("Anthropic raw response:", raw_text)

        # Split into facts (one per line or bullet)
        facts = [f.strip(" -•") for f in raw_text.split("\n") if f.strip()]

        if not facts:
            return jsonify({"error": "No facts could be generated"}), 500

        return jsonify({"title": title, "year": year, "poster": poster, "facts": facts})

    except Exception as e:
        print("Anthropic error:", str(e))
        return jsonify({"error": "Problem generating trivia"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
