import os
import atexit
import tempfile

# Use a unique temp DB per pytest session to avoid cross-process lock conflicts.
_fd, _TMPDB = tempfile.mkstemp(suffix="_dosemed_test.db")
os.close(_fd)
os.unlink(_TMPDB)  # remove so SQLite creates it fresh

os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SMTP_HOST", "")
os.environ.setdefault("BREVO_API_KEY", "")

import uuid
import pytest
from fastapi.testclient import TestClient

# Import database AFTER env var is set so engine points at the temp file.
import database as _db_module
from database import get_db, limiter
from models import Base, Estoque, AlarmeRemedio
from main import app
import cron
from tests.helpers import TELEFONE, TELEFONE_B, PIN, _criar_usuario, _definir_pin, _login

# Reuse the same engine/session factory that database.py already created.
# Having two engines on the same SQLite file causes lock races.
_engine = _db_module.engine
_Session = _db_module.SessionLocal

# Unique key per request so slowapi rate limits never fire in tests.
limiter._key_func = lambda request: str(uuid.uuid4())

# Remove the temp DB file when the process exits.
def _cleanup():
    _engine.dispose()
    try:
        os.unlink(_TMPDB)
    except (FileNotFoundError, PermissionError):
        pass

atexit.register(_cleanup)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture
def db(reset_db):
    session = _Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    limiter._storage.reset()

    def _override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    cron._scheduler = None
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def token(client):
    return _criar_usuario(client)


@pytest.fixture
def token_com_pin(client):
    _criar_usuario(client)
    _definir_pin(client)
    return _login(client)


@pytest.fixture
def token_b(client):
    return _criar_usuario(client, telefone=TELEFONE_B, nome="Outro")


@pytest.fixture
def item_id(client, token, db):
    item = Estoque(usuario_id=TELEFONE, nome_medicamento="Dipirona", quantidade=2, status="ok")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item.id


@pytest.fixture
def alarme_id(client, token, db):
    a = AlarmeRemedio(usuario_id=TELEFONE, nome_med="Dipirona", horario="08:00", dias="1,2,3,4,5", ativo=1)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a.id
