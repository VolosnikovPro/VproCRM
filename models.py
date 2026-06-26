from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Table, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


client_tags = Table(
    "client_tags", Base.metadata,
    Column("client_id", Integer, ForeignKey("clients.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    order = Column(Integer, nullable=False, default=0)
    color = Column(String(7), nullable=False, default="#6b7280")

    clients = relationship("Client", back_populates="stage")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True)
    order = Column(Integer, nullable=False, default=0)


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True)
    color = Column(String(7), nullable=False, default="#6b7280")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    deal_name = Column(String(500), default="")
    phone = Column(String(50), nullable=False)
    email = Column(String(200), nullable=False, default="")
    organization = Column(String(300), nullable=False, default="")
    address = Column(String(500), nullable=False, default="")
    responsible = Column(String(200), nullable=False, default="")
    budget = Column(Integer, nullable=False, default=0)
    source = Column(String(200), nullable=False, default="")
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=False)
    rejection_reason_id = Column(Integer, ForeignKey("rejection_reasons.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))
    updated_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0), onupdate=lambda: datetime.now().replace(microsecond=0))

    stage = relationship("PipelineStage", back_populates="clients")
    notes = relationship("Note", back_populates="client", cascade="all, delete-orphan", order_by="Note.created_at.desc()")
    tasks = relationship("Task", back_populates="client", cascade="all, delete-orphan")
    custom_fields = relationship("ClientCustomField", back_populates="client", cascade="all, delete-orphan")
    tags = relationship("Tag", secondary=client_tags, lazy="selectin")
    activity_log = relationship("ActivityLog", back_populates="client", cascade="all, delete-orphan", order_by="ActivityLog.created_at.desc()")
    attachments = relationship("Attachment", back_populates="client", cascade="all, delete-orphan")


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))

    client = relationship("Client", back_populates="notes")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    title = Column(String(500), nullable=False)
    due_date = Column(DateTime, nullable=True)
    completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))

    client = relationship("Client", back_populates="tasks")


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    action = Column(String(100), nullable=False)
    description = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))

    client = relationship("Client", back_populates="activity_log")


class ClientCustomField(Base):
    __tablename__ = "client_custom_fields"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    field_name = Column(String(200), nullable=False)
    field_value = Column(Text, nullable=False, default="")

    client = relationship("Client", back_populates="custom_fields")


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    filename = Column(String(500), nullable=False)
    original_name = Column(String(500), nullable=False)
    file_size = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))

    client = relationship("Client", back_populates="attachments")


class AvitoToken(Base):
    __tablename__ = "avito_tokens"

    id = Column(Integer, primary_key=True, index=True)
    access_token = Column(String(2000), nullable=False)
    refresh_token = Column(String(500), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user_id = Column(String(100), nullable=False)
    company_id = Column(String(100), default="")
    avito_client_id = Column(String(500), default="")
    avito_client_secret = Column(String(500), default="")


class AvitoItem(Base):
    __tablename__ = "avito_items"

    id = Column(Integer, primary_key=True, index=True)
    avito_item_id = Column(Integer, unique=True, nullable=True)
    title = Column(String(500), default="")
    address = Column(String(500), default="")
    url = Column(String(1000), default="")
    price = Column(Integer, nullable=True)
    status = Column(String(50), default="")
    category = Column(String(200), default="")
    placed_at = Column(DateTime, nullable=True)
    impressions = Column(Integer, nullable=True)
    views = Column(Integer, nullable=True)
    contacts = Column(Integer, nullable=True)
    favorites = Column(Integer, nullable=True)
    spent = Column(Float, nullable=True)
    stats_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))
    updated_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0), onupdate=lambda: datetime.now().replace(microsecond=0))


class AvitoChat(Base):
    __tablename__ = "avito_chats"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String(100), unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    avito_user_id = Column(String(100), nullable=False)
    avito_item_id = Column(Integer, nullable=True, index=True)
    other_user_name = Column(String(200), default="")
    other_user_phone = Column(String(50), default="")
    item_title = Column(String(500), default="")
    item_url = Column(String(1000), default="")
    item_image = Column(String(1000), default="")
    last_message_at = Column(DateTime, nullable=True)
    unread_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))
    updated_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0), onupdate=lambda: datetime.now().replace(microsecond=0))

    client = relationship("Client")


class RejectionReason(Base):
    __tablename__ = "rejection_reasons"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True)
    order = Column(Integer, nullable=False, default=0)


class AvitoItemDailyStat(Base):
    __tablename__ = "avito_item_daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    avito_item_id = Column(Integer, nullable=False)
    date_from = Column(DateTime, nullable=True)
    date = Column(DateTime, nullable=False)  # stores date_to (end of range)
    impressions = Column(Integer, nullable=True)
    views = Column(Integer, nullable=True)
    contacts = Column(Integer, nullable=True)
    favorites = Column(Integer, nullable=True)
    spent = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))
    __table_args__ = (UniqueConstraint("avito_item_id", "date_from", "date", name="uix_item_range"),)


class AvitoMessage(Base):
    __tablename__ = "avito_messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String(100), nullable=False)
    message_id = Column(String(100), unique=True, nullable=False)
    author_id = Column(String(100), nullable=False)
    author_name = Column(String(200), default="")
    content = Column(Text, default="")
    payload = Column(Text, default="")
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
    synced_at = Column(DateTime, default=lambda: datetime.now().replace(microsecond=0))
