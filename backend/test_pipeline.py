"""
Test script to verify Greenlight Studio Phase 1 pipeline:
1. Dataset generation
2. ClickHouse client & Local fallback memory
3. FastMCP tool functions execution (Vector similarity & Statistical aggregations)
"""
import os
import sys
import json
import time

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure backend path is available
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.scripts.generate_dataset import generate_movies, generate_cast_analytics, generate_weekly_trends
from backend.mcp_server.ch_client import ch_client
from backend.mcp_server.server import (
    search_similar_movies,
    get_genre_roi_distribution,
    get_cast_roi_recommendations,
    run_custom_box_office_query
)

def run_tests():
    print("=" * 60)
    print("[TEST] Starting Greenlight Studio Phase 1 Test Suite")
    print("=" * 60)
    
    # 1. Test Dataset Generation
    print("\n[1/4] Generating Sample Dataset...")
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    
    movies = generate_movies(count=500)
    cast = generate_cast_analytics()
    trends = generate_weekly_trends(movies)
    
    movies_path = os.path.join(data_dir, "movies_historical.json")
    cast_path = os.path.join(data_dir, "cast_analytics.json")
    trends_path = os.path.join(data_dir, "weekly_trends.json")
    
    with open(movies_path, "w", encoding="utf-8") as f:
        json.dump(movies, f, indent=2, ensure_ascii=False)
    with open(cast_path, "w", encoding="utf-8") as f:
        json.dump(cast, f, indent=2, ensure_ascii=False)
    with open(trends_path, "w", encoding="utf-8") as f:
        json.dump(trends, f, indent=2, ensure_ascii=False)
        
    print(f"[OK] Created {len(movies)} movies, {len(cast)} cast, {len(trends)} trends in data/")
    
    # Reload local memory
    ch_client._load_local_data()
    
    # 2. Test Vector Similarity Search (MCP Tool)
    print("\n[2/4] Testing Vector Similarity Search Tool (search_similar_movies)...")
    sample_emb = movies[0]["script_embedding"]
    t0 = time.perf_counter()
    similar_res = search_similar_movies(target_embedding=sample_emb, genre="Sci-Fi", limit=3)
    t1 = time.perf_counter()
    
    similar_data = json.loads(similar_res)
    print(f"[PERF] Vector Search Latency: {(t1 - t0) * 1000:.2f} ms")
    print(f"Top Similar Matches found: {len(similar_data)}")
    for idx, item in enumerate(similar_data):
        print(f"  #{idx+1}: {item.get('title')} (Distance: {item.get('distance')}, Budget: ${item.get('budget'):,}, WW Gross: ${item.get('box_office_worldwide'):,})")
    assert len(similar_data) > 0, "Vector search should return matches"
    
    # 3. Test Genre ROI Distribution (MCP Tool)
    print("\n[3/4] Testing Genre ROI Distribution Tool (get_genre_roi_distribution)...")
    roi_res = get_genre_roi_distribution(genre="Sci-Fi", mpaa_rating="PG-13")
    roi_data = json.loads(roi_res)
    print(f"Sci-Fi ROI Stats: {json.dumps(roi_data, indent=2)}")
    assert len(roi_data) > 0, "ROI distribution should return stats"
    
    # 4. Test Cast Recommendations (MCP Tool)
    print("\n[4/4] Testing Cast Recommendations Tool (get_cast_roi_recommendations)...")
    cast_res = get_cast_roi_recommendations(primary_genre="Sci-Fi", role_type="director", limit=3)
    cast_data = json.loads(cast_res)
    print(f"Top Sci-Fi Directors: {json.dumps(cast_data, indent=2)}")
    assert len(cast_data) > 0, "Cast recommendation should return results"
    
    print("\n" + "=" * 60)
    print("[SUCCESS] All Phase 1 Pipeline Tests Passed Successfully!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
