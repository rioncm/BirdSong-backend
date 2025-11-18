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
    UniqueConstraint,
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
    Column("forecast_office", String(128)),
    Column("actual_high", Float),
    Column("actual_low", Float),
    Column("actual_rain", Float),
    Column("actual_updated_at", DateTime),
    Column("actual_source", String(128)),
    Column("observation_station_id", String(64)),
    Column("observation_station_name", String(128)),
    Column("season", String(32)),
)

species = Table(
    "species",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("sci_name", String(255), nullable=False, unique=True),
    Column("ebird_code", String(50)), # eBird species code for direct linking
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
    Column("duration_seconds", Float),
    Column("source_id", String(128)),
    Column("source_name", String(255)),
    Column("source_display_name", String(255)),
    Column("source_location", String(255)),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
)

weather_sites = Table(
    "weather_sites",
    metadata,
    Column("site_id", Integer, primary_key=True, autoincrement=True),
    Column("site_key", String(64), nullable=False, unique=True),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("timezone", String(64)),
    Column("grid_id", String(32)),
    Column("grid_x", Integer),
    Column("grid_y", Integer),
    Column("forecast_office", String(128)),
    Column("station_id", String(64)),
    Column("station_name", String(128)),
    Column("last_refreshed", DateTime),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
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

settings_categories = Table(
    "settings_categories",
    metadata,
    Column("category_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(128), nullable=False, unique=True),
    Column("description", Text),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

settings_keys = Table(
    "settings_keys",
    metadata,
    Column("setting_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "category_id",
        Integer,
        ForeignKey("settings_categories.category_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("key", String(255), nullable=False, unique=True),
    Column("label", String(255)),
    Column("description", Text),
    Column("data_type", String(32), nullable=False),
    Column("default_value", Text),
    Column("constraints", JSON, default={}),
    Column("editable", Boolean, nullable=False, default=True),
    Column("sensitive", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

settings_values = Table(
    "settings_values",
    metadata,
    Column("value_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "setting_id",
        Integer,
        ForeignKey("settings_keys.setting_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("scope", String(64), nullable=False, default="global"),
    Column("scope_ref", String(128)),
    Column("value", Text, nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
    Column("updated_by", String(128)),
    UniqueConstraint("setting_id", "scope", "scope_ref", name="uq_settings_values_scope"),
)
Index(
    "ix_settings_values_setting_scope",
    settings_values.c.setting_id,
    settings_values.c.scope,
    settings_values.c.scope_ref,
)

settings_audit = Table(
    "settings_audit",
    metadata,
    Column("audit_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "setting_id",
        Integer,
        ForeignKey("settings_keys.setting_id", ondelete="SET NULL"),
    ),
    Column("scope", String(64), nullable=False, default="global"),
    Column("scope_ref", String(128)),
    Column("previous_value", Text),
    Column("new_value", Text),
    Column("actor", String(128)),
    Column("event", String(64)),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
)

data_source_credentials = Table(
    "data_source_credentials",
    metadata,
    Column("credential_id", Integer, primary_key=True, autoincrement=True),
    Column("source_name", String(128), nullable=False, unique=True),
    Column("api_key", Text),
    Column("headers", JSON, default={}),
    Column("expires_at", DateTime),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

bootstrap_state = Table(
    "bootstrap_state",
    metadata,
    Column("state_key", String(128), primary_key=True),
    Column("state_value", JSON, default={}),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(64), primary_key=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("role", String(32), nullable=False, default="user"),
    Column("password_hash", String(255)),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("mfa_secret", String(255)),
    Column("profile", JSON, default={}),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    Column("last_login_at", DateTime),
)

social_accounts = Table(
    "social_accounts",
    metadata,
    Column("account_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("provider", String(64), nullable=False),
    Column("provider_user_id", String(255), nullable=False),
    Column("access_token", Text),
    Column("refresh_token", Text),
    Column("expires_at", DateTime),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    UniqueConstraint("provider", "provider_user_id", name="uq_social_provider_user"),
)

user_preferences = Table(
    "user_preferences",
    metadata,
    Column("preference_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("key", String(128), nullable=False),
    Column("value", JSON, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    UniqueConstraint("user_id", "key", name="uq_user_preferences"),
)


TABLES = {
    "days": days,
    "species": species,
    "recordings": recordings,
    "weather_sites": weather_sites,
    "idents": idents,
    "data_sources": data_sources,
    "data_citations": data_citations,
    "settings_categories": settings_categories,
    "settings_keys": settings_keys,
    "settings_values": settings_values,
    "settings_audit": settings_audit,
    "data_source_credentials": data_source_credentials,
    "bootstrap_state": bootstrap_state,
    "users": users,
    "social_accounts": social_accounts,
    "user_preferences": user_preferences,
}
