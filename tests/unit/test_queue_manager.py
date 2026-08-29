import pytest
from src.utils.queue_manager import QueueManager

def test_initialization():
    qm = QueueManager()
    assert qm.tracks == []
    assert qm.index == 0
    assert qm.played_count == 0
    assert qm.loop is False
    assert qm.shuffle is False

def test_add_track():
    qm = QueueManager()
    qm.add_track({"track_id": "1", "title": "Test 1"})
    assert len(qm.tracks) == 1
    assert qm.tracks[0]["title"] == "Test 1"

def test_extend_tracks():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}])
    assert len(qm.tracks) == 2

def test_clear():
    qm = QueueManager()
    qm.add_track({"track_id": "1"})
    qm.index = 5
    qm.played_count = 10
    qm.clear()
    
    assert len(qm.tracks) == 0
    assert qm.index == 0
    assert qm.played_count == 0

def test_get_current():
    qm = QueueManager()
    assert qm.get_current() is None
    
    qm.add_track({"track_id": "1"})
    assert qm.get_current()["track_id"] == "1"

def test_get_next_normal():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}, {"track_id": "3"}])
    
    # 0 -> 1
    nxt = qm.get_next()
    assert nxt["track_id"] == "2"
    assert qm.index == 1
    assert qm.played_count == 1
    assert len(qm.prev_history) == 1
    assert qm.prev_history[0]["track_id"] == "1"
    
    # 1 -> 2
    nxt = qm.get_next()
    assert nxt["track_id"] == "3"
    assert qm.index == 2
    
    # 2 -> None (end)
    nxt = qm.get_next()
    assert nxt is None

def test_get_next_loop():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}])
    qm.loop = True
    
    nxt = qm.get_next()
    # Should stay on track 1
    assert nxt["track_id"] == "1"
    assert qm.index == 0
    
def test_get_next_shuffle():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}, {"track_id": "3"}])
    qm.shuffle = True
    
    nxt = qm.get_next()
    # It must pick something and change index
    assert nxt is not None
    assert nxt["track_id"] in ["1", "2", "3"]

def test_get_previous():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}, {"track_id": "3"}])
    
    # Move forward
    qm.get_next() # now at index 1
    qm.get_next() # now at index 2
    
    assert qm.index == 2
    assert qm.get_current()["track_id"] == "3"
    
    # Move back
    prev = qm.get_previous()
    assert prev["track_id"] == "2"
    assert qm.index == 1
    
    prev = qm.get_previous()
    assert prev["track_id"] == "1"
    assert qm.index == 0

def test_remove_track():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}, {"track_id": "3"}])
    qm.index = 1
    
    # Remove track before current
    assert qm.remove_track(0) is True
    assert len(qm.tracks) == 2
    # Index should shift left
    assert qm.index == 0
    assert qm.get_current()["track_id"] == "2"
    
    # Remove invalid
    assert qm.remove_track(10) is False

def test_played_ids():
    qm = QueueManager()
    assert isinstance(qm.played_ids, set)
    assert len(qm.played_ids) == 0
    
    qm.played_ids.add("track_1")
    assert "track_1" in qm.played_ids
    
    qm.clear()
    assert len(qm.played_ids) == 0


def test_getattr_key_error():
    qm = QueueManager()
    with pytest.raises(AttributeError):
        _ = qm.nonexistent_attribute


def test_clear_upcoming():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}, {"track_id": "3"}])
    qm.index = 1
    
    # has current track
    qm.clear_upcoming()
    assert len(qm.tracks) == 1
    assert qm.tracks[0]["track_id"] == "2"
    assert qm.index == 0
    
    # no current track
    qm.index = 5
    qm.clear_upcoming()
    assert len(qm.tracks) == 0
    assert qm.index == 0


def test_reset_source_meta():
    qm = QueueManager()
    qm.source = "test"
    qm.station_id = "test"
    qm.batch_id = "test"
    qm.radio_session_id = "test"
    qm.total_fetch_count = 10
    
    qm.reset_source_meta()
    assert qm.source is None
    assert qm.station_id is None
    assert qm.batch_id is None
    assert qm.radio_session_id is None
    assert qm.total_fetch_count == 0


def test_get_next_empty():
    qm = QueueManager()
    assert qm.get_next() is None


def test_get_next_history_limit():
    qm = QueueManager()
    qm.add_track({"track_id": "0"})
    for i in range(1, 55):
        qm.add_track({"track_id": str(i)})
        qm.get_next()
    assert len(qm.prev_history) == 50
    assert qm.prev_history[0]["track_id"] == "4"


def test_get_previous_not_in_tracks():
    qm = QueueManager()
    track1 = {"track_id": "1"}
    track2 = {"track_id": "2"}
    qm.extend_tracks([track1, track2])
    qm.get_next() # index 1, prev_history has track1
    
    # remove track1 from tracks so it's not found in self.tracks
    qm.tracks.pop(0) # now only track2 is left
    qm.index = 0
    
    prev = qm.get_previous()
    # It should insert track1 back at self.index (0)
    assert prev["track_id"] == "1"
    assert qm.tracks[0]["track_id"] == "1"
    assert qm.index == 0


def test_get_previous_fallback():
    qm = QueueManager()
    qm.extend_tracks([{"track_id": "1"}, {"track_id": "2"}])
    qm.index = 1
    # prev_history is empty
    assert len(qm.prev_history) == 0
    
    prev = qm.get_previous()
    assert prev["track_id"] == "1"
    assert qm.index == 0
    
    # index is 0, should return None
    assert qm.get_previous() is None


