import os, sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

_DB_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
_DB_PATH = os.path.join(_DB_DIR, "crm.db")
if not os.path.exists(_DB_PATH):
    _PARENT = os.path.dirname(_DB_DIR)
    _PARENT_DB = os.path.join(_PARENT, "crm.db")
    if os.path.exists(_PARENT_DB):
        _DB_DIR = _PARENT
        _DB_PATH = _PARENT_DB
SQLALCHEMY_DATABASE_URL = f"sqlite:///{_DB_PATH}"
DB_DIR = _DB_DIR

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_tokens ADD COLUMN company_id VARCHAR(100) DEFAULT ''"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_chats ADD COLUMN avito_item_id INTEGER DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE clients ADD COLUMN deal_name VARCHAR(500) DEFAULT ''"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_tokens ADD COLUMN avito_client_id VARCHAR(500) DEFAULT ''"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_tokens ADD COLUMN avito_client_secret VARCHAR(500) DEFAULT ''"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_messages ADD COLUMN is_read BOOLEAN DEFAULT 0"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_messages ADD COLUMN read_at DATETIME DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE clients ADD COLUMN rejection_reason_id INTEGER DEFAULT NULL REFERENCES rejection_reasons(id)"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_items ADD COLUMN impressions INTEGER DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_items ADD COLUMN views INTEGER DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_items ADD COLUMN contacts INTEGER DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_items ADD COLUMN favorites INTEGER DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_items ADD COLUMN spent FLOAT DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_items ADD COLUMN stats_updated_at DATETIME DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS avito_item_daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    avito_item_id INTEGER NOT NULL,
                    date DATETIME NOT NULL,
                    impressions INTEGER DEFAULT NULL,
                    views INTEGER DEFAULT NULL,
                    contacts INTEGER DEFAULT NULL,
                    favorites INTEGER DEFAULT NULL,
                    spent FLOAT DEFAULT NULL,
                    updated_at DATETIME DEFAULT NULL,
                    UNIQUE(avito_item_id, date)
                )
            """))
            conn.commit()
    except Exception:
        pass
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE avito_item_daily_stats ADD COLUMN date_from DATETIME DEFAULT NULL"))
            conn.commit()
    except Exception:
        pass
