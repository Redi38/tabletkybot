import os
from datetime import datetime
from sqlalchemy import (
    BigInteger, String, Integer, Boolean,
    DateTime, ForeignKey, Text, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator
from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
_cipher: Fernet | None = Fernet(_ENCRYPTION_KEY.encode()) if _ENCRYPTION_KEY else None


class EncryptedString(TypeDecorator):
    """Прозоре шифрування/розшифрування рядкових даних у БД."""
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
    language: Mapped[str] = mapped_column(String(8), default="uk")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Kyiv")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    medicines: Mapped[list["Medicine"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_history: Mapped[list["ChatHistory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:
        return f"{self.full_name} (ID: {self.id})"


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(EncryptedString)
    form: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dosage: Mapped[str] = mapped_column(String(64))
    course_duration: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="medicines")
    records: Mapped[list["MedicineRecord"]] = relationship(
        back_populates="medicine", cascade="all, delete-orphan"
    )
    schedules: Mapped[list["MedicineSchedule"]] = relationship(
        back_populates="medicine", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:
        return f"{self.name} {self.dosage}"


class MedicineSchedule(Base):
    __tablename__ = "medicine_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id", ondelete="CASCADE"))
    scheduled_time: Mapped[str] = mapped_column(String(8))

    medicine: Mapped["Medicine"] = relationship(back_populates="schedules")

    def __str__(self) -> str:
        return self.scheduled_time


class MedicineRecord(Base):
    __tablename__ = "medicine_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id", ondelete="CASCADE"))
    taken_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    status: Mapped[str] = mapped_column(String(16), default="taken")
    remaining_days: Mapped[int] = mapped_column(Integer, default=0)

    medicine: Mapped["Medicine"] = relationship(back_populates="records")

    def __str__(self) -> str:
        return f"{self.status} ({self.taken_at.strftime('%d.%m %H:%M')})"


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(EncryptedString)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="chat_history")

    def __str__(self) -> str:
        return f"{self.role.capitalize()}: {self.content[:30]}..."