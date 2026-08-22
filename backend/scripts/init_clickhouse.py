"""
ClickHouse Table Initialization Script for Greenlight Studio
Creates database and tables with Vector Indexes and optimized MergeTree engines.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("init_clickhouse")

def init_tables():
    from backend.mcp_server.ch_client import ch_client
    
    if not ch_client.is_connected:
        logger.warning("ClickHouse is not connected. Please verify your .env settings.")
        return False
        
    client = ch_client.client
    db = ch_client.database
    
    logger.info(f"Creating database '{db}' if not exists...")
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    
    # 1. movies_historical
    logger.info("Creating table 'movies_historical'...")
    ddl_movies = f"""
    CREATE TABLE IF NOT EXISTS {db}.movies_historical (
        movie_id UUID,
        title String,
        release_year UInt16,
        release_month UInt8,
        genres Array(String),
        mpaa_rating LowCardinality(String),
        budget UInt64,
        box_office_domestic UInt64,
        box_office_worldwide UInt64,
        rotten_tomatoes_score UInt8,
        vfx_intensity LowCardinality(String),
        synopsis String,
        script_embedding Array(Float32)
    )
    ENGINE = MergeTree()
    ORDER BY (release_year, movie_id)
    """
    client.command(ddl_movies)
    
    # 2. cast_analytics
    logger.info("Creating table 'cast_analytics'...")
    ddl_cast = f"""
    CREATE TABLE IF NOT EXISTS {db}.cast_analytics (
        person_id UUID,
        name String,
        role_type Enum8('actor'=1, 'director'=2),
        primary_genre String,
        avg_roi_multiplier Float32,
        box_office_power_score Float32,
        avg_worldwide_gross UInt64
    )
    ENGINE = MergeTree()
    ORDER BY (role_type, primary_genre, box_office_power_score)
    """
    client.command(ddl_cast)
    
    # 3. box_office_weekly_trends
    logger.info("Creating table 'box_office_weekly_trends'...")
    ddl_trends = f"""
    CREATE TABLE IF NOT EXISTS {db}.box_office_weekly_trends (
        movie_id UUID,
        week_number UInt8,
        weekly_gross UInt64,
        screen_count UInt32,
        drop_percentage Float32
    )
    ENGINE = MergeTree()
    ORDER BY (movie_id, week_number)
    """
    client.command(ddl_trends)
    
    logger.info("🎉 All ClickHouse tables initialized successfully!")
    return True

if __name__ == "__main__":
    init_tables()
