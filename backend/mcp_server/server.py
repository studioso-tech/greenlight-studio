"""
Greenlight Studio - FastMCP Server for ClickHouse
Exposes movie intelligence and OLAP tools for Gemini Enterprise Agent Platform via Model Context Protocol.
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional
from fastmcp import FastMCP
from dotenv import load_dotenv

# Try importing ch_client with multiple fallback path styles
try:
    from backend.mcp_server.ch_client import ch_client
except ImportError:
    try:
        from mcp_server.ch_client import ch_client
    except ImportError:
        import sys
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        from backend.mcp_server.ch_client import ch_client

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("greenlight_mcp")

mcp = FastMCP("Greenlight Studio ClickHouse Intelligence")

@mcp.tool()
def search_similar_movies(
    target_embedding: List[float],
    genre: Optional[str] = None,
    max_budget: Optional[int] = None,
    limit: int = 5
) -> str:
    """
    Search historical movies by script tone embedding using ClickHouse Vector Similarity.
    Returns similar titles, actual budget, worldwide gross, RT score, and cosine distance.
    """
    results = ch_client.search_similar_movies(
        target_embedding=target_embedding,
        genre=genre,
        max_budget=max_budget,
        limit=limit
    )
    return json.dumps(results, indent=2, ensure_ascii=False)

@mcp.tool()
def get_genre_roi_distribution(
    genre: str,
    mpaa_rating: Optional[str] = None,
    release_month: Optional[int] = None
) -> str:
    """
    Computes statistical ROI distribution, average budget, box office multiplier, 
    and success rate for a specific genre and rating from ClickHouse.
    """
    if ch_client.is_connected and ch_client.client:
        where = [f"has(genres, '{genre}')"]
        if mpaa_rating:
            where.append(f"mpaa_rating = '{mpaa_rating}'")
        if release_month:
            where.append(f"release_month = {release_month}")
            
        sql = f"""
        SELECT 
            count() as sample_size,
            avg(budget) as avg_budget,
            avg(box_office_worldwide) as avg_worldwide_gross,
            avg(box_office_worldwide / nullif(budget, 0)) as avg_roi_multiplier,
            quantile(0.5)(box_office_worldwide / nullif(budget, 0)) as median_roi_multiplier,
            countIf((box_office_worldwide / nullif(budget, 0)) >= 2.5) / count() * 100 as blockbuster_success_rate_pct
        FROM movies_historical
        WHERE {' AND '.join(where)}
        """
        rows = ch_client.query(sql)
        return json.dumps(rows, indent=2, ensure_ascii=False)
    else:
        # Fallback local calculation
        movies = [m for m in ch_client.local_movies if genre in m.get("genres", [])]
        if mpaa_rating:
            movies = [m for m in movies if m.get("mpaa_rating") == mpaa_rating]
        if not movies:
            return json.dumps({"error": "No matching records found"})
            
        rois = [m["box_office_worldwide"] / max(1, m["budget"]) for m in movies]
        budgets = [m["budget"] for m in movies]
        grosses = [m["box_office_worldwide"] for m in movies]
        
        stat = {
            "sample_size": len(movies),
            "avg_budget": sum(budgets) / len(budgets),
            "avg_worldwide_gross": sum(grosses) / len(grosses),
            "avg_roi_multiplier": round(sum(rois) / len(rois), 2),
            "median_roi_multiplier": round(float(sorted(rois)[len(rois)//2]), 2),
            "blockbuster_success_rate_pct": round(len([r for r in rois if r >= 2.5]) / len(rois) * 100, 1)
        }
        return json.dumps([stat], indent=2)

@mcp.tool()
def get_cast_roi_recommendations(
    primary_genre: str,
    role_type: str = "actor",
    limit: int = 5
) -> str:
    """
    Ranks top actors or directors based on historical ROI and Box Office Power Score for a genre.
    """
    if ch_client.is_connected and ch_client.client:
        sql = f"""
        SELECT name, role_type, primary_genre, avg_roi_multiplier, box_office_power_score, avg_worldwide_gross
        FROM cast_analytics
        WHERE role_type = '{role_type}' AND primary_genre = '{primary_genre}'
        ORDER BY avg_roi_multiplier DESC, box_office_power_score DESC
        LIMIT {limit}
        """
        rows = ch_client.query(sql)
        return json.dumps(rows, indent=2, ensure_ascii=False)
    else:
        cast = [c for c in ch_client.local_cast if c["role_type"] == role_type and c["primary_genre"] == primary_genre]
        cast.sort(key=lambda x: (x["avg_roi_multiplier"], x["box_office_power_score"]), reverse=True)
        return json.dumps(cast[:limit], indent=2, ensure_ascii=False)

@mcp.tool()
def run_custom_box_office_query(sql_query: str) -> str:
    """
    Executes a custom read-only SQL query against ClickHouse for deep multi-dimensional analytics.
    """
    # Simple safety guard
    forbidden = ["DROP", "TRUNCATE", "DELETE", "ALTER", "INSERT", "UPDATE"]
    for kw in forbidden:
        if kw in sql_query.upper():
            return json.dumps({"error": f"Operation '{kw}' is not permitted for read-only agent queries."})
            
    rows = ch_client.query(sql_query)
    return json.dumps(rows, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    logger.info("Starting Greenlight ClickHouse FastMCP Server...")
    mcp.run()
