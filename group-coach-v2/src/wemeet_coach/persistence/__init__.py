"""Optional PostgreSQL adapter; install wemeet-coach[database] before importing."""

from .store import Store, Conflict, NotFound

__all__ = ["Store", "Conflict", "NotFound"]
