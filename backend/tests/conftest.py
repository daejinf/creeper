import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["AI_PROVIDER"] = "mock"
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest

from app.database import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

