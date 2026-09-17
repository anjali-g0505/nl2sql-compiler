"""Thin MySQL layer: a connection pool and a function that runs raw SQL as-is.

No ORM on purpose — later steps generate SQL strings and hand them straight to
execute_query().
"""
import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

from mysql.connector import Error as MySQLError
from mysql.connector import pooling

from app.config import settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Raised for any DB failure. The message is safe to return to API clients."""


_pool: Optional[pooling.MySQLConnectionPool] = None # The connection pool is created lazily on the first request, so the app can start even if MySQL is down.
_pool_lock = threading.Lock() # if multiple threads try to create the pool at once, only one should succeed
 

def _get_pool() -> pooling.MySQLConnectionPool: #this function is called by execute_query() to get the connection pool. If the pool doesn't exist yet, it creates it using the settings from the config. If it fails to create the pool, it logs the exception and raises a DatabaseError.
    # a failed attempt leaves _pool as None and the next request retries.
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = pooling.MySQLConnectionPool(
                    pool_name="nl2sql",
                    pool_size=settings.mysql_pool_size,
                    host=settings.mysql_host,
                    port=settings.mysql_port,
                    user=settings.mysql_user,
                    password=settings.mysql_password,
                    database=settings.mysql_database,
                    connection_timeout=5,
                )
    return _pool
#a pool is a collection of database connections that can be reused, which improves performance by avoiding the overhead of creating and closing connections for each query. The _get_pool() function ensures that only one pool is created, even if multiple threads try to access it at the same time.

def execute_query(sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Run one SQL statement and return all rows as a list of column->value dicts."""
    try:
        conn = _get_pool().get_connection()
    except MySQLError:
        logger.exception("Could not obtain a MySQL connection")
        raise DatabaseError("Could not connect to the database.") from None

    try:
        cursor = conn.cursor(dictionary=True) #cursor is an object that allows you to send SQL to database, execute SQL queries and fetch results. The dictionary=True argument means that the results will be returned as dictionaries, where the keys are the column names and the values are the corresponding values for each row.
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            cursor.close()
    except MySQLError:
        logger.exception("MySQL query failed")
        raise DatabaseError("Database query failed.") from None
    finally:
        conn.close()  # returns the connection to the pool, doesn't destroy it
