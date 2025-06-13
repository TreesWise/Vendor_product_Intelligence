import logging
import threading
from langchain_community.utilities.sql_database import SQLDatabase
from dotenv import load_dotenv
import os

load_dotenv()


# Fetch credentials from environment variables
API_TOKEN = os.getenv("API_TOKEN")
HOST = os.getenv("HOST")
WAREHOUSE_ID = os.getenv("WAREHOUSE_ID")



# Databricks connection details 
CATALOG = "hive_metastore"
SCHEMA = "Common"

# logging.info(f"Using Databricks host: {host}")

# class SingletonSQLDatabase:
#     """Thread-safe Singleton for managing a shared SQLDatabase instance."""
#     _instance = None
#     _lock = threading.Lock()

#     def __new__(cls):
#         """Create or return the singleton instance."""
#         if not cls._instance:
#             with cls._lock:
#                 if not cls._instance:  # Double-check for thread safety
#                     logging.info("Initializing SQLDatabase instance...")
#                     cls._instance = cls._initialize_instance()
#         return cls._instance

#     @classmethod
#     def _initialize_instance(cls):
#         try:
#             return SQLDatabase.from_databricks(
#                 catalog=CATALOG,
#                 schema=SCHEMA,
#                 api_token=api_token,
#                 host=host,
#                 warehouse_id=warehouse_id,
#             )
#         except Exception as e:
#             logging.error("Failed to initialize SQLDatabase:", exc_info=True)
#             raise RuntimeError("Failed to initialize SQLDatabase") from e

#     @classmethod
#     def get_instance(cls):
#         """Retrieve the existing singleton instance."""
#         return cls.__new__(cls)





logging.basicConfig(level=logging.INFO)
logging.info(f"Using Databricks host: {HOST}")


class SingletonSQLDatabase:
    """Thread-safe Singleton for managing a shared SQLDatabase instance that auto-refreshes on failure."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def _create_instance(cls):
        try:
            return SQLDatabase.from_databricks(
                catalog=CATALOG,
                schema=SCHEMA,
                api_token=API_TOKEN,
                host=HOST,
                warehouse_id=WAREHOUSE_ID,
                engine_args={"pool_pre_ping": True}  # Ensures stale connections are detected
            )
        except Exception as e:
            logging.error("Failed to create SQLDatabase instance.", exc_info=True)
            raise RuntimeError("Failed to initialize SQLDatabase") from e

    @classmethod
    def get_instance(cls):
        """Returns a valid SQLDatabase instance, refreshing if needed."""
        with cls._lock:
            if cls._instance is None:
                logging.info("Initializing new SQLDatabase instance...")
                cls._instance = cls._create_instance()
            else:
                # Validate connection with a quick query
                try:
                    cls._instance.run("SELECT 1")
                except OperationalError as e:
                    logging.warning("Stale or broken SQL connection detected. Reinitializing...")
                    cls._instance = cls._create_instance()
                except Exception as e:
                    logging.error("Unexpected DB error during health check", exc_info=True)
                    raise
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Forcefully reset the instance (manual override)."""
        with cls._lock:
            logging.info("Resetting SQLDatabase singleton instance.")
            cls._instance = None

