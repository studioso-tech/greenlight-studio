"""
Greenlight Studio - Dataset Generator
Generates realistic historical movie box office data, script embeddings, cast performance, and weekly trend metrics.
"""
import os
import sys
import json
import uuid
import random
import numpy as np
import pandas as pd
from typing import List, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# Set random seed for reproducible realistic data
np.random.seed(42)
random.seed(42)

GENRES = ["Action", "Sci-Fi", "Drama", "Comedy", "Thriller", "Horror", "Adventure", "Animation", "Romance", "Crime", "Fantasy", "Mystery"]
RATINGS = ["G", "PG", "PG-13", "R"]
VFX_LEVELS = ["Low", "Medium", "High", "Extreme"]

FAMOUS_DIRECTORS = [
    ("Christopher Nolan", "Sci-Fi", 3.8, 92),
    ("Denis Villeneuve", "Sci-Fi", 3.2, 88),
    ("James Cameron", "Sci-Fi", 4.5, 95),
    ("Steven Spielberg", "Adventure", 3.6, 91),
    ("Greta Gerwig", "Drama", 4.1, 89),
    ("Jordan Peele", "Horror", 5.2, 87),
    ("Quentin Tarantino", "Crime", 3.4, 86),
    ("Rian Johnson", "Mystery", 3.5, 84),
    ("Guillermo del Toro", "Fantasy", 2.9, 85),
    ("Bong Joon-ho", "Thriller", 4.3, 90),
    ("Chad Stahelski", "Action", 3.9, 82),
    ("Taika Waititi", "Comedy", 3.1, 80),
]

FAMOUS_ACTORS = [
    ("Timothée Chalamet", "Sci-Fi", 3.4, 88),
    ("Zendaya", "Drama", 3.7, 90),
    ("Leonardo DiCaprio", "Drama", 3.5, 94),
    ("Florence Pugh", "Thriller", 3.2, 85),
    ("Tom Cruise", "Action", 4.2, 96),
    ("Margot Robbie", "Comedy", 3.9, 91),
    ("Keanu Reeves", "Action", 3.6, 89),
    ("Daniel Kaluuya", "Horror", 4.8, 86),
    ("Ryan Gosling", "Comedy", 3.0, 87),
    ("Ana de Armas", "Mystery", 3.1, 83),
    ("Cillian Murphy", "Drama", 3.8, 88),
    ("Emily Blunt", "Sci-Fi", 3.3, 86),
]

SAMPLE_MOVIES_BASE = [
    {
        "title": "Interstellar Horizon",
        "genres": ["Sci-Fi", "Drama", "Adventure"],
        "mpaa_rating": "PG-13",
        "budget": 165_000_000,
        "box_office_domestic": 188_000_000,
        "box_office_worldwide": 701_000_000,
        "rotten_tomatoes_score": 73,
        "vfx_intensity": "Extreme",
        "synopsis": "A team of explorers travel through a wormhole in space in an attempt to ensure humanity's survival as Earth faces catastrophic famine.",
    },
    {
        "title": "Neon Syndicate",
        "genres": ["Action", "Sci-Fi", "Crime"],
        "mpaa_rating": "R",
        "budget": 40_000_000,
        "box_office_domestic": 45_000_000,
        "box_office_worldwide": 120_000_000,
        "rotten_tomatoes_score": 86,
        "vfx_intensity": "Medium",
        "synopsis": "In a rain-soaked dystopian metropolis, a rogue cybernetic detective uncovers a conspiracy among elite AI corporate overlords.",
    },
    {
        "title": "Shadows of the Bayou",
        "genres": ["Horror", "Mystery", "Thriller"],
        "mpaa_rating": "R",
        "budget": 15_000_000,
        "box_office_domestic": 68_000_000,
        "box_office_worldwide": 145_000_000,
        "rotten_tomatoes_score": 91,
        "vfx_intensity": "Low",
        "synopsis": "A young documentary filmmaker investigates mysterious disappearances in rural Louisiana swamps, awakening an ancient folk terror.",
    },
    {
        "title": "Quantum Heist",
        "genres": ["Action", "Sci-Fi", "Thriller"],
        "mpaa_rating": "PG-13",
        "budget": 85_000_000,
        "box_office_domestic": 110_000_000,
        "box_office_worldwide": 290_000_000,
        "rotten_tomatoes_score": 79,
        "vfx_intensity": "High",
        "synopsis": "A team of quantum physicists and elite thieves attempt to steal an experimental timeline-manipulating device from a fortress.",
    },
    {
        "title": "The Glass Symphony",
        "genres": ["Drama", "Romance"],
        "mpaa_rating": "PG-13",
        "budget": 22_000_000,
        "box_office_domestic": 42_000_000,
        "box_office_worldwide": 95_000_000,
        "rotten_tomatoes_score": 94,
        "vfx_intensity": "Low",
        "synopsis": "A gifted conductor recovering from hearing loss finds unexpected inspiration and romance with an avant-garde acoustic architect in Vienna.",
    }
]

def generate_embedding(genre_list: List[str], tone_seed: int, dim: int = 768) -> List[float]:
    """Generates a clustered 768-dimensional normalized embedding vector based on genres."""
    rng = np.random.default_rng(seed=tone_seed)
    vec = rng.normal(0, 1, dim)
    
    # Add genre-specific cluster bias
    for g in genre_list:
        g_idx = GENRES.index(g) if g in GENRES else 0
        vec[g_idx * 50 : (g_idx + 1) * 50] += 2.5
        
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist()

def generate_movies(count: int = 2500) -> List[Dict[str, Any]]:
    movies = []
    
    # 1. Base curated sample movies
    for i, base in enumerate(SAMPLE_MOVIES_BASE):
        m_id = str(uuid.uuid4())
        year = random.randint(2010, 2025)
        month = random.randint(1, 12)
        emb = generate_embedding(base["genres"], tone_seed=1000 + i)
        movies.append({
            "movie_id": m_id,
            "title": base["title"],
            "release_year": year,
            "release_month": month,
            "genres": base["genres"],
            "mpaa_rating": base["mpaa_rating"],
            "budget": base["budget"],
            "box_office_domestic": base["box_office_domestic"],
            "box_office_worldwide": base["box_office_worldwide"],
            "rotten_tomatoes_score": base["rotten_tomatoes_score"],
            "vfx_intensity": base["vfx_intensity"],
            "synopsis": base["synopsis"],
            "script_embedding": emb,
        })
        
    # 2. Procedurally generated realistic movie catalogue
    prefixes = ["The Last", "Chronicles of", "Echoes in", "Beyond", "Project", "Secret of", "Return to", "Silent", "Lost", "Infinite", "Edge of", "Dark"]
    nouns = ["Odyssey", "Protocol", "Horizon", "Empire", "Mirror", "City", "Enigma", "Voyage", "Memory", "Realm", "Paradox", "Legacy", "Signal", "Alliance"]
    
    for i in range(len(movies), count):
        m_id = str(uuid.uuid4())
        g_count = random.choices([1, 2, 3], weights=[0.3, 0.5, 0.2])[0]
        selected_genres = random.sample(GENRES, g_count)
        
        rating = random.choices(RATINGS, weights=[0.05, 0.25, 0.45, 0.25])[0]
        vfx = random.choices(VFX_LEVELS, weights=[0.3, 0.35, 0.25, 0.1])[0]
        
        # Budget logic based on genre & VFX
        base_budget = random.randint(5, 50) * 1_000_000
        if "Sci-Fi" in selected_genres or "Action" in selected_genres:
            base_budget = random.randint(35, 220) * 1_000_000
        elif "Horror" in selected_genres:
            base_budget = random.randint(4, 30) * 1_000_000
            
        budget = int(base_budget)
        
        # ROI multiplier calculation with realistic variance
        # Horror often high ROI, Sci-Fi high variance
        if "Horror" in selected_genres:
            roi = np.random.lognormal(mean=1.2, sigma=0.6)
        elif "Sci-Fi" in selected_genres or "Action" in selected_genres:
            roi = np.random.lognormal(mean=0.8, sigma=0.7)
        else:
            roi = np.random.lognormal(mean=0.7, sigma=0.5)
            
        worldwide = int(budget * max(0.15, roi))
        domestic = int(worldwide * random.uniform(0.3, 0.55))
        rt_score = int(np.clip(np.random.normal(68, 18), 10, 99))
        
        year = random.randint(1995, 2025)
        month = random.randint(1, 12)
        title = f"{random.choice(prefixes)} {random.choice(nouns)} {random.choice(['', 'II', 'Origins', 'Rising', 'Redemption', 'Zero']) if random.random() < 0.25 else ''}".strip()
        
        synopsis = f"A compelling {', '.join(selected_genres)} narrative exploring high stakes, emotional depth, and unexpected twists as protagonists confront formidable odds."
        emb = generate_embedding(selected_genres, tone_seed=i + 5000)
        
        movies.append({
            "movie_id": m_id,
            "title": f"{title} ({year})",
            "release_year": year,
            "release_month": month,
            "genres": selected_genres,
            "mpaa_rating": rating,
            "budget": budget,
            "box_office_domestic": domestic,
            "box_office_worldwide": worldwide,
            "rotten_tomatoes_score": rt_score,
            "vfx_intensity": vfx,
            "synopsis": synopsis,
            "script_embedding": emb,
        })
        
    return movies

def generate_cast_analytics() -> List[Dict[str, Any]]:
    cast = []
    # Add famous directors
    for name, genre, roi, score in FAMOUS_DIRECTORS:
        cast.append({
            "person_id": str(uuid.uuid4()),
            "name": name,
            "role_type": "director",
            "primary_genre": genre,
            "avg_roi_multiplier": float(roi),
            "box_office_power_score": float(score),
            "avg_worldwide_gross": int(roi * 90_000_000),
        })
    # Add famous actors
    for name, genre, roi, score in FAMOUS_ACTORS:
        cast.append({
            "person_id": str(uuid.uuid4()),
            "name": name,
            "role_type": "actor",
            "primary_genre": genre,
            "avg_roi_multiplier": float(roi),
            "box_office_power_score": float(score),
            "avg_worldwide_gross": int(roi * 75_000_000),
        })
    return cast

def generate_weekly_trends(movies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    trends = []
    # Sample 300 movies for granular weekly box office trajectories
    sampled = random.sample(movies, min(300, len(movies)))
    for m in sampled:
        total_ww = m["box_office_worldwide"]
        # Typical 8-week theatrical run
        week_1 = int(total_ww * random.uniform(0.35, 0.45))
        screens = random.randint(1500, 4200)
        current_gross = week_1
        
        trends.append({
            "movie_id": m["movie_id"],
            "week_number": 1,
            "weekly_gross": current_gross,
            "screen_count": screens,
            "drop_percentage": 0.0,
        })
        
        for w in range(2, 9):
            drop = random.uniform(0.38, 0.58)
            current_gross = int(current_gross * (1.0 - drop))
            screens = int(screens * random.uniform(0.85, 0.98))
            trends.append({
                "movie_id": m["movie_id"],
                "week_number": w,
                "weekly_gross": max(10_000, current_gross),
                "screen_count": max(100, screens),
                "drop_percentage": float(round(drop * 100, 2)),
            })
    return trends

def main():
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    
    print("🎬 Generating realistic movie datasets for Greenlight Studio...")
    movies = generate_movies(count=2500)
    cast = generate_cast_analytics()
    trends = generate_weekly_trends(movies)
    
    # Save as JSON
    movies_file = os.path.join(data_dir, "movies_historical.json")
    cast_file = os.path.join(data_dir, "cast_analytics.json")
    trends_file = os.path.join(data_dir, "weekly_trends.json")
    
    with open(movies_file, "w", encoding="utf-8") as f:
        json.dump(movies, f, ensure_ascii=False, indent=2)
    with open(cast_file, "w", encoding="utf-8") as f:
        json.dump(cast, f, ensure_ascii=False, indent=2)
    with open(trends_file, "w", encoding="utf-8") as f:
        json.dump(trends, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Generated {len(movies)} movies -> {movies_file}")
    print(f"✅ Generated {len(cast)} cast members -> {cast_file}")
    print(f"✅ Generated {len(trends)} weekly trend points -> {trends_file}")

if __name__ == "__main__":
    main()
