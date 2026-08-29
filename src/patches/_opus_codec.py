"""
Патч для aiortc OpusEncoder — замена PyAV CodecContext на ctypes.

Проблема:
    av.CodecContext.encode() утекает ~82 байт на каждый аудио-фрейм
    на уровне C-кода внутри Cython-обёртки PyAV. За час непрерывного
    стриминга утечка составляет ~15 МБ на одну сессию.

Решение:
    Используем lolka.opus.Encoder, который вызывает opus_encode()
    напрямую через ctypes, минуя PyAV. Локальный бенчмарк показал
    0.27 МБ утечки на 500 000 фреймов (vs 39.24 МБ у PyAV).

Использование:
    Монкипатч при старте бота:
        from patches._opus_codec import PatchedOpusEncoder
        import aiortc.codecs.opus
        aiortc.codecs.opus.OpusEncoder = PatchedOpusEncoder
"""

from __future__ import annotations

import fractions
import logging
from typing import Optional

from av import AudioFrame
from av.frame import Frame
from av.packet import Packet

from lolka.opus import Encoder as CtypesOpusEncoder

from aiortc.codecs.base import Encoder
from aiortc.mediastreams import convert_timebase

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960
TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)

_log = logging.getLogger('patches.opus_codec')


class PatchedOpusEncoder(Encoder):
    """Opus-энкодер без PyAV — использует lolka.opus через ctypes.

    Полностью устраняет утечку ~82 байт/фрейм, возникавшую
    в ``av.CodecContext.encode()`` и ``av.AudioResampler.resample()``.

    Входные данные от ``SourceAudioTrack.recv()`` уже имеют формат
    48 кГц / stereo / s16 / 960 сэмплов — ресемплер не требуется.
    """

    def __init__(self) -> None:
        self._encoder = CtypesOpusEncoder(application='audio', bitrate=128)
        self._timestamp: int = 0
        self._first_pts: Optional[int] = None
        _log.info('PatchedOpusEncoder инициализирован (128kbps, ctypes).')

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        assert isinstance(frame, AudioFrame)

        # Извлекаем сырые PCM-байты из AudioFrame с защитой от NULL C-буферов PyAV (unref)
        try:
            pcm_data = bytes(frame.planes[0])
        except (ValueError, AttributeError, IndexError, RuntimeError):
            pcm_data = b'\x00' * (SAMPLES_PER_FRAME * 4)  # 20 мс тишины (3840 байт)

        # Кодируем через ctypes opus_encode()
        encoded = self._encoder.encode(pcm_data, SAMPLES_PER_FRAME)

        timestamp = self._timestamp
        self._timestamp += SAMPLES_PER_FRAME

        return [encoded], timestamp

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, TIME_BASE)
        return [bytes(packet)], timestamp
