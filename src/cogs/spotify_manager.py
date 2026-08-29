import re
import json
import logging
import asyncio
import aiohttp
from typing import List, Dict, Optional

import yt_dlp
import config

logger = logging.getLogger("cogs.spotify_manager")

VK_URL_PATTERN = re.compile(
    r'https?://(?:www\.|m\.)?vk\.(?:com|ru)/'
    r'(?:'
    r'(?:audio_playlist|audio|wall|audios|video|clip)-?\d+_\d+'
    r'|(?:music/)?(?:album|playlist)/-?\d+_\d+[^/]*'
    r'|audios-?\d+'
    r'|artist/[a-zA-Z0-9_-]+'
    r'|music\?z=(?:audio_playlist|playlist|album)-?\d+_\d+'
    r')'
)

def is_vk_url(url: str) -> bool:
    """Проверяет, является ли ссылка публичной ссылкой на VK Музыку/контент."""
    return bool(VK_URL_PATTERN.search(url))


VK_STR = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN0PQRSTUVWXYZO123456789+/="

def vk_o(string):
    result = []
    index2 = 0
    for i in range(len(string)):
        sym = string[i]
        try:
            index = VK_STR.index(sym)
        except ValueError:
            continue
        if index2 % 4 != 0:
            result.append((index2 % 4, (result.pop()[1] << (index2 % 4 * 2)) + (index >> (6 - index2 % 4 * 2))))
        else:
            result.append((0, index))
        index2 += 1
    
    res_bytes = bytearray()
    for item in result:
        if isinstance(item, tuple) and item[0] != 0:
            res_bytes.append(item[1] & 255)
    return res_bytes.decode('latin1', errors='ignore')

def decode_vk_audio_url(string: str) -> Optional[str]:
    """Декодирует маскированные ссылки VK audio с параметром ?extra=."""
    if not string or not isinstance(string, str):
        return None
    if string.startswith("http://") or string.startswith("https://"):
        if ".mp3" in string or ".m3u8" in string or "userapi" in string:
            return string
    if "?extra=" not in string:
        return None
    try:
        vals = string.split("?extra=", 1)[1].split("#")
        tstr = vk_o(vals[0])
        ops_list = vk_o(vals[1]).split('\x09')[::-1]

        for op_data in ops_list:
            split_op_data = op_data.split('\x0b')
            cmd = split_op_data[0]
            arg = split_op_data[1] if len(split_op_data) > 1 else None
            if cmd == 'v':
                tstr = tstr[::-1]
            elif cmd == 'r':
                arg = int(arg)
                tstr = "".join(chr(ord(c) ^ arg) for c in tstr)
            elif cmd == 'x':
                tstr = "".join(chr(ord(c) ^ ord(arg[i % len(arg)])) for i, c in enumerate(tstr))
            elif cmd == 's':
                arg = int(arg)
                tstr = "".join(chr(ord(c) ^ (i + arg)) for i, c in enumerate(tstr))

        if tstr.startswith("http://") or tstr.startswith("https://"):
            return tstr
    except Exception:
        pass
    return None


class SpotifyManager:
    def __init__(self):
        # Basic yt-dlp options
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0'
        }
        if config.YTDLP_PROXY:
            self.ydl_opts['proxy'] = config.YTDLP_PROXY

    async def parse_spotify_url(self, url: str) -> List[Dict]:
        """Парсит ссылку на Spotify и возвращает список треков через Scraping Embed."""
        tracks = []
        
        # Преобразуем обычный URL в embed URL
        # Из https://open.spotify.com/track/123 -> https://open.spotify.com/embed/track/123
        try:
            embed_url = url.replace("open.spotify.com/", "open.spotify.com/embed/")
            # Очищаем от query параметров
            embed_url = embed_url.split("?")[0]
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(embed_url, headers=headers, proxy=config.SPOTIFY_PROXY) as resp:
                    if resp.status != 200:
                        logger.error("Ошибка запроса к Spotify Embed: %s", resp.status)
                        return []
                        
                    html = await resp.text()
                    
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                logger.error("Не удалось найти JSON состояние на странице Spotify Embed.")
                return []
                
            data = json.loads(match.group(1))
            page_props = data.get('props', {}).get('pageProps', {})
            if 'state' not in page_props:
                logger.error("Ошибка: в JSON-состоянии Spotify отсутствует 'state'. Ссылка не существует (404) или заблокирована.")
                return []
                
            entity = page_props['state'].get('data', {}).get('entity')
            if not entity:
                logger.error("Ошибка: в JSON-состоянии Spotify отсутствует 'entity'.")
                return []
            
            # Извлекаем обложку сущности (если это альбом или плейлист)
            common_cover = None
            try:
                images = entity.get('visualIdentity', {}).get('image', [])
                if images:
                    common_cover = images[0].get('url')
            except Exception:
                pass
                
            e_type = entity.get('type')
            
            if e_type == 'track':
                artists = ", ".join(a.get('name', 'Unknown') for a in entity.get('artists', []))
                track_id = entity.get('id', '')
                tracks.append({
                    'id': track_id,
                    'title': entity.get('title', 'Unknown'),
                    'artist': artists,
                    'duration': int(entity.get('duration', 0) / 1000),
                    'cover': common_cover,
                    'search_query': f"{artists} - {entity.get('title', 'Unknown')}"
                })
            elif 'trackList' in entity:
                for t in entity['trackList']:
                    title = t.get('title', 'Unknown')
                    artist = t.get('subtitle', 'Unknown')
                    # trackList items do not have ID directly sometimes, we can generate a stub
                    track_id = t.get('uri', '').split(':')[-1]
                    tracks.append({
                        'id': track_id,
                        'title': title,
                        'artist': artist,
                        'duration': int(t.get('duration', 0) / 1000),
                        'cover': common_cover,
                        'search_query': f"{artist} - {title}"
                    })
                    
        except Exception as e:
            logger.error("Ошибка парсинга Spotify URL %s: %s", url, e)

        return tracks

    def _sync_parse_vk_url(self, url: str) -> List[Dict]:
        """Синхронный парсинг ссылки VK с помощью yt-dlp flat-extraction."""
        tracks = []
        # Преобразуем vk.ru в vk.com для корректной работы экстракторов yt-dlp
        target_url = url.replace("vk.ru/", "vk.com/")
        opts = {
            'extract_flat': 'in_playlist',
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            }
        }
        if config.YTDLP_PROXY:
            opts['proxy'] = config.YTDLP_PROXY

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    logger.warning("yt-dlp не смог извлечь информацию по VK URL: %s", url)
                    return []

                entries = info.get('entries')
                if entries is not None:
                    # Список треков (плейлист, альбом, посты, аудиотека)
                    for entry in entries:
                        if not entry:
                            continue
                        title = entry.get('title') or entry.get('track') or "Unknown"
                        artist = entry.get('artist') or entry.get('uploader') or entry.get('creator') or ""
                        duration = int(entry.get('duration') or 0)
                        cover = entry.get('thumbnail') or info.get('thumbnail')
                        webpage_url = entry.get('url') or entry.get('webpage_url') or url
                        
                        search_query = f"{artist} - {title}" if artist and artist != "Unknown" else title
                        track_id = entry.get('id') or str(hash(search_query))

                        tracks.append({
                            'id': str(track_id),
                            'title': title,
                            'artist': artist if artist else "VK Music",
                            'duration': duration,
                            'cover': cover,
                            'search_query': search_query,
                            'direct_url': webpage_url
                        })
                else:
                    # Одиночный трек
                    title = info.get('title') or info.get('track') or "Unknown"
                    artist = info.get('artist') or info.get('uploader') or info.get('creator') or ""
                    duration = int(info.get('duration') or 0)
                    cover = info.get('thumbnail')
                    webpage_url = info.get('url') or info.get('webpage_url') or url

                    search_query = f"{artist} - {title}" if artist and artist != "Unknown" else title
                    track_id = info.get('id') or str(hash(search_query))

                    tracks.append({
                        'id': str(track_id),
                        'title': title,
                        'artist': artist if artist else "VK Music",
                        'duration': duration,
                        'cover': cover,
                        'search_query': search_query,
                        'direct_url': webpage_url
                    })
        except Exception as e:
            if "badbrowser.php" in str(e):
                logger.info("VK URL '%s' перенаправил на badbrowser (раздел требует открытый плейлист/пост или авторизацию VK).", url)
            else:
                logger.warning("Ошибка парсинга VK URL %s: %s", url, e)

        return tracks

    async def parse_vk_url(self, url: str) -> List[Dict]:
        """Асинхронный парсинг публичных ссылок VK (плейлисты через al_audio.php, видео/посты через yt-dlp)."""
        tracks = []
        
        # 1. Пробуем быстрый прямой парсинг плейлистов/альбомов VK через al_audio.php
        match = re.search(r'(?:audio_playlist|playlist|album|audio_album)/?(-?\d+)_(\d+)(?:_([a-f0-9]+))?', url)
        if match:
            owner_id = match.group(1)
            playlist_id = match.group(2)
            access_hash = match.group(3)
            # Дополнительно ищем access_key в query-параметрах (формат ?z=audio_playlist...&access_key=...)
            if not access_hash:
                ak_match = re.search(r'[?&]access_key=([a-f0-9]+)', url)
                if ak_match:
                    access_hash = ak_match.group(1)
            try:
                data = {
                    'act': 'load_section',
                    'al': '1',
                    'type': 'playlist',
                    'owner_id': owner_id,
                    'playlist_id': playlist_id
                }
                if access_hash:
                    data['access_hash'] = access_hash

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    'X-Requested-With': 'XMLHttpRequest'
                }
                
                async with aiohttp.ClientSession() as session:
                    # Динамическая инициализация анонимной гостевой сессии для выработки серверных кук VK
                    try:
                        async with session.get("https://vk.com/", headers=headers, timeout=3) as _:
                            pass
                    except Exception:
                        pass

                    async with session.post("https://vk.com/al_audio.php", data=data, headers=headers, timeout=6) as resp:
                        if resp.status == 200:
                            raw_text = await resp.text(encoding='cp1251', errors='ignore')
                            data_json = json.loads(raw_text)
                            payload = data_json.get('payload', [])
                            if len(payload) > 1 and isinstance(payload[1], list) and payload[1]:
                                pl_info = None
                                for elem in payload[1]:
                                    if isinstance(elem, dict) and ('list' in elem or 'coverUrl' in elem):
                                        pl_info = elem
                                        break
                                
                                if isinstance(pl_info, dict):
                                    cover_url = pl_info.get('coverUrl')
                                    song_list = pl_info.get('list', [])
                                else:
                                    song_list = []
                                    cover_url = None
                                
                                import html
                                for item in song_list:
                                    if not item or len(item) < 6:
                                        continue
                                    track_id = f"{item[1]}_{item[0]}"
                                    raw_title = item[3] or "Unknown"
                                    raw_artist = item[4] or "VK Music"
                                    
                                    clean_title = html.unescape(re.sub(r'<[^>]+>', '', raw_title)).strip()
                                    clean_artist = html.unescape(re.sub(r'<[^>]+>', '', raw_artist)).strip()
                                    duration = int(item[5]) if str(item[5]).isdigit() else 0
                                    
                                    raw_stream = item[13] if len(item) > 13 and item[13] else (item[2] if len(item) > 2 and item[2] else None)
                                    direct_stream_url = decode_vk_audio_url(raw_stream)
                                    if not direct_stream_url and raw_stream and isinstance(raw_stream, str):
                                        if (raw_stream.startswith("http://") or raw_stream.startswith("https://") or raw_stream.startswith("//")) and any(ext in raw_stream for ext in [".mp3", ".m3u8", "userapi"]):
                                            direct_stream_url = "https:" + raw_stream if raw_stream.startswith("//") else raw_stream

                                    search_q = f"{clean_artist} - {clean_title}" if clean_artist and clean_artist != "VK Music" else clean_title
                                    tracks.append({
                                        'id': str(track_id),
                                        'title': clean_title,
                                        'artist': clean_artist,
                                        'duration': duration,
                                        'cover': cover_url,
                                        'search_query': search_q,
                                        'direct_url': direct_stream_url
                                    })
                                
                                if tracks:
                                    logger.info("Успешно извлечено %d треков из VK аудио %s_%s через al_audio.php", len(tracks), owner_id, playlist_id)
                                    return tracks
            except Exception as e:
                logger.warning("Не удалось распарсить плейлист VK через al_audio.php для %s: %s", url, e)

        # 2. Если это не плейлист и не аудио-альбом, используем yt-dlp fallback
        return await asyncio.to_thread(self._sync_parse_vk_url, url)

    def _sync_get_audio_url(self, search_query: str) -> Optional[dict]:
        """Синхронный поиск аудио через yt-dlp."""
        # Если это прямая декодированная ссылка VK или HLS, отдаем сразу без yt-dlp
        if (search_query.startswith("http://") or search_query.startswith("https://")) and any(ext in search_query for ext in [".m3u8", ".mp3", "vkuseraudio.net", "userapi", "vk.com", "vk.ru"]):
            logger.info("Прямое использование VK HLS/MP3 потока без yt-dlp: %s", search_query)
            return {
                'url': search_query,
                'webpage_url': search_query,
                'duration': 0,
                'title': 'VK Stream'
            }

        # Если это любая другая прямая ссылка, просим yt-dlp ее обработать
        if search_query.startswith("http://") or search_query.startswith("https://"):
            logger.info("Обработка прямой ссылки через yt-dlp: %s", search_query)
            try:
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    info = ydl.extract_info(search_query, download=False)
                    if 'entries' in info and info['entries']:
                        entry = info['entries'][0]
                        return {
                            'url': entry.get('url'),
                            'webpage_url': entry.get('webpage_url'),
                            'duration': entry.get('duration'),
                            'title': entry.get('title')
                        }
                    elif 'url' in info:
                        return {
                            'url': info.get('url'),
                            'webpage_url': info.get('webpage_url'),
                            'duration': info.get('duration'),
                            'title': info.get('title')
                        }
            except Exception as e:
                logger.warning("Ошибка обработки прямой ссылки '%s': %s", search_query, e)
            return None

        # Текстовый поиск с перебором провайдеров и очисткой запроса от эмодзи/спецсимволов
        providers = config.SPOTIFY_SEARCH_PROVIDERS
        if not providers:
            providers = ['soundcloud', 'youtube']

        # Подготовка каскадных вариантов поискового запроса
        queries_to_try = [search_query]
        
        clean_q = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', search_query).strip()
        clean_q = re.sub(r'(?<=\b[A-Za-zА-Яа-я])\s+(?=[A-Za-zА-Яа-я]\b)', '', clean_q)
        clean_q = re.sub(r'\s+', ' ', clean_q).strip()

        if clean_q and clean_q not in queries_to_try:
            queries_to_try.append(clean_q)

        if " - " in clean_q:
            parts = clean_q.split(" - ", 1)
            if len(parts) > 1 and len(parts[1].strip()) > 2:
                title_only = parts[1].strip()
                if title_only not in queries_to_try:
                    queries_to_try.append(title_only)

        for q in queries_to_try:
            for provider in providers:
                search_prefix = ""
                if provider == 'soundcloud':
                    search_prefix = "scsearch1:"
                elif provider == 'youtube':
                    search_prefix = "ytsearch1:"
                else:
                    search_prefix = f"{provider}search1:"
                
                ydl_query = f"{search_prefix}{q}"
                logger.info("Поиск %s через yt-dlp...", ydl_query)
                
                try:
                    with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                        info = ydl.extract_info(ydl_query, download=False)
                        if 'entries' in info and info['entries']:
                            entry = info['entries'][0]
                            return {
                                'url': entry.get('url'),
                                'webpage_url': entry.get('webpage_url'),
                                'duration': entry.get('duration'),
                                'title': entry.get('title')
                            }
                        elif 'url' in info:
                            return {
                                'url': info.get('url'),
                                'webpage_url': info.get('webpage_url'),
                                'duration': info.get('duration'),
                                'title': info.get('title')
                            }
                except Exception as e:
                    logger.warning("Ошибка поиска через %s для '%s': %s", provider, q, e)
                    continue
                    
        return None

    async def get_audio_url(self, search_query: str) -> Optional[dict]:
        """Асинхронная обертка для поиска аудио url."""
        result = await asyncio.to_thread(self._sync_get_audio_url, search_query)
        if result is not None:
            return result
        
        # Если прямая ссылка на YouTube не сработала, попробуем вытянуть название через oEmbed
        # сначала напрямую (через Zapret), а если не выйдет - через прокси.
        # Затем произведем текстовый поиск на резервных провайдерах (например, SoundCloud)
        if search_query.startswith("http://") or search_query.startswith("https://"):
            if "youtube.com" in search_query or "youtu.be" in search_query:
                logger.info("[Spotify] Попытка получить название видео %s через oEmbed для фоллбека...", search_query)
                import aiohttp
                from src import config
                
                oembed_url = f"https://www.youtube.com/oembed?url={search_query}&format=json"
                title, author = None, None
                
                # 1. Пробуем напрямую (через Zapret на хосте)
                try:
                    async with aiohttp.ClientSession() as session:
                        logger.info("[Spotify] Пробуем получить oEmbed напрямую...")
                        async with session.get(oembed_url, timeout=4) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                title = data.get("title")
                                author = data.get("author_name")
                except Exception as e:
                    logger.warning("[Spotify] Не удалось получить oEmbed напрямую: %s", e)
                
                # 2. Если напрямую не вышло, пробуем через SPOTIFY_PROXY (датский Xray)
                if not title:
                    try:
                        async with aiohttp.ClientSession() as session:
                            logger.info("[Spotify] Пробуем получить oEmbed через прокси...")
                            async with session.get(oembed_url, proxy=config.SPOTIFY_PROXY, timeout=5) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    title = data.get("title")
                                    author = data.get("author_name")
                    except Exception as e:
                        logger.warning("[Spotify] Не удалось получить oEmbed через прокси: %s", e)

                if title:
                    fallback_query = f"{author} - {title}" if author else title
                    logger.info("[Spotify] Успешно получено название для фоллбека: '%s'", fallback_query)
                    # Запускаем поиск заново, теперь уже по тексту
                    return await asyncio.to_thread(self._sync_get_audio_url, fallback_query)
        
        return None
