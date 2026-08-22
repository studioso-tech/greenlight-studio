"""
ClickHouse Client for Greenlight Studio
Provides connection management, query execution, vector similarity search, and fallback simulation.
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("ch_client")
logging.basicConfig(level=logging.INFO)

class ClickHouseClient:
    def __init__(self):
        self.host = os.getenv("CLICKHOUSE_HOST", "localhost")
        self.port = int(os.getenv("CLICKHOUSE_PORT", "8443"))
        self.user = os.getenv("CLICKHOUSE_USER", "default")
        self.password = os.getenv("CLICKHOUSE_PASSWORD", "")
        self.secure = os.getenv("CLICKHOUSE_SECURE", "true").lower() == "true"
        self.database = os.getenv("CLICKHOUSE_DATABASE", "greenlight_studio")
        
        self.client = None
        self.is_connected = False
        self._init_connection()

    def _init_connection(self):
        try:
            import clickhouse_connect
            self.client = clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                secure=self.secure,
                database=self.database,
                connect_timeout=5,
            )
            self.is_connected = True
            logger.info(f"⚡ Successfully connected to ClickHouse at {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"⚠️ Could not connect to ClickHouse ({e}). Enabling Local Memory Simulator for offline/dev mode.")
            self.is_connected = False
            self._load_local_data()

    def _load_local_data(self):
        """Loads local JSON data into memory if ClickHouse connection is not reachable."""
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        movies_file = os.path.join(data_dir, "movies_historical.json")
        cast_file = os.path.join(data_dir, "cast_analytics.json")
        
        self.local_movies = []
        self.local_cast = []
        
        if os.path.exists(movies_file):
            with open(movies_file, "r", encoding="utf-8") as f:
                self.local_movies = json.load(f)
        if os.path.exists(cast_file):
            with open(cast_file, "r", encoding="utf-8") as f:
                self.local_cast = json.load(f)
        logger.info(f"Loaded {len(self.local_movies)} movies and {len(self.local_cast)} cast records in local memory.")

    def query(self, query_str: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Executes a SQL query against ClickHouse, or simulates it locally."""
        if self.is_connected and self.client:
            result = self.client.query(query_str, parameters=parameters)
            columns = result.column_names
            return [dict(zip(columns, row)) for row in result.result_rows]
        else:
            return self._simulate_query(query_str, parameters)

    def search_similar_movies(self, target_embedding: List[float], genre: Optional[str] = None, max_budget: Optional[int] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Finds most similar movies by cosine distance on script_embedding."""
        if self.is_connected and self.client:
            where_clauses = []
            params = {"target_emb": target_embedding, "limit": limit}
            if genre:
                where_clauses.append("has(genres, %(genre)s)")
                params["genre"] = genre
            if max_budget:
                where_clauses.append("budget <= %(max_budget)s")
                params["max_budget"] = max_budget
                
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            sql = f"""
                SELECT movie_id, title, release_year, genres, mpaa_rating, budget, 
                       box_office_domestic, box_office_worldwide, rotten_tomatoes_score, 
                       vfx_intensity, synopsis,
                       cosineDistance(script_embedding, %(target_emb)s) AS distance
                FROM movies_historical
                {where_sql}
                ORDER BY distance ASC
                LIMIT %(limit)s
            """
            return self.query(sql, parameters=params)
        else:
            # High-speed numpy cosine similarity in memory
            target_vec = np.array(target_embedding)
            filtered = self.local_movies
            if genre:
                filtered = [m for m in filtered if genre in m.get("genres", [])]
            if max_budget:
                filtered = [m for m in filtered if m.get("budget", 0) <= max_budget]
                
            results = []
            for m in filtered:
                m_vec = np.array(m["script_embedding"])
                sim = float(np.dot(target_vec, m_vec) / (np.linalg.norm(target_vec) * np.linalg.norm(m_vec) + 1e-9))
                dist = float(round(1.0 - sim, 4))
                results.append({
                    "movie_id": m["movie_id"],
                    "title": m["title"],
                    "release_year": m["release_year"],
                    "genres": m["genres"],
                    "mpaa_rating": m["mpaa_rating"],
                    "budget": m["budget"],
                    "box_office_domestic": m["box_office_domestic"],
                    "box_office_worldwide": m["box_office_worldwide"],
                    "rotten_tomatoes_score": m["rotten_tomatoes_score"],
                    "vfx_intensity": m["vfx_intensity"],
                    "synopsis": m["synopsis"],
                    "distance": dist,
                })
            results.sort(key=lambda x: x["distance"])
            return results[:limit]

    def _simulate_query(self, query_str: str, parameters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Fallback basic aggregations if needed
        return [{"status": "simulated", "message": "Local memory mode active"}]

# Singleton instance
ch_client = ClickHouseClient()
