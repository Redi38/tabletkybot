"""
Resolves a free-form "City, Country" string (e.g. "Варшава, Польща") into
an IANA timezone name (e.g. "Europe/Warsaw"), using OpenStreetMap/Nominatim
for geocoding and timezonefinder for the coordinate -> timezone lookup.
timezonefinder works fully offline once installed, so only the geocoding
step needs a network call.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)

_geolocator = Nominatim(user_agent="medbot_timezone_lookup")
_tf = TimezoneFinder()


async def resolve_timezone_from_place(place_text: str) -> str | None:
    """
    Takes a free-form place description ("City, Country" or just "City")
    and returns the IANA timezone name for it, or None if the place
    couldn't be geocoded or no timezone could be determined.
    """
    loop = asyncio.get_event_loop()
    try:
        location = await loop.run_in_executor(None, _geolocator.geocode, place_text)
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.warning(f"Geocoding failed for '{place_text}': {e}")
        return None

    if not location:
        return None

    tz_name = _tf.timezone_at(lat=location.latitude, lng=location.longitude)
    if not tz_name:
        logger.warning(f"No timezone found for coordinates ({location.latitude}, {location.longitude})")
        return None

    return tz_name


def format_timezone_display(tz_name: str | None) -> str | None:
    """
    Converts an IANA timezone name into a human-friendly display string,
    e.g. "Europe/Kyiv" -> "Kyiv (UTC+3)". Uses the current UTC offset,
    so it automatically reflects DST where applicable.
    """
    if not tz_name:
        return None

    city = tz_name.split("/")[-1].replace("_", " ")

    try:
        offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
    except Exception:
        return city

    if offset is None:
        return city

    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)

    if minutes:
        offset_str = f"UTC{sign}{hours}:{minutes:02d}"
    else:
        offset_str = f"UTC{sign}{hours}"

    return f"{city} ({offset_str})"
