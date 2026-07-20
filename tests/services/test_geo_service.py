"""
Tests for services/geo_service.py

resolve_timezone_from_place() hits Nominatim over the network, so the
geolocator itself is always mocked - we never make real HTTP calls in tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from services import geo_service


class FakeLocation:
    def __init__(self, latitude, longitude):
        self.latitude = latitude
        self.longitude = longitude


# resolve_timezone_from_place
@pytest.mark.asyncio
async def test_resolve_timezone_success():
    """Successful geocode + timezonefinder lookup returns the IANA name."""
    fake_location = FakeLocation(latitude=50.0755, longitude=14.4378)  # Prague

    with patch.object(geo_service, "_geolocator") as mock_geolocator, patch.object(geo_service, "_tf") as mock_tf:
        mock_geolocator.geocode = MagicMock(return_value=fake_location)
        mock_tf.timezone_at = MagicMock(return_value="Europe/Prague")

        result = await geo_service.resolve_timezone_from_place("Прага, Чехія")

    assert result == "Europe/Prague"
    mock_geolocator.geocode.assert_called_once_with("Прага, Чехія")
    mock_tf.timezone_at.assert_called_once_with(lat=50.0755, lng=14.4378)


@pytest.mark.asyncio
async def test_resolve_timezone_place_not_found():
    """Nominatim returns None (no match) -> function returns None, no crash."""
    with patch.object(geo_service, "_geolocator") as mock_geolocator, patch.object(geo_service, "_tf") as mock_tf:
        mock_geolocator.geocode = MagicMock(return_value=None)

        result = await geo_service.resolve_timezone_from_place("Асдфасдф Йцукй")

    assert result is None
    mock_tf.timezone_at.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_timezone_no_tz_for_coordinates():
    """Coordinates resolve (e.g. open ocean) but timezonefinder finds nothing."""
    fake_location = FakeLocation(latitude=0.0, longitude=-30.0)

    with patch.object(geo_service, "_geolocator") as mock_geolocator, patch.object(geo_service, "_tf") as mock_tf:
        mock_geolocator.geocode = MagicMock(return_value=fake_location)
        mock_tf.timezone_at = MagicMock(return_value=None)

        result = await geo_service.resolve_timezone_from_place("Middle of the Atlantic")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_timezone_geocoder_timeout():
    """GeocoderTimedOut is caught and treated as a resolution failure."""
    from geopy.exc import GeocoderTimedOut

    with patch.object(geo_service, "_geolocator") as mock_geolocator:
        mock_geolocator.geocode = MagicMock(side_effect=GeocoderTimedOut("timed out"))

        result = await geo_service.resolve_timezone_from_place("Kharkiv, Ukraine")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_timezone_geocoder_service_error():
    """GeocoderServiceError (e.g. Nominatim 5xx) is caught the same way."""
    from geopy.exc import GeocoderServiceError

    with patch.object(geo_service, "_geolocator") as mock_geolocator:
        mock_geolocator.geocode = MagicMock(side_effect=GeocoderServiceError("boom"))

        result = await geo_service.resolve_timezone_from_place("Kharkiv, Ukraine")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_timezone_runs_geocode_in_executor():
    """geocode() must not block the event loop - it should go through run_in_executor."""
    fake_location = FakeLocation(latitude=1.0, longitude=2.0)

    with (
        patch.object(geo_service, "_geolocator") as mock_geolocator,
        patch.object(geo_service, "_tf") as mock_tf,
        patch("asyncio.get_event_loop") as mock_get_loop,
    ):
        mock_loop = MagicMock()

        async def fake_run_in_executor(executor, func, *args):
            return func(*args)

        mock_loop.run_in_executor = fake_run_in_executor
        mock_get_loop.return_value = mock_loop
        mock_geolocator.geocode = MagicMock(return_value=fake_location)
        mock_tf.timezone_at = MagicMock(return_value="Europe/Kyiv")

        result = await geo_service.resolve_timezone_from_place("Kyiv, Ukraine")

    assert result == "Europe/Kyiv"


# format_timezone_display
def test_format_timezone_display_none_input():
    assert geo_service.format_timezone_display(None) is None


def test_format_timezone_display_positive_whole_hour_offset():
    """Asia/Tokyo has no DST, so UTC+9 is stable year-round - safe to assert exactly."""
    result = geo_service.format_timezone_display("Asia/Tokyo")
    assert result == "Tokyo (UTC+9)"


def test_format_timezone_display_negative_offset():
    """America/Sao_Paulo dropped DST in 2019, so UTC-3 is stable - safe to assert exactly."""
    result = geo_service.format_timezone_display("America/Sao_Paulo")
    assert result == "Sao Paulo (UTC-3)"


def test_format_timezone_display_replaces_underscores_in_city_name():
    result = geo_service.format_timezone_display("America/New_York")
    assert result.startswith("New York (")


def test_format_timezone_display_half_hour_offset():
    """Asia/Kolkata is UTC+5:30 year-round, no DST - stable for assertions."""
    result = geo_service.format_timezone_display("Asia/Kolkata")
    assert result == "Kolkata (UTC+5:30)"


def test_format_timezone_display_invalid_tz_name_falls_back_to_city():
    """An invalid/unknown IANA name should degrade gracefully to just the city label."""
    result = geo_service.format_timezone_display("Not/A_Real_Zone")
    assert result == "A Real Zone"


def test_format_timezone_display_offset_none_falls_back_to_city():
    """If utcoffset() ever returns None, we should still return the city label, not crash."""
    with patch.object(geo_service, "ZoneInfo"):
        fake_dt = MagicMock()
        fake_dt.utcoffset.return_value = None
        with patch.object(geo_service, "datetime") as mock_datetime:
            mock_datetime.now.return_value = fake_dt
            result = geo_service.format_timezone_display("Europe/Kyiv")

    assert result == "Kyiv"
