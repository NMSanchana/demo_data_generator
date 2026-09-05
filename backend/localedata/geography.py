"""
Structured, validated geography data: Continent -> Country -> State/Region.

Backed by pycountry / pycountry_convert so the country and state lists are
real ISO data, not a hand-maintained list. Exposed as real pydantic/Enum
types in models.py so the API schema (and therefore the OpenAPI docs) shows
genuine dropdown-able values instead of a free-text string.

Ported from the old build's localedata/geography.py — logic unchanged.
"""

import re
import unicodedata
from enum import Enum

import pycountry
import pycountry_convert as pcc

CONTINENT_NAMES = {
    "AF": "Africa",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
    "AN": "Antarctica",
}

_MANUAL_CONTINENT_OVERRIDES = {
    "AQ": "AN",
    "TF": "AN",
    "EH": "AF",
    "PN": "OC",
    "SX": "NA",
    "TL": "AS",
    "UM": "OC",
    "VA": "EU",
}


def _country_continent_code(alpha_2: str) -> str:
    try:
        return pcc.country_alpha2_to_continent_code(alpha_2)
    except KeyError:
        return _MANUAL_CONTINENT_OVERRIDES.get(alpha_2, "AF")


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").upper()
    return normalized or "UNKNOWN"


COUNTRIES_BY_CONTINENT: dict[str, list[dict]] = {code: [] for code in CONTINENT_NAMES}
COUNTRY_BY_CODE: dict[str, dict] = {}
STATES_BY_COUNTRY: dict[str, list[dict]] = {}
STATE_BY_CODE: dict[str, dict] = {}

for country in pycountry.countries:
    continent_code = _country_continent_code(country.alpha_2)
    entry = {
        "code": country.alpha_2,
        "name": country.name,
        "continent_code": continent_code,
    }
    COUNTRIES_BY_CONTINENT[continent_code].append(entry)
    COUNTRY_BY_CODE[country.alpha_2] = entry
    STATES_BY_COUNTRY[country.alpha_2] = []

for continent_code in COUNTRIES_BY_CONTINENT:
    COUNTRIES_BY_CONTINENT[continent_code].sort(key=lambda c: c["name"])

_subdivision_name_counts: dict[str, int] = {}
for sub in pycountry.subdivisions:
    _subdivision_name_counts[sub.name] = _subdivision_name_counts.get(sub.name, 0) + 1

for sub in pycountry.subdivisions:
    country_code = sub.country_code
    if country_code not in STATES_BY_COUNTRY:
        continue
    country_name = COUNTRY_BY_CODE[country_code]["name"]
    display_name = sub.name
    if _subdivision_name_counts.get(sub.name, 0) > 1:
        display_name = f"{sub.name} ({country_name})"
    entry = {
        "code": sub.code,
        "name": display_name,
        "raw_name": sub.name,
        "country_code": country_code,
        "continent_code": COUNTRY_BY_CODE[country_code]["continent_code"],
    }
    STATES_BY_COUNTRY[country_code].append(entry)
    STATE_BY_CODE[sub.code] = entry

for country_code in STATES_BY_COUNTRY:
    STATES_BY_COUNTRY[country_code].sort(key=lambda s: s["name"])


def _build_enum(name: str, members: dict[str, str]) -> Enum:
    return Enum(name, members)


ContinentEnum = _build_enum(
    "ContinentEnum",
    {_slug(name): code for code, name in CONTINENT_NAMES.items()},
)

_country_enum_members: dict[str, str] = {}
_seen_country_keys: set[str] = set()
for code, entry in sorted(COUNTRY_BY_CODE.items(), key=lambda kv: kv[1]["name"]):
    key = _slug(entry["name"])
    if key in _seen_country_keys:
        key = f"{key}_{code}"
    _seen_country_keys.add(key)
    _country_enum_members[key] = code
CountryEnum = _build_enum("CountryEnum", _country_enum_members)

_state_enum_members: dict[str, str] = {}
_seen_state_keys: set[str] = set()
for code, entry in sorted(STATE_BY_CODE.items(), key=lambda kv: kv[1]["name"]):
    key = _slug(entry["name"])
    if key in _seen_state_keys:
        key = f"{key}_{code.replace('-', '_')}"
    _seen_state_keys.add(key)
    _state_enum_members[key] = code
StateEnum = _build_enum("StateEnum", _state_enum_members)


def continent_display_name(code: str) -> str:
    return CONTINENT_NAMES.get(code, code)


def country_display_name(code: str) -> str:
    entry = COUNTRY_BY_CODE.get(code)
    return entry["name"] if entry else code


def state_display_name(code: str) -> str:
    entry = STATE_BY_CODE.get(code)
    return entry["name"] if entry else code


def get_country(code: str) -> dict | None:
    return COUNTRY_BY_CODE.get(code)


def get_state(code: str) -> dict | None:
    return STATE_BY_CODE.get(code)


def geography_tree() -> dict:
    tree = {}
    for continent_code, countries in COUNTRIES_BY_CONTINENT.items():
        tree[continent_code] = {
            "name": continent_display_name(continent_code),
            "countries": [
                {
                    "code": c["code"],
                    "name": c["name"],
                    "states": [
                        {"code": s["code"], "name": s["name"]}
                        for s in STATES_BY_COUNTRY.get(c["code"], [])
                    ],
                }
                for c in countries
            ],
        }
    return tree


def all_states_flat() -> list[dict]:
    return [
        {
            "code": s["code"],
            "name": s["name"],
            "country_code": s["country_code"],
            "country_name": country_display_name(s["country_code"]),
            "continent_code": s["continent_code"],
            "continent_name": continent_display_name(s["continent_code"]),
        }
        for s in STATE_BY_CODE.values()
    ]


def build_geography_display(continent: str, country: str, state: str | None) -> str:
    """Human-readable string for LLM prompt context, e.g. 'Tamil Nadu, India'."""
    country_name = country_display_name(country)
    if state:
        return f"{state_display_name(state)}, {country_name}"
    return country_name
