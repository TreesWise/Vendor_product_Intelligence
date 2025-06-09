import logging
import threading
from langchain_community.utilities.sql_database import SQLDatabase
from dotenv import load_dotenv
import os

load_dotenv()


# Fetch credentials from environment variables
api_token = os.getenv("API_TOKEN")
host = os.getenv("HOST")
warehouse_id = os.getenv("WAREHOUSE_ID")



# Databricks connection details 
CATALOG = "hive_metastore"
SCHEMA = "Common"

logging.info(f"Using Databricks host: {host}")

class SingletonSQLDatabase:
    """Thread-safe Singleton for managing a shared SQLDatabase instance."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Create or return the singleton instance."""
        if not cls._instance:
            with cls._lock:
                if not cls._instance:  # Double-check for thread safety
                    logging.info("Initializing SQLDatabase instance...")
                    cls._instance = cls._initialize_instance()
        return cls._instance

    @classmethod
    def _initialize_instance(cls):
        try:
            return SQLDatabase.from_databricks(
                catalog=CATALOG,
                schema=SCHEMA,
                api_token=api_token,
                host=host,
                warehouse_id=warehouse_id,
            )
        except Exception as e:
            logging.error("Failed to initialize SQLDatabase:", exc_info=True)
            raise RuntimeError("Failed to initialize SQLDatabase") from e

    @classmethod
    def get_instance(cls):
        """Retrieve the existing singleton instance."""
        return cls.__new__(cls)
