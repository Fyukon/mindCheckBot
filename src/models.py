from __future__ import annotations
from sqlalchemy import BigInteger, String, Integer, DateTime, Text, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from .db import Base


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    language_code: Mapped[str | None] = mapped_column(String(8), default='ru', nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default='Europe/Moscow')
    checkin_time: Mapped[str] = mapped_column(String(5), default='18:00')  # HH:MM

    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    checkins: Mapped[list['Checkin']] = relationship(back_populates='user')


class Checkin(Base):
    __tablename__ = 'checkins'
    __table_args__ = (
        UniqueConstraint('user_id', 'date', name='uq_checkin_user_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)

    date: Mapped[datetime] = mapped_column(DateTime, index=True)  # normalized to local date start
    mood_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-10
    stress_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    energy_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    emotions: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated labels
    sleep_hours: Mapped[float | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendations: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates='checkins')


class Reminder(Base):
    __tablename__ = 'reminders'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    times: Mapped[str] = mapped_column(String(64), default='18:00')  # e.g., '09:00,18:00'

