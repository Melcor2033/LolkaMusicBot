import asyncio
import random
from typing import Any, Optional

class QueueManager(dict):
    """
    Управление очередью воспроизведения.
    Наследуется от dict для 100% совместимости с существующим кодом Cogs,
    но предоставляет методы и свойства для тестирования и чистой логики.
    """
    
    def __init__(self):
        super().__init__({
            "tracks": [],
            "index": 0,
            "played_count": 0,
            "source": None,
            "station_id": None,
            "batch_id": None,
            "radio_session_id": None,
            "version": 0,
            "loop": False,
            "shuffle": False,
            "current_track_id": None,
            "np_msg": None,
            "channel": None,
            "prev_history": [],
            "played_ids": set(),
            "pending_tracks": [],
            "pending_type": None,
            "pending_source_id": None,
            "is_refilling_pending": False,
            "fail_count": 0,
            "total_fetch_count": 0,
            "initiator_id": None,
        })
        # Lock для предотвращения гонки между фоновым prefetch
        # и синхронным refill в play_track
        self._refill_wave_lock: asyncio.Lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def add_track(self, track: dict[str, Any]) -> None:
        """Добавить трек в конец очереди."""
        self.tracks.append(track)
        
    def extend_tracks(self, tracks: list[dict[str, Any]]) -> None:
        """Добавить несколько треков в конец очереди."""
        self.tracks.extend(tracks)

    def clear(self) -> None:
        """Очистить очередь и сбросить счетчики."""
        self.tracks.clear()
        self.index = 0
        self.played_count = 0
        self.prev_history.clear()
        self.current_track_id = None
        self["played_ids"] = set()
        self["pending_tracks"].clear()
        self["is_refilling_pending"] = False
        self["version"] = self.get("version", 0) + 1
        
    def clear_upcoming(self) -> None:
        """Очистить предстоящие треки в очереди, оставив только текущий."""
        current = self.get_current()
        if current:
            self["tracks"] = [current]
            self.index = 0
        else:
            self["tracks"] = []
            self.index = 0
        self["pending_tracks"].clear()
        self["is_refilling_pending"] = False
        self["version"] = self.get("version", 0) + 1
            
    def reset_source_meta(self) -> None:
        """Сбросить мета-информацию об источнике (например, при поиске нового трека)."""
        self.source = None
        self.station_id = None
        self.batch_id = None
        self.radio_session_id = None
        self.total_fetch_count = 0

    def get_current(self) -> Optional[dict[str, Any]]:
        """Получить текущий трек по индексу."""
        if 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    def get_next(self) -> Optional[dict[str, Any]]:
        """Перейти к следующему треку и вернуть его."""
        if not self.tracks:
            return None
            
        current = self.get_current()
        if current:
            self.prev_history.append(current)
            if len(self.prev_history) > 50:
                self.prev_history.pop(0)

        self.played_count += 1

        if self.loop and current:
            return current

        if self.shuffle:
            if len(self.tracks) > 1:
                options = [i for i in range(len(self.tracks)) if i != self.index]
                self.index = random.choice(options)
            return self.tracks[self.index]

        self.index += 1
        if self.index < len(self.tracks):
            return self.tracks[self.index]
            
        return None

    def get_previous(self) -> Optional[dict[str, Any]]:
        """Перейти к предыдущему треку."""
        if self.prev_history:
            track = self.prev_history.pop()
            try:
                self.index = next(i for i, t in enumerate(self.tracks) if t.get("track_id") == track.get("track_id"))
            except StopIteration:
                self.tracks.insert(self.index, track)
            
            return self.tracks[self.index]
            
        if not self.shuffle and self.index > 0:
            self.index -= 1
            return self.tracks[self.index]
            
        return None

    def remove_track(self, index: int) -> bool:
        """Удалить трек по индексу."""
        if 0 <= index < len(self.tracks):
            if index < self.index:
                self.index -= 1
            self.tracks.pop(index)
            return True
        return False
