from __future__ import annotations

from datetime import timedelta
from typing import Dict, Iterable, List

from .models import AlertContext, AlertEvent
from .rules.base import AlertRule
from .rules.first_detection import FirstDetectionRule
from .rules.first_return import FirstReturnRule
from .rules.rare_species import RareSpeciesRule


def _parse_period(raw: str) -> timedelta:
    value, unit = raw.strip().split()
    amount = int(value)
    unit = unit.lower()
    if unit.startswith("day"):
        return timedelta(days=amount)
    if unit.startswith("week"):
        return timedelta(weeks=amount)
    if unit.startswith("month"):
        return timedelta(days=30 * amount)
    if unit.startswith("year"):
        return timedelta(days=365 * amount)
    raise ValueError(f"Unsupported period unit: {unit}")


def build_rules(config: Dict[str, dict]) -> List[AlertRule]:
    rules: List[AlertRule] = []
    rules_config = config.get("rules") if isinstance(config, dict) else {}

    rare_conf = rules_config.get("rare_species")
    if isinstance(rare_conf, dict) and rare_conf.get("enabled"):
        names = rare_conf.get("scientific_names") or []
        if isinstance(names, list) and names:
            rules.append(RareSpeciesRule(names))

    first_detection_conf = rules_config.get("first_detection")
    if isinstance(first_detection_conf, dict) and first_detection_conf.get("enabled", True):
        rules.append(FirstDetectionRule())

    first_return_conf = rules_config.get("first_return")
    if isinstance(first_return_conf, dict) and first_return_conf.get("enabled"):
        period_raw = first_return_conf.get("period", "2 months")
        period = _parse_period(str(period_raw))
        rules.append(FirstReturnRule(period))

    return rules
