- database tables
    - days
        - date_id
        - date
        - dawn
        - sunrise
        - solar_noon
        - dusk
        - sunset
        - forecast_high
        - forecast_low
        - forecast_rain
        - actual_high
        - actual_low
        - actual_rain
        - season

    - idents - identifications
        - id -- serial
        - date_id -- fk to days
        - species_id -- fk to species
        - date - id date
        - time -- time of recording
        - common_name - 
        - sci_name
        - confidence
        - wav_id
        - start_time
        - end_time

    - recordings
        - wave_id
        - path

    - species - species identified
        - id -- hash of the sci_name
        - sci_name
        - species
        - genus
        - family
        - common_name
        - first_id
        - last_id
        - image_url
        - id_days -- number of days this species was identified
        - info_url
        - ai_summary

    - data_sources - API datasources loaded and updated from yaml config
        - id
        - name
        - title
        - date_added
        - date_update
        - source_type [image, taxa, copy, ai ]
        - reference_url
        - api_url
        - key_required -- bool
        - api_key
        - cite -- bool

    - data_citations -- citations for species information, images, and other content
        - citation_id
        - source_id
        - species_id
        - created_date
        - updated_date
        - data_type [image, taxa, copy, ai ]
        - content 

## Migration Workflow
- database migrations live in `backend/app/lib/data/db.py` alongside the `register_migration` helper.
- when schema changes are needed, write a new function that accepts a `Connection`, performs the DDL/data updates, then call `register_migration("ZZZZ_description", your_function)`; version strings sort lexicographically, so prefix with zero-padded numbers like `0002_add_table`.
- after registering, keep the import order stable so the registration runs on startup; the migration system records applied versions in `schema_migrations`.
- run the backend once (or invoke a small bootstrap script) to apply the migration; SQLite will automatically create the database file and WAL metadata if missing.
- to create a new migration:
    1. edit `backend/app/lib/data/db.py` and add a function such as `def _upgrade_0002_add_observations(connection: Connection) -> None:` that executes SQL/SQLAlchemy DDL (e.g. `connection.execute(text(\"ALTER TABLE ...\"))` or `metadata.tables[...]` operations).
    2. at module scope, register it via `register_migration("0002_add_observations", _upgrade_0002_add_observations)`.
    3. ensure the function is defined before the registration call so Python has the symbol ready at import time.
    4. run the backend (or a dedicated bootstrap script) once—the migration runner handles ordering and only applies versions not recorded in `schema_migrations`.
    5. if something goes wrong mid-migration, fix the code, delete the row from `schema_migrations` or restore from backup, then re-run.

## New Contributor Quickstart
- install deps listed in `backend/requirements.txt` (use a venv: `python -m venv venv && source venv/bin/activate`, then `pip install -r backend/requirements.txt`).
- run `python backend/app/main.py --duration 0` once to bootstrap the environment; the database file and tables are created automatically.
- do your changes and run `python -m compileall backend/app/lib/data` to catch syntax errors early.
- if you add migrations, bump the version string, re-run the app, then peek at `data/birdsong.db` with any SQLite browser to verify the new schema.
