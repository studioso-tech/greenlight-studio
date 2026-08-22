"""
ClickHouse Data Seeding Script for Greenlight Studio
Bulk inserts movies, cast, and weekly trends data into ClickHouse.
"""
import os
import json
import logging
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_data")

def seed_database():
    from backend.mcp_server.ch_client import ch_client
    
    if not ch_client.is_connected:
        logger.warning("ClickHouse is not connected. Skipping remote seeding.")
        return False
        
    client = ch_client.client
    db = ch_client.database
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    
    movies_path = os.path.join(data_dir, "movies_historical.json")
    cast_path = os.path.join(data_dir, "cast_analytics.json")
    trends_path = os.path.join(data_dir, "weekly_trends.json")
    
    if not os.path.exists(movies_path):
        logger.error("Dataset not found. Please run generate_dataset.py first.")
        return False
        
    # 1. Insert movies
    logger.info("Inserting movies_historical...")
    with open(movies_path, "r", encoding="utf-8") as f:
        movies_data = json.load(f)
    df_movies = pd.DataFrame(movies_data)
    client.insert_df(f"{db}.movies_historical", df_movies)
    logger.info(f"✅ Inserted {len(df_movies)} movies.")
    
    # 2. Insert cast
    logger.info("Inserting cast_analytics...")
    with open(cast_path, "r", encoding="utf-8") as f:
        cast_data = json.load(f)
    df_cast = pd.DataFrame(cast_data)
    client.insert_df(f"{db}.cast_analytics", df_cast)
    logger.info(f"✅ Inserted {len(df_cast)} cast records.")
    
    # 3. Insert weekly trends
    logger.info("Inserting box_office_weekly_trends...")
    with open(trends_path, "r", encoding="utf-8") as f:
        trends_data = json.load(f)
    df_trends = pd.DataFrame(trends_data)
    client.insert_df(f"{db}.box_office_weekly_trends", df_trends)
    logger.info(f"✅ Inserted {len(df_trends)} weekly trend records.")
    
    logger.info("🚀 Data seeding completed successfully!")
    return True

if __name__ == "__main__":
    seed_database()
