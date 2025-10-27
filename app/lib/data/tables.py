from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Time,
)


metadata = MetaData()


source_type_enum = Enum(
    "image",
    "taxa",
    "copy",
    "ai",
    name="source_type_enum",
)

data_type_enum = Enum(
    "image",
    "taxa",
    "copy",
    "ai",
    name="data_type_enum",
)

days = Table(
    "days",
    metadata,
    Column("date_id", Integer, primary_key=True, autoincrement=True),
    Column("date", Date, nullable=False, unique=True),
    Column("dawn", Time),
    Column("sunrise", Time),
    Column("solar_noon", Time),
    Column("dusk", Time),
    Column("sunset", Time),
    Column("forecast_high", Float),
    Column("forecast_low", Float),
    Column("forecast_rain", Float),
    Column("forecast_issued_at", DateTime),
    Column("forecast_source", String(128)),
    Column("actual_high", Float),
    Column("actual_low", Float),
    Column("actual_rain", Float),
    Column("actual_updated_at", DateTime),
    Column("actual_source", String(128)),
    Column("season", String(32)),
)

species = Table(
    "species",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("sci_name", String(255), nullable=False, unique=True),
    Column("species", String(255)),
    Column("genus", String(255)),
    Column("family", String(255)),
    Column("common_name", String(255)),
    Column("first_id", DateTime),
    Column("last_id", DateTime),
    Column("image_url", String(512)),
    Column("id_days", Integer, default=0),
    Column("info_url", String(512)),
    Column("summary", Text),
)

recordings = Table(
    "recordings",
    metadata,
    Column("wav_id", String(255), primary_key=True),
    Column("path", String(1024), nullable=False, unique=True),
    Column("source_id", String(128)),
    Column("source_name", String(255)),
    Column("source_display_name", String(255)),
    Column("source_location", String(255)),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
)

idents = Table(
    "idents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "date_id",
        Integer,
        ForeignKey("days.date_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "species_id",
        String(64),
        ForeignKey("species.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("date", Date, nullable=False),
    Column("time", Time),
    Column("common_name", String(255)),
    Column("sci_name", String(255)),
    Column("confidence", Float),
    Column(
        "wav_id",
        String(255),
        ForeignKey("recordings.wav_id", ondelete="SET NULL"),
    ),
    Column("start_time", Float),
    Column("end_time", Float),
)

Index("ix_idents_date_id", idents.c.date_id)
Index("ix_idents_species_id", idents.c.species_id)
Index("ix_idents_date_time", idents.c.date, idents.c.time)
Index("ix_idents_confidence", idents.c.confidence)

data_sources = Table(
    "data_sources",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(128), nullable=False, unique=True),
    Column("active", Boolean, default=True, nullable=False),
    Column("title", String(255)),
    Column("date_added", DateTime, default=datetime.utcnow, nullable=False),
    Column("date_updated", DateTime),
    Column("source_type", source_type_enum, nullable=False),
    Column("reference_url", String(512)),
    Column("api_url", String(512)),
    Column("key_required", Boolean, default=False, nullable=False),
    Column("api_key", String(255)),
    Column("cite", Boolean, default=True, nullable=False),
    Column("headers", JSON, default={}),
)

data_citations = Table(
    "data_citations",
    metadata,
    Column("citation_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "source_id",
        Integer,
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "species_id",
        String(64),
        ForeignKey("species.id", ondelete="CASCADE"),
    ),
    Column("created_date", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_date", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    Column("data_type", data_type_enum, nullable=False),
    Column("content", Text, nullable=False),
)

Index("ix_data_citations_source_id", data_citations.c.source_id)
Index("ix_data_citations_species_id", data_citations.c.species_id)


TABLES = {
    "days": days,
    "species": species,
    "recordings": recordings,
    "idents": idents,
    "data_sources": data_sources,
    "data_citations": data_citations,
}
