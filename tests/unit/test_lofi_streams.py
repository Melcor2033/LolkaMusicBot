import pytest
from src.lofi_streams import STATIONS, DEFAULT_STATION, LofiStation, get_station_by_name, get_random_station

def test_stations_presence():
    assert len(STATIONS) > 0
    assert DEFAULT_STATION == STATIONS[0]
    for station in STATIONS:
        assert isinstance(station, LofiStation)
        assert station.name
        assert station.url
        assert station.emoji
        assert station.genre

def test_get_station_by_name():
    # Test existing station case-insensitive
    station = get_station_by_name("lofi girl")
    assert station is not None
    assert station.name == "Lofi Girl"

    # Test non-existing station
    station = get_station_by_name("NonExistentStation")
    assert station is None

def test_get_random_station():
    # Without exclude
    station = get_random_station()
    assert station in STATIONS

    # With exclude
    exclude = STATIONS[0]
    for _ in range(20): # Run multiple times to reduce chance of false passes
        station = get_random_station(exclude=exclude)
        assert station in STATIONS
        assert station != exclude

    # If only one station is in list, get_random_station handles it
    # We mock STATIONS temporarily with a single element
    import src.lofi_streams
    original_stations = src.lofi_streams.STATIONS
    try:
        single_station = STATIONS[0]
        src.lofi_streams.STATIONS = [single_station]
        
        # Excluding the only station should fallback to that station
        res = get_random_station(exclude=single_station)
        assert res == single_station
    finally:
        src.lofi_streams.STATIONS = original_stations
