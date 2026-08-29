"""
The MIT License (MIT)

Copyright (c) 2015-present Rapptz; lolka fork

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

# Внутренний модуль: WebRTC voice-слой для VoiceClient.
# lolka voice использует WebRTC (как клиенты lolka), а не Discord-voice/libsodium.
# Импортируется ЛЕНИВО из voice_client.py, чтобы `import lolka` не требовал extras.
# Зависит от extras "voice" (см. pyproject.toml).

from __future__ import annotations

import asyncio
import gc
import logging
import time
from fractions import Fraction
from typing import Any, Callable, Dict, Optional

import array as _array
import sys as _sys
import aiohttp
import av

try:
    import audioop as _audioop
except ImportError:
    _audioop = None

_IS_BIG_ENDIAN = _sys.byteorder == 'big'

from aiortc import MediaStreamTrack

from pymediasoup import Device, AiortcHandler
from pymediasoup.rtp_parameters import RtpCapabilities, RtpParameters
from pymediasoup.models.transport import IceParameters, IceCandidate, DtlsParameters

try:
    from .opus import Encoder as _OpusEncoder
    from .player import AudioSource
except (ImportError, ValueError):
    from lolka.opus import Encoder as _OpusEncoder
    from lolka.player import AudioSource

_log = logging.getLogger('lolka.voice')

# 20 мс аудио: 48 кГц, stereo, s16le.
SAMPLE_RATE = _OpusEncoder.SAMPLING_RATE
CHANNELS = _OpusEncoder.CHANNELS
SAMPLES_PER_FRAME = _OpusEncoder.SAMPLES_PER_FRAME
FRAME_BYTES = _OpusEncoder.FRAME_SIZE
SILENCE = b'\x00' * FRAME_BYTES


class SourceAudioTrack(MediaStreamTrack):
    """Постоянный исходящий аудио-трек для aiortc.

    Отдаёт 20-мс кадры из активного :class:`AudioSource`; при отсутствии
    источника — тишину. :meth:`set_source` переключает источник без
    пересоздания трека/producer, поэтому play()/stop()/смена source дешёвые.
    Поддерживает как PCM-источники, так и Opus (декодирует в PCM).
    """

    kind = 'audio'

    def __init__(self) -> None:
        super().__init__()
        self._source: Optional[AudioSource] = None
        self._after: Optional[Callable[[Optional[Exception]], Any]] = None
        self._decoder = None
        self._paused = False
        self._samples = 0
        self._start: Optional[float] = None
        self._play_event = asyncio.Event()
        self._silence_frames_left = 0

        # Smart Auto-Ducking: Плавная регулировка громкости (gain LERP)
        self._current_gain: float = 1.0
        self._target_gain: float = 1.0
        self._fade_step: float = 0.0
        self._fade_frames_left: int = 0

    def set_gain(self, target: float, fade_ms: int = 200) -> None:
        """Плавно меняет громкость трека (gain 0.0 .. 1.0) за fade_ms миллисекунд."""
        target = max(0.0, min(1.0, float(target)))
        if fade_ms <= 0:
            self._current_gain = target
            self._target_gain = target
            self._fade_frames_left = 0
            self._fade_step = 0.0
            return

        fade_frames = max(1, int(fade_ms / 20))  # 20 мс на кадр
        self._target_gain = target
        self._fade_frames_left = fade_frames
        self._fade_step = (target - self._current_gain) / fade_frames

    @staticmethod
    def _apply_gain(pcm_data: bytes, gain: float) -> bytes:
        """Масштабирует громкость s16le stereo PCM массива байтов."""
        if gain <= 0.0:
            return SILENCE
        # Fast Path: на 100% громкости отдаем PCM напрямую без пересчета
        if 0.999 <= gain <= 1.001:
            return pcm_data

        if _audioop is not None:
            try:
                return _audioop.mul(pcm_data, 2, gain)
            except Exception:
                pass

        # Fallback с поддержкой правильного порядка байтов
        samples = _array.array('h', pcm_data)
        if _IS_BIG_ENDIAN:
            samples.byteswap()
        for i in range(len(samples)):
            val = int(samples[i] * gain)
            samples[i] = max(-32768, min(32767, val))
        if _IS_BIG_ENDIAN:
            samples.byteswap()
        return samples.tobytes()

    def set_source(
        self,
        source: Optional[AudioSource],
        after: Optional[Callable[[Optional[Exception]], Any]] = None,
    ) -> None:
        old = self._source
        self._source = source
        self._after = after
        self._decoder = None
        self._paused = False
        
        if source is not None:
            self._start = None  # Сбрасываем тайминг при установке нового источника
            self._play_event.set()
        else:
            self._play_event.clear()
            self._silence_frames_left = 5  # Плавное затухание (100 мс тишины)
            
        if source is not None and source.is_opus():
            try:
                from . import opus
            except ImportError:
                from lolka import opus

            self._decoder = opus.Decoder()
        if old is not None and old is not source:
            try:
                old.cleanup()
            except Exception:
                pass

    @property
    def source(self) -> Optional[AudioSource]:
        return self._source

    @property
    def is_playing(self) -> bool:
        return self._source is not None and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._source is not None and self._paused

    def pause(self) -> None:
        self._paused = True
        self._play_event.clear()
        self._silence_frames_left = 5

    def resume(self) -> None:
        self._paused = False
        self._start = None  # Сбрасываем тайминг, чтобы не нагонять время паузы
        self._play_event.set()

    def _finish(self, error: Optional[Exception]) -> None:
        after = self._after
        self._source = None
        self._after = None
        self._decoder = None
        self._play_event.clear()
        self._silence_frames_left = 5
        if after is not None:
            try:
                after(error)
            except Exception:
                _log.exception('ошибка в after-callback')

    async def recv(self) -> 'av.AudioFrame':
        # Silence suppression: если воспроизведения нет и тишина отправлена, ждем начала игры
        if not self.is_playing and self._silence_frames_left <= 0:
            await self._play_event.wait()

        # Держим реальный темп 20 мс/кадр (иначе aiortc захлебнётся кадрами).
        if self._start is None:
            self._start = time.monotonic() - (self._samples / SAMPLE_RATE)
        target = self._start + (self._samples / SAMPLE_RATE)
        delay = target - time.monotonic()
        
        # Корректировка времени при отставании (защита от catch-up loop и 100% CPU)
        # Сдвигаем self._start так, чтобы текущее время соответствовало self._samples (PTS)
        if delay < -0.05:
            self._start = time.monotonic() - (self._samples / SAMPLE_RATE)
            delay = 0.0

        if delay > 0:
            await asyncio.sleep(delay)

        data = SILENCE
        src = self._source
        if src is not None and not self._paused:
            try:
                # Читаем асинхронно в отдельном потоке, чтобы не блокировать asyncio loop
                chunk = await asyncio.to_thread(src.read)
                if chunk and self._decoder is not None:
                    chunk = self._decoder.decode(chunk)
            except Exception as exc:
                self._finish(exc)
                chunk = b''
            else:
                if not chunk:
                    self._finish(None)
                    # Конец трека, переходим к отправке 5 кадров тишины перед ожиданием
                    self._silence_frames_left = 5
                else:
                    if len(chunk) < FRAME_BYTES:
                        chunk = chunk + b'\x00' * (FRAME_BYTES - len(chunk))
                    data = chunk[:FRAME_BYTES]
        else:
            # Если не играем, но счетчик тишины активен — шлем тишину
            if self._silence_frames_left > 0:
                self._silence_frames_left -= 1

        # Плавная регулировка громкости (Smart Ducking LERP)
        if self._fade_frames_left > 0:
            self._current_gain += self._fade_step
            self._fade_frames_left -= 1
            if self._fade_frames_left <= 0:
                self._current_gain = self._target_gain

        if data != SILENCE and self._current_gain != 1.0:
            data = self._apply_gain(data, self._current_gain)

        # Создаем новый AudioFrame вместо переиспользования старого пула,
        # так как aiortc/PyAV освобождает буферы отправленных кадров (av_frame_unref),
        # что приводило к ошибке ValueError: PyMemoryView_FromBuffer(): info->buf must not be NULL.
        frame = av.AudioFrame(format='s16', layout='stereo', samples=SAMPLES_PER_FRAME)
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = Fraction(1, SAMPLE_RATE)
        frame.pts = self._samples
        frame.planes[0].update(data)
        self._samples += SAMPLES_PER_FRAME
        return frame


class Signaling:
    """JSON-RPC поверх WebSocket к голосовому серверу.

    Форматы:
      request:      {id, method, data}
      response:     {id, response: true, data} | {id, response: true, error}
      notification: {notification: true, method, data}
    """

    def __init__(self, url: str, on_notification: Callable[[str, dict], Any]) -> None:
        self._url = url
        self._on_notification = on_notification
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._reader: Optional[asyncio.Task] = None
        self._closed = False

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url, heartbeat=15)
        self._reader = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                obj = msg.json()
                if obj.get('response'):
                    fut = self._pending.pop(obj.get('id'), None)
                    if fut is not None and not fut.done():
                        err = obj.get('error')
                        if err:
                            fut.set_exception(RuntimeError(str(err)))
                        else:
                            fut.set_result(obj.get('data') or {})
                elif obj.get('notification'):
                    try:
                        await self._on_notification(obj.get('method'), obj.get('data') or {})
                    except Exception:
                        _log.exception('ошибка обработки нотификации %s', obj.get('method'))
        except Exception:
            if not self._closed:
                _log.exception('сигналинг: обрыв чтения')
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError('signaling closed'))
            self._pending.clear()

    async def request(self, method: str, data: Optional[dict] = None, timeout: float = 15.0) -> dict:
        assert self._ws is not None
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send_json({'id': rid, 'method': method, 'data': data or {}})
        return await asyncio.wait_for(fut, timeout=timeout)

    async def close(self) -> None:
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()


class VoiceConnection:
    """Один сеанс WebRTC-войса: сигналинг, транспорты, producer, consumers.

    Инкапсулирует всю низкоуровневую механику; :class:`~lolka.VoiceClient` —
    тонкий фасад поверх этого класса.
    """

    def __init__(self, endpoint: str, token: str, on_receive_track: Optional[Callable] = None, guild_id: Optional[int] = None, bot_user_id: Optional[int] = None) -> None:
        self._endpoint = endpoint
        self._token = token
        self._on_receive_track = on_receive_track
        self.guild_id: Optional[int] = guild_id
        self.bot_user_id: Optional[int] = bot_user_id

        self.signaling: Optional[Signaling] = None
        self.device: Optional[Device] = None
        self.send_transport = None
        self.recv_transport = None
        self.producer = None
        self.out_track: Optional[SourceAudioTrack] = None
        self._send_connected = asyncio.Event()
        self._recv_connected = asyncio.Event()
        self.consumers: Dict[str, Any] = {}
        self._closed = False

        # Smart Auto-Ducking (Событийное приглушение без микрофонов)
        self._speaking_users: set[Any] = set()
        self._ducking_enabled: bool = True
        self._ducking_level: float = 0.35
        self._watchdog_task: Optional[asyncio.Task] = None
        self._recovery_task: Optional[asyncio.Task] = None

        # Soundscapes (Фоновые Атмосферы)
        self._soundscapes_enabled: bool = True
        self._current_soundscape: Optional[str] = None

    def _signaling_url(self) -> str:
        ep = self._endpoint or ''
        if '://' not in ep:
            ep = 'ws://' + ep
        sep = '&' if '?' in ep else '?'
        return f'{ep}{sep}token={self._token}'

    async def start(self) -> None:
        try:
            import db
            if self.guild_id:
                cfg = await db.get_ducking_config(self.guild_id)
                self._ducking_enabled = cfg.get("ducking_enabled", True)
                self._ducking_level = float(cfg.get("ducking_level", 0.35))
                _log.info("voice: загружены настройки Ducking для гильдии %s: enabled=%s level=%s", self.guild_id, self._ducking_enabled, self._ducking_level)

                s_cfg = await db.get_soundscapes_config(self.guild_id)
                self._soundscapes_enabled = s_cfg.get("soundscapes_enabled", True)
                _log.info("voice: загружены настройки Soundscapes для гильдии %s: enabled=%s", self.guild_id, self._soundscapes_enabled)
        except Exception as e:
            _log.debug("voice: Ошибка загрузки ducking/soundscapes config при старте: %s", e)

        self.signaling = Signaling(self._signaling_url(), self._on_notification)
        await self.signaling.connect()

        caps = await self.signaling.request('getRouterRtpCapabilities', {})
        self.device = Device(handlerFactory=AiortcHandler.createFactory())
        await self.device.load(routerRtpCapabilities=RtpCapabilities(**caps))

        await self._create_send_transport()
        await self._create_recv_transport()

        self.out_track = SourceAudioTrack()
        self.producer = await self.send_transport.produce(
            track=self.out_track,
            stopTracks=False,
            appData={'source': 'mic'},
        )
        _log.debug('voice: producer создан id=%s', self.producer.id)

        res = await self.signaling.request('getProducers', {})
        for p in res.get('producers', []):
            await self._consume(p['producerId'], p.get('userId'), p.get('kind'))

    async def _create_send_transport(self) -> None:
        params = await self.signaling.request('createWebRtcTransport', {'direction': 'send'})
        self.send_transport = self.device.createSendTransport(
            id=params['id'],
            iceParameters=IceParameters(**params['iceParameters']),
            iceCandidates=[IceCandidate(**c) for c in params['iceCandidates']],
            dtlsParameters=DtlsParameters(**params['dtlsParameters']),
            sctpParameters=None,
        )

        @self.send_transport.on('connect')
        async def _on_connect(dtlsParameters):
            await self.signaling.request(
                'connectTransport',
                {'transportId': self.send_transport.id, 'dtlsParameters': dtlsParameters.model_dump(exclude_none=True)},
            )
            self._send_connected.set()

        @self.send_transport.on('produce')
        async def _on_produce(kind, rtpParameters, appData):
            # ждём подтверждения connectTransport, иначе produce может уйти раньше
            await self._send_connected.wait()
            res = await self.signaling.request(
                'produce',
                {
                    'transportId': self.send_transport.id,
                    'kind': kind,
                    'rtpParameters': rtpParameters.model_dump(exclude_none=True),
                    'appData': appData or {},
                },
            )
            return res['id']

    async def _create_recv_transport(self) -> None:
        params = await self.signaling.request('createWebRtcTransport', {'direction': 'recv'})
        self.recv_transport = self.device.createRecvTransport(
            id=params['id'],
            iceParameters=IceParameters(**params['iceParameters']),
            iceCandidates=[IceCandidate(**c) for c in params['iceCandidates']],
            dtlsParameters=DtlsParameters(**params['dtlsParameters']),
            sctpParameters=None,
        )

        @self.recv_transport.on('connect')
        async def _on_connect(dtlsParameters):
            await self.signaling.request(
                'connectTransport',
                {'transportId': self.recv_transport.id, 'dtlsParameters': dtlsParameters.model_dump(exclude_none=True)},
            )
            self._recv_connected.set()

    async def _consume(self, producer_id: str, user_id: Any, kind: Any) -> None:
        # Отключаем приём чужого звука, чтобы избежать утечки памяти в очередях aiortc
        return

    async def _on_notification(self, method: str, data: dict) -> None:
        _log.info('voice WS notification received: method=%s data=%s', method, data)
        if method == 'newProducer':
            await self._consume(data.get('producerId'), data.get('userId'), data.get('kind'))
        elif method in ('consumerClosed', 'producerClosed'):
            pid = data.get('producerId')
            if pid:
                self.consumers.pop(pid, None)
        elif method in ('peerTalking', 'speaking', 'userSpeaking', 'speakingState', 'activeSpeaker', 'dominantSpeaker'):
            user_id = data.get('userId') or data.get('user_id')
            _log.info('voice WS speaking event detected (START): user=%s', user_id)
            self.handle_speaking_event(user_id, True)
        elif method in ('peerStoppedTalking', 'speakingStopped', 'userStoppedSpeaking'):
            user_id = data.get('userId') or data.get('user_id')
            _log.info('voice WS speaking event detected (STOP): user=%s', user_id)
            self.handle_speaking_event(user_id, False)
        elif method == 'kicked':
            await self.close()

    def handle_speaking_event(self, user_id: Any, speaking: bool) -> None:
        """Обрабатывает WebSocket-события активности речи участников с Watchdog и Hysteresis."""
        if not self._ducking_enabled or not self.out_track:
            return

        # Игнорируем события речи для самого бота
        if user_id is not None and self.bot_user_id is not None:
            try:
                if int(user_id) == int(self.bot_user_id):
                    return
            except (ValueError, TypeError):
                pass

        if speaking:
            if user_id is not None:
                self._speaking_users.add(user_id)
            
            # Отменяем восстановление, так как кто-то говорит
            if self._recovery_task and not self._recovery_task.done():
                self._recovery_task.cancel()
                self._recovery_task = None
                
            # Занижаем громкость до 35%
            self.out_track.set_gain(self._ducking_level, fade_ms=150)

            # Перезапускаем Watchdog таймер (5 секунд)
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
            self._watchdog_task = asyncio.create_task(self._ducking_watchdog_loop())
        else:
            if user_id is not None:
                self._speaking_users.discard(user_id)
            
            if not self._speaking_users:
                if self._watchdog_task and not self._watchdog_task.done():
                    self._watchdog_task.cancel()
                    self._watchdog_task = None
                if not self._recovery_task or self._recovery_task.done():
                    self._recovery_task = asyncio.create_task(self._ducking_recovery_loop())

    async def _ducking_watchdog_loop(self) -> None:
        """Watchdog: сбрасывает приглушение через 5 секунд, если событие 'speaking: False' было потеряно."""
        try:
            await asyncio.sleep(5.0)
            _log.warning("voice: Ducking watchdog сработал (событие speaking:False потеряно). Сброс приглушения.")
            self._speaking_users.clear()
            if self.out_track:
                self.out_track.set_gain(1.0, fade_ms=300)
        except asyncio.CancelledError:
            pass

    async def _ducking_recovery_loop(self) -> None:
        """Hysteresis: ждёт 1 секунду после окончания речи перед возвратом 100% громкости."""
        try:
            await asyncio.sleep(1.0)
            if not self._speaking_users and self.out_track:
                self.out_track.set_gain(1.0, fade_ms=300)
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.consumers.clear()
        
        for task in (self._watchdog_task, self._recovery_task):
            if task and not task.done():
                task.cancel()
        self._watchdog_task = None
        self._recovery_task = None
        self._speaking_users.clear()

        if self.out_track is not None:
            try:
                self.out_track.set_source(None)
                self.out_track.stop()
            except Exception:
                pass
            self.out_track = None
        if self.producer is not None:
            try:
                await self.producer.close()
            except Exception:
                pass
            self.producer = None
        for tr in (self.send_transport, self.recv_transport):
            if tr is not None:
                try:
                    await tr.close()
                except Exception:
                    pass
        self.send_transport = None
        self.recv_transport = None
        self.device = None
        self._on_receive_track = None
        if self.signaling is not None:
            try:
                await self.signaling.close()
            except Exception:
                pass
            self.signaling = None
        # Force a collection to clean up WebRTC C structures
        gc.collect()


# ── Монкипатч VoiceClient.connect для проброса guild_id в VoiceConnection ──
try:
    import lolka.voice_client as _vc_module
    _orig_voice_client_connect = _vc_module.VoiceClient.connect

    async def _patched_voice_client_connect(self, *, reconnect: bool = True, timeout: float = 30.0, self_deaf: bool = False, self_mute: bool = False) -> None:
        effective_timeout = max(timeout or 30.0, 30.0)
        self._timeout = effective_timeout
        guild = self.channel.guild
        await guild.change_voice_state(channel=self.channel, self_mute=self_mute, self_deaf=self_deaf)
        await asyncio.wait_for(
            asyncio.gather(self._state_ready.wait(), self._server_ready.wait()),
            timeout=effective_timeout,
        )
        if not self._endpoint or not self._token:
            from lolka.errors import ClientException
            raise ClientException('voice server did not provide an endpoint/token')

        bot_user_id = getattr(getattr(self, "client", None), "user", None)
        bot_id = getattr(bot_user_id, "id", None) if bot_user_id else None

        _log.info('voice: connecting to voice endpoint=%s (guild_id=%s)', self._endpoint, guild.id)
        self._conn = VoiceConnection(
            self._endpoint, self._token, on_receive_track=self._handle_track, guild_id=guild.id, bot_user_id=bot_id
        )
        self._conn.guild_id = guild.id
        await self._conn.start()
        self._connected = True

    _vc_module.VoiceClient.connect = _patched_voice_client_connect
    _log.info("VoiceClient.connect патч для проброса guild_id успешно усыновлён!")
except Exception as _ex:
    _log.warning("Не удалось пропатчить VoiceClient.connect: %s", _ex)


# ── Монкипатч aioice.stun.Transaction.__retry ──────────────────────────────────
# Проблема: при закрытии ICE-транспорта pending call_later таймеры ещё живут и
# вызывают __retry после того, как self.__protocol.transport (или asyncio loop)
# уже занулён → AttributeError: 'NoneType' has no attribute 'sendto' /
# 'call_exception_handler'.
#
# Верификация (aioice 0.10.2): __retry сначала вызывает send_stun, ПОТОМ
# планирует следующий call_later. Перехват AttributeError до call_later
# гарантирует, что цепочка таймеров останавливается чисто — новые не создаются.
# Успешные STUN-транзакции не затрагиваются (try/except входит только при исключении).
try:
    import aioice.stun as _aioice_stun
    if hasattr(_aioice_stun.Transaction, "_Transaction__retry"):
        _orig_stun_retry = _aioice_stun.Transaction._Transaction__retry

        def _patched_stun_retry(self) -> None:  # type: ignore[override]
            try:
                _orig_stun_retry(self)
            except AttributeError:
                # Транспорт уже закрыт — таймер устарел, подавляем безопасно.
                # Новый call_later не будет запланирован (send_stun до call_later).
                _log.debug(
                    "aioice STUN retry: AttributeError подавлен для закрытого транспорта"
                )

        _aioice_stun.Transaction._Transaction__retry = _patched_stun_retry
        _log.info("aioice.stun.Transaction.__retry патч успешно применён (aioice 0.10.2)")
    else:
        _log.warning(
            "aioice STUN патч: метод _Transaction__retry не найден — "
            "возможно, версия aioice изменилась"
        )
except Exception as _ex:
    _log.warning("Не удалось пропатчить aioice.stun.Transaction.__retry: %s", _ex)
