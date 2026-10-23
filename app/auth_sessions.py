"""Private server-side authentication state, excluded from business exports."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from database.models import AdminUser, Base, UTCDateTime


class AuthBase(DeclarativeBase):
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)


class AuthSession(AuthBase):
    __tablename__ = 'admin_auth_sessions'

    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_user_id: Mapped[UUID] = mapped_column(ForeignKey(AdminUser.id, ondelete='CASCADE'), index=True)
    token_data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
