from __future__ import annotations

from datetime import datetime, timedelta

from app.lib.alerts import AlertEngine


def _collect_events(config: dict) -> tuple[AlertEngine, list]:
    events = []

    def publisher(event):
        events.append(event)

    engine = AlertEngine(config, publisher)
    return engine, events


def test_rare_species_rule_emits_event():
    engine, events = _collect_events(
        {
            "rules": {
                "rare_species": {
                    "enabled": True,
                    "scientific_names": ["Corvus corax"],
                }
            }
        }
    )

    engine.process_detection({
        "scientific_name": "Corvus corax",
        "confidence": 0.94,
        "recording_path": "/tmp/sample.wav",
    })

    assert len(events) == 1
    event_dict = events[0].to_dict()
    assert event_dict["name"] == "rare_species"
    assert event_dict["species"]["scientific_name"] == "Corvus corax"


def test_first_detection_only_emits_once():
    engine, events = _collect_events(
        {"rules": {"first_detection": {"enabled": True}}}
    )

    detection = {
        "scientific_name": "Aphelocoma californica",
        "species_id": "apca",
    }

    engine.process_detection(detection)
    engine.process_detection(detection)

    assert len(events) == 1
    assert events[0].name == "first_detection"


def test_first_return_respects_period():
    engine, events = _collect_events(
        {"rules": {"first_return": {"enabled": True, "period": "2 months"}}}
    )

    species_id = "cardinalis-cardinalis"
    # simulate last detection 90 days ago
    engine._recent_detections[species_id] = datetime.utcnow() - timedelta(days=90)

    engine.process_detection({
        "species_id": species_id,
        "scientific_name": "Cardinalis cardinalis",
    })

    assert len(events) == 1
    assert events[0].name == "first_return"


def test_event_to_dict_serialization():
    engine, events = _collect_events(
        {
            "rules": {
                "rare_species": {
                    "enabled": True,
                    "scientific_names": ["Corvus corax"],
                }
            }
        }
    )

    engine.process_detection({
        "scientific_name": "Corvus corax",
        "confidence": 0.94,
        "recording_path": "/tmp/sample.wav",
    })

    event = events[0]
    serialized = event.to_dict()
    assert "detected_at" in serialized
    assert serialized["detection"]["recording_path"] == "/tmp/sample.wav"
