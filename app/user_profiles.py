"""Audit profiles for verified Supabase users; no accounts or passwords live here."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import db
from database.models import AdminUser


def profile_for_identity(subject):
    profile = db.session.execute(select(AdminUser).where(AdminUser.supabase_user_id == subject)).scalar_one_or_none()
    if profile is not None:
        return profile
    # One identity for new profiles; preserve independently numbered legacy rows.
    # Concurrent first logins must converge on the same unique Supabase link.
    db.session.execute(insert(AdminUser).values(id=subject, supabase_user_id=subject,
        username='supabase-' + subject.hex, role='operator', active=True).on_conflict_do_nothing())
    profile = db.session.execute(select(AdminUser).where(AdminUser.supabase_user_id == subject)).scalar_one_or_none()
    if profile is None:
        raise ValueError('The application profile conflicts with an existing record. Contact your administrator.')
    return profile
