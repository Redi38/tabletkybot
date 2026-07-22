import os
from datetime import date, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
_cipher: Fernet | None = Fernet(_ENCRYPTION_KEY.encode()) if _ENCRYPTION_KEY else None


class EncryptedString(TypeDecorator):
    """Transparent encryption/decryption of string data in the DB."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and _cipher:
            return _cipher.encrypt(str(value).encode()).decode()
        return value

    def process_result_value(self, value, dialect):
        if value is not None and _cipher:
            try:
                return _cipher.decrypt(str(value).encode()).decode()
            except InvalidToken:
                return value
        return value


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(8), default="ua")
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    repeat_reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    medicines: Mapped[list["Medicine"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    chat_history: Mapped[list["ChatHistory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __str__(self) -> str:
        return f"{self.full_name} (ID: {self.id})"


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(EncryptedString)
    form: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dosage: Mapped[str] = mapped_column(String(64))
    course_duration: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="medicines")
    records: Mapped[list["MedicineRecord"]] = relationship(back_populates="medicine", cascade="all, delete-orphan")
    schedules: Mapped[list["MedicineSchedule"]] = relationship(back_populates="medicine", cascade="all, delete-orphan")

    def __str__(self) -> str:
        return f"{self.name} {self.dosage}"


class MedicineSchedule(Base):
    __tablename__ = "medicine_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id", ondelete="CASCADE"), index=True)
    scheduled_time: Mapped[str] = mapped_column(String(8))

    medicine: Mapped["Medicine"] = relationship(back_populates="schedules")

    def __str__(self) -> str:
        return self.scheduled_time


class MedicineRecord(Base):
    __tablename__ = "medicine_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    status: Mapped[str] = mapped_column(String(16), default="taken")
    remaining_days: Mapped[int] = mapped_column(Integer, default=0)

    medicine: Mapped["Medicine"] = relationship(back_populates="records")

    def __str__(self) -> str:
        return f"{self.status} ({self.taken_at.strftime('%d.%m %H:%M')})"


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(EncryptedString)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="chat_history")

    def __str__(self) -> str:
        return f"{self.role.capitalize()}: {self.content[:30]}..."


class Prescription(Base):
    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    medicine_name: Mapped[str] = mapped_column(EncryptedString)
    valid_from: Mapped[date] = mapped_column(Date)
    expires_at: Mapped[date] = mapped_column(Date)
    max_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchased_quantity: Mapped[int] = mapped_column(Integer, default=0)
    is_fully_purchased: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_days_before: Mapped[int] = mapped_column(Integer, default=3)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="prescriptions")

    def __str__(self) -> str:
        return f"{self.medicine_name} (until {self.expires_at.strftime('%d.%m.%Y')})"


class AIMetric(Base):
    __tablename__ = "ai_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    model_used: Mapped[str] = mapped_column(String(128))
    tool_choice: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tool_names: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped["User"] = relationship()

    def __str__(self) -> str:
        return f"{self.model_used} ({self.latency_ms}ms, {self.created_at.strftime('%d.%m %H:%M')})"
