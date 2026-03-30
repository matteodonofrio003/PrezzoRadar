"""
PrezzoVicinato — SQLAlchemy 2.0 Models
Dipendenze: sqlalchemy>=2.0, geoalchemy2, psycopg[binary]
"""
import uuid
from datetime import date, datetime
from enum import Enum as PyEnum
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    UUID, Boolean, CheckConstraint, Date, DateTime,
    ForeignKey, Index, Numeric, SmallInteger, String, Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enum Python (specchiato sul DB) ───────────────────────────────────────────

class StatoVolantino(str, PyEnum):
    PENDING    = "pending"
    PROCESSING = "processing"
    COMPLETED  = "completed"
    FAILED     = "failed"


# ── Modelli ───────────────────────────────────────────────────────────────────

class Supermercato(Base):
    __tablename__ = "supermercati"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    catena: Mapped[str] = mapped_column(String(100), nullable=False)
    nome_punto_vendita: Mapped[Optional[str]] = mapped_column(String(200))
    indirizzo: Mapped[str] = mapped_column(Text, nullable=False)
    citta: Mapped[str] = mapped_column(String(100), nullable=False)
    cap: Mapped[Optional[str]] = mapped_column(String(10))
    location: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326)
    )
    attivo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    logo_url: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    volantini: Mapped[list["Volantino"]] = relationship(
        back_populates="supermercato", cascade="all, delete-orphan"
    )
    offerte: Mapped[list["Offerta"]] = relationship(
        back_populates="supermercato"
    )

    __table_args__ = (
        Index("idx_supermercati_location", "location", postgresql_using="gist"),
        Index("idx_supermercati_catena", "catena"),
    )

    def __repr__(self) -> str:
        return f"<Supermercato {self.catena} — {self.citta}>"


class Volantino(Base):
    __tablename__ = "volantini"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    supermercato_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("supermercati.id", ondelete="CASCADE"),
        nullable=False,
    )
    data_inizio: Mapped[date] = mapped_column(Date, nullable=False)
    data_fine: Mapped[date] = mapped_column(Date, nullable=False)
    url_originale: Mapped[Optional[str]] = mapped_column(Text)
    url_pdf: Mapped[Optional[str]] = mapped_column(Text)
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, default=StatoVolantino.PENDING.value
    )
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    pagine: Mapped[Optional[int]] = mapped_column(SmallInteger)
    errore: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    supermercato: Mapped["Supermercato"] = relationship(back_populates="volantini")
    offerte: Mapped[list["Offerta"]] = relationship(
        back_populates="volantino", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("data_fine >= data_inizio", name="chk_volantino_date"),
        Index("idx_volantini_supermercato", "supermercato_id"),
        Index(
            "idx_volantini_attivi", "data_inizio", "data_fine",
            postgresql_where="stato = 'completed'",
        ),
    )

    @property
    def is_attivo(self) -> bool:
        return (
            self.stato == StatoVolantino.COMPLETED.value
            and self.data_fine >= date.today()
        )

    def __repr__(self) -> str:
        return f"<Volantino {self.supermercato_id} {self.data_inizio}→{self.data_fine}>"


class Offerta(Base):
    __tablename__ = "offerte"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    volantino_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("volantini.id", ondelete="CASCADE"),
        nullable=False,
    )
    supermercato_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("supermercati.id", ondelete="CASCADE"),
        nullable=False,
    )
    nome_prodotto: Mapped[str] = mapped_column(String(300), nullable=False)
    marca: Mapped[Optional[str]] = mapped_column(String(100))
    quantita: Mapped[Optional[str]] = mapped_column(String(50))
    prezzo: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    prezzo_per_unita: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    categoria: Mapped[Optional[str]] = mapped_column(String(100))
    nome_normalizzato: Mapped[Optional[str]] = mapped_column(String(300))
    data_inizio: Mapped[date] = mapped_column(Date, nullable=False)
    data_fine: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    volantino: Mapped["Volantino"] = relationship(back_populates="offerte")
    supermercato: Mapped["Supermercato"] = relationship(back_populates="offerte")

    __table_args__ = (
        CheckConstraint("prezzo > 0", name="chk_offerta_prezzo"),
        CheckConstraint("data_fine >= data_inizio", name="chk_offerta_date"),
        Index("idx_offerte_nome_norm", "nome_normalizzato"),
        Index(
            "idx_offerte_trgm", "nome_normalizzato",
            postgresql_using="gin",
            postgresql_ops={"nome_normalizzato": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Offerta {self.nome_prodotto!r} €{self.prezzo}>"


class Prodotto(Base):
    """Catalogo normalizzato per il fuzzy matching dei prodotti."""
    __tablename__ = "prodotti"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nome_canonico: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    marca: Mapped[Optional[str]] = mapped_column(String(100))
    categoria: Mapped[Optional[str]] = mapped_column(String(100))
    # Lista di nomi alternativi visti nel tempo
    # es: ["gin gordons 70cl", "gordon's dry gin lt 0.7"]
    varianti: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_prodotti_trgm", "nome_canonico",
            postgresql_using="gin",
            postgresql_ops={"nome_canonico": "gin_trgm_ops"},
        ),
        Index("idx_prodotti_varianti", "varianti", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Prodotto {self.nome_canonico!r}>"