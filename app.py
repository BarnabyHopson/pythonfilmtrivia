from flask import Flask, render_template, request, jsonify
import requests
import os
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)

# API Keys from environment variables
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search_movies')
def search_movies():
    """Search for movies as user types"""
    query = request.args.get('query', '').strip()
    
    if len(query) < 3:
        return jsonify([])
    
    try:
        # Search TMDB for movies using v4 API token
        url = f"https://api.themoviedb.org/3/search/movie"
        headers = {
            'Authorization': f'Bearer {TMDB_API_KEY}',
            'Content-Type': 'application/json'
        }
        params = {
            'query': query,
            'language': 'en-US',
            'page': 1
        }
        
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        
        # Return top 5 results with basic info
        movies = []
        for movie in data.get('results', [])[:5]:
            movies.append({
                'id': movie['id'],
                'title': movie['title'],
                'year': movie.get('release_date', '')[:4] if movie.get('release_date') else 'Unknown',
                'poster': f"https://image.tmdb.org/t/p/w92{movie['poster_path']}" if movie.get('poster_path') else None
            })
        
        return jsonify(movies)
    
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify([])

@app.route('/get_movie_facts')
def get_movie_facts():
    """Get interesting facts about a specific movie"""
    movie_id = request.args.get('movie_id')
    
    if not movie_id:
        return jsonify({'error': 'No movie selected'})
    
    try:
        # Get detailed movie info from TMDB using v4 API token
        movie_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        credits_url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits"
        
        headers = {
            'Authorization': f'Bearer {TMDB_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        movie_params = {
            'language': 'en-US',
            'append_to_response': 'keywords,videos'
        }
        
        credits_params = {
            'language': 'en-US'
        }
        
        # Get movie details and credits
        movie_response = requests.get(movie_url, headers=headers, params=movie_params)
        credits_response = requests.get(credits_url, headers=headers, params=credits_params)
        
        movie_data = movie_response.json()
        credits_data = credits_response.json()
        
        # Prepare data for Claude
        movie_info = {
            'title': movie_data.get('title', ''),
            'year': movie_data.get('release_date', '')[:4] if movie_data.get('release_date') else 'Unknown',
            'director': next((person['name'] for person in credits_data.get('crew', []) if person['job'] == 'Director'), 'Unknown'),
            'budget': movie_data.get('budget', 0),
            'revenue': movie_data.get('revenue', 0),
            'runtime': movie_data.get('runtime', 0),
            'genres': [genre['name'] for genre in movie_data.get('genres', [])],
            'overview': movie_data.get('overview', ''),
            'vote_average': movie_data.get('vote_average', 0),
            'production_companies': [company['name'] for company in movie_data.get('production_companies', [])[:3]],
            'cast': [actor['name'] for actor in credits_data.get('cast', [])[:10]]
        }
        
        # Generate facts using Claude API
        facts = generate_movie_facts(movie_info)
        
        if not facts:
            return jsonify({'error': "Sorry but I'm struggling to find enough interesting info for this movie. Please try again"})
        
        return jsonify({
            'title': movie_info['title'],
            'year': movie_info['year'],
            'poster': f"https://image.tmdb.org/t/p/w300{movie_data['poster_path']}" if movie_data.get('poster_path') else None,
            'facts': facts
        })
    
    except Exception as e:
        print(f"Movie facts error: {e}")
        return jsonify({'error': "Sorry but I'm struggling to find enough interesting info for this movie. Please try again"})

def generate_movie_facts(movie_info):
    """Use Claude API to generate interesting facts"""
    try:
        # Create prompt for Claude
        prompt = f"""Generate 7-9 really interesting, energetic facts about the movie "{movie_info['title']}" ({movie_info['year']}). 

Movie details:
- Director: {movie_info['director']}
- Budget: ${movie_info['budget']:,} 
- Box office: ${movie_info['revenue']:,}
- Runtime: {movie_info['runtime']} minutes
- Genres: {', '.join(movie_info['genres'])}
- Cast: {', '.join(movie_info['cast'][:5])}
- Production: {', '.join(movie_info['production_companies'])}
- Plot: {movie_info['overview'][:200]}...

Focus on:
- Behind-the-scenes scandals or drama
- Production problems or interesting challenges
- Revolutionary techniques or groundbreaking aspects
- Surprising casting decisions or actor stories
- Awards and recognition
- Box office surprises
- Cultural impact
- Fun trivia that movie fans would love

Make each fact 1-2 sentences maximum. Write in a friendly, energizing tone that gets people excited about movies. Return exactly this JSON format:

{
  "facts": [
    "Fact 1 here",
    "Fact 2 here",
    "Fact 3 here"
  ]
}

Only return the JSON, nothing else."""

        # Call Anthropic API
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY
        }
        
        data = {
            'model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 1000,
            'messages': [
                {
                    'role': 'user',
                    'content': prompt
                }
            ]
        }
        
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers,
            json=data
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result['content'][0]['text']
            
            # Parse JSON response
            import json
            facts_data = json.loads(content)
            return facts_data['facts']
        else:
            print(f"Anthropic API error: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Fact generation error: {e}")
        return None

if __name__ == '__main__':
    app.run(debug=True)
