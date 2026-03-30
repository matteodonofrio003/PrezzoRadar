# app/database.py
# Connessione PostgreSQL sincrona — niente async, niente pool complesso.
# Dipendenze: pip install sqlalchemy psycopg2-binary geoalchemy2

import os
from sqlalchemy import (
    create_engine, Column, String, Numeric, Date, Text,
    Boolean, DateTime, func
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from geoalchemy2 import Geometry
import uuid

# ─── Stringa di connessione ────────────────────────────────────────────────────
# Metti i tuoi dati qui oppure crea un file .env e usa python-dotenv
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:1234@localhost:5432/prezzoradar"
    #               ^user    ^pass        ^host   ^port  ^dbname
)

# ─── Engine e sessione ─────────────────────────────────────────────────────────
engine = create_engine(
    DATABASE_URL,
    echo=False,        # metti True per vedere le query SQL in console
    pool_pre_ping=True # riconnette automaticamente se la connessione è caduta
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# ─── Base ORM ─────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  MODELLI
# ─────────────────────────────────────────────────────────────────────────────

class Supermercato(Base):
    __tablename__ = "supermercati"

    id       = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catena   = Column(String(100), nullable=False)
    nome     = Column(String(200))
    indirizzo= Column(Text)
    citta    = Column(String(100))
    logo_url = Column(Text)
    attivo   = Column(Boolean, default=True)
    # Punto GPS — SRID 4326 = WGS84 (longitudine, latitudine)
    location = Column(Geometry("POINT", srid=4326))


class Offerta(Base):
    __tablename__ = "offerte"

    id                = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supermercato_id   = Column(PG_UUID(as_uuid=True), nullable=False)
    nome_prodotto     = Column(String(300), nullable=False)
    marca             = Column(String(100))
    quantita          = Column(String(50))
    prezzo            = Column(Numeric(8, 2), nullable=False)
    prezzo_originale  = Column(Numeric(8, 2))
    categoria         = Column(String(100))
    nome_normalizzato = Column(String(300))  # per fuzzy search
    data_inizio       = Column(Date)
    data_fine         = Column(Date)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())


# ─── Utility ──────────────────────────────────────────────────────────────────

def get_db():
    """
    Dependency per FastAPI — yield della sessione, chiusura garantita.
    Uso:  db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Crea tutte le tabelle se non esistono.
    Chiamato all'avvio da main.py o da run_pipeline.py.
    Richiede che le estensioni PostGIS e pg_trgm siano già installate nel DB.
    """
    # Le estensioni vanno create una volta sola da psql o da questo blocco
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text(
            "CREATE EXTENSION IF NOT EXISTS postgis;"
        ))
        conn.execute(__import__("sqlalchemy").text(
            "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
        ))
        conn.execute(__import__("sqlalchemy").text(
            "CREATE EXTENSION IF NOT EXISTS unaccent;"
        ))
        conn.commit()

    Base.metadata.create_all(bind=engine)

    # Indice GIN per fuzzy search — CREATE INDEX IF NOT EXISTS è idempotente
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text("""
            CREATE INDEX IF NOT EXISTS idx_offerte_trgm
            ON offerte USING GIN (nome_normalizzato gin_trgm_ops);
        """))
        conn.execute(__import__("sqlalchemy").text("""
            CREATE INDEX IF NOT EXISTS idx_supermercati_location
            ON supermercati USING GIST (location);
        """))
        conn.commit()

    print("✅  DB inizializzato correttamente.")