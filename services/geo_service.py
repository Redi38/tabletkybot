"""
Resolves a free-form "City, Country" string (e.g. "Варшава, Польща") into
an IANA timezone name (e.g. "Europe/Warsaw"), using OpenStreetMap/Nominatim
for geocoding and timezonefinder for the coordinate -> timezone lookup.
timezonefinder works fully offline once installed, so only the geocoding
step needs a network call.
"""
import asyncio
import logging

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
