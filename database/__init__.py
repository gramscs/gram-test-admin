"""Next database version. Deliberately independent of the running Flask schema."""

from database.models import Base

__all__ = ["Base"]
