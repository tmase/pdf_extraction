from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from db.models import Base

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create all tables (idempotent) and load default prompts / seed ground truth."""
    from pathlib import Path
    Path(config.BASE_DIR / "storage").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)

    from db import seed
    with session_scope() as session:
        seed.seed_prompts(session)
        seed.seed_ground_truth(session)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
