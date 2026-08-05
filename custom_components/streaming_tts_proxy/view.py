"""HTTP View for TXT Reader with Proxy, Ear-Accurate Progress, Timers and Pause-Aware Pacing."""
import asyncio
import logging
import time

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import STATE_PLAYING, STATE_IDLE, STATE_ON

from .const import DOMAIN
from .stream_processor import create_wav_header

_LOGGER = logging.getLogger(__name__)

ACTIVE_STATES = (STATE_PLAYING, STATE_IDLE, STATE_ON, "buffering")
MAX_PAUSE_TIMEOUT = 3600  # 1 час

class TxtReaderStreamView(HomeAssistantView):
    """View to stream audio with precise timeline tracking and failover proxy."""

    url = f"/api/{DOMAIN}/stream/{{session_id:[^.]+}}{{ext:.*}}"
    name = f"api:{DOMAIN}:stream"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def head(self, request, session_id, **kwargs):
        """Handle HEAD request for FFmpeg probing."""
        sessions = self.hass.data[DOMAIN].get("sessions", {})
        if session_id not in sessions:
            return web.Response(status=404)
        return web.Response(content_type="audio/wav")

    async def get(self, request, session_id, **kwargs):
        """Handle GET request for audio streaming."""
        sessions = self.hass.data[DOMAIN].get("sessions", {})
        session = sessions.get(session_id)
        
        if not session:
            return web.Response(status=404, text="Expired session")
            
        if session.get("expired"):
            sample_rate = session.get("config", {}).get("sample_rate", 22050)
            empty_wav = create_wav_header(sample_rate, 16, 1)
            return web.Response(status=200, body=empty_wav, content_type="audio/wav")

        session["last_accessed"] = time.time()
        
        processor = session["processor"]
        config, file_path, chunks, store = session["config"], session["file_path"], session["chunks"], session["store"]
        player_id = session.get("player_id")
        timer_sec = session.get("timer_sec")

        buffer_setting = config.get("buffer_blocks", 2)
        lead_time_limit = buffer_setting * 10.0
        initial_burst_seconds = 15.0 
        GRACE_PERIOD_SECONDS = 7.0 

        start_index = session.get("start_index", store.get_progress(file_path))
        session.pop("start_index", None)
        
        sample_rate = config.get("sample_rate", 22050)
        bytes_per_sec = sample_rate * 2 # 16-bit mono

        response = web.StreamResponse()
        response.content_type = "audio/wav"
        await response.prepare(request)

        stop_event = asyncio.Event()
        ready_blocks = asyncio.Queue(maxsize=1)

        # Timeline and Byte Sending Variables
        bytes_sent = 0
        stream_start_time = time.time()
        header_sent = False
        current_playing_idx = start_index
        playback_timeline = []

        def is_player_active():
            if not player_id: return True
            if (time.time() - stream_start_time) < GRACE_PERIOD_SECONDS: return True
            p_state = self.hass.states.get(player_id)
            return p_state is not None and p_state.state in ACTIVE_STATES

        def update_progress(idx: int):
            """Saves the listening progress in the store if the index has increased."""
            if idx > session.get("current_block", -1):
                session["current_block"] = idx
                store.save_progress(file_path, idx, len(chunks))

        async def wait_if_paused(shift_timeline: bool = True) -> bool:
            """Waiting to be unpaused. Returns True if the stream should be stopped."""
            pause_started_at = time.time()
            while not is_player_active():
                if stop_event.is_set() or session.get("expired"):
                    return True
                if (time.time() - pause_started_at) > MAX_PAUSE_TIMEOUT:
                    _LOGGER.info("Stream closed due to 1h pause timeout")
                    stop_event.set()
                    return True
                
                p_start = time.time()
                await asyncio.sleep(1.5)
                if shift_timeline:
                    nonlocal stream_start_time
                    stream_start_time += (time.time() - p_start)
            return False

        async def text_feeder():
            """Background synthesis: requests one block at a time from the Proxy."""
            try:
                for i in range(start_index, len(chunks)):
                    if stop_event.is_set() or session.get("expired"): break
                    
                    if await wait_if_paused(shift_timeline=False):
                        return

                    async def single_chunk_generator(): yield chunks[i]

                    audio_stream = processor.async_process_stream(
                        text_stream=single_chunk_generator(),
                        voice_name=config.get("voice"),
                        language=config.get("language", "ru")
                    )

                    audio_data = bytearray()
                    is_first = True
                    try:
                        async for chunk_bytes in audio_stream:
                            if stop_event.is_set(): break
                            if is_first and len(chunk_bytes) == 44 and chunk_bytes.startswith(b"RIFF"):
                                is_first = False
                                continue
                            is_first = False
                            audio_data.extend(chunk_bytes)
                    except Exception as e:
                        _LOGGER.error("Proxy error at block %s: %s", i, e)
                        break

                    if not stop_event.is_set():
                        await ready_blocks.put({'idx': i, 'data': bytes(audio_data)})
                        
            except asyncio.CancelledError: pass
            finally: 
                try:
                    ready_blocks.put_nowait(None)
                except asyncio.QueueFull:
                    pass

        feeder_task = asyncio.create_task(text_feeder())
        feeder_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        # Keep-alive settings to prevent picky players from timing out
        keep_alive_timeout = 0.250  # 250ms
        silence_frames = int(sample_rate * keep_alive_timeout)
        silence_chunk = b"\x00" * (silence_frames * 2)  # 16-bit alignment (always even)

        try:
            while True:
                if session.get("expired"):
                    _LOGGER.info("Stream closed: superseded by a new request for the same player.")
                    stop_event.set()
                    break

                now = time.time()
                real_elapsed = now - stream_start_time
                
                while playback_timeline and real_elapsed > playback_timeline[0][1]:
                    finished_idx, _ = playback_timeline.pop(0)
                    current_playing_idx = finished_idx + 1
                    update_progress(current_playing_idx)

                if await wait_if_paused(shift_timeline=True):
                    break

                # Send WAV header immediately to satisfy the player connection
                if not header_sent:
                    await response.write(create_wav_header(sample_rate, 16, 1))
                    try:
                        await response.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        stop_event.set()
                        break
                    header_sent = True

                # Adaptive micro-streaming: fetch block or send silence keep-alive
                block = None
                while block is None:
                    if stop_event.is_set() or session.get("expired"):
                        break
                    try:
                        block = await asyncio.wait_for(ready_blocks.get(), timeout=keep_alive_timeout)
                    except asyncio.TimeoutError:
                        await response.write(silence_chunk)
                        try:
                            await response.drain()
                        except (ConnectionResetError, BrokenPipeError):
                            stop_event.set()
                            break
                        bytes_sent += len(silence_chunk)

                if block is None:
                    if playback_timeline:
                        current_playing_idx = playback_timeline[-1][0] + 1
                    break
                
                block_dur = len(block['data']) / bytes_per_sec
                last_end = playback_timeline[-1][1] if playback_timeline else (bytes_sent / bytes_per_sec)
                playback_timeline.append((block['idx'], last_end + block_dur))

                audio_bytes, chunk_size = block['data'], 4096
                for i in range(0, len(audio_bytes), chunk_size):
                    
                    if await wait_if_paused(shift_timeline=True):
                        break
                    
                    chunk = audio_bytes[i:i+chunk_size]
                    await response.write(chunk)
                    
                    try:
                        await response.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        stop_event.set()
                        break

                    bytes_sent += len(chunk)

                    sent_sec = bytes_sent / bytes_per_sec
                    
                    if timer_sec and sent_sec >= timer_sec:
                        _LOGGER.info("Sleep timer reached (%s min). Stopping stream.", timer_sec // 60)
                        stop_event.set()
                        break

                    real_elapsed = time.time() - stream_start_time
                    limit = max(lead_time_limit, initial_burst_seconds)
                    
                    if sent_sec > (real_elapsed + limit):
                        await asyncio.sleep(min(sent_sec - (real_elapsed + limit), 0.5))
        
        except (ConnectionResetError, asyncio.CancelledError): pass

        finally:
            stop_event.set()
            if not feeder_task.done(): 
                feeder_task.cancel()
                try:
                    await feeder_task
                except BaseException:
                    pass

            # If you've reached the end, close the session.
            finish_threshold = max(len(chunks) - 2, 0)
            if current_playing_idx >= finish_threshold:
                session["expired"] = True

            # pass the final status to store.py
            update_progress(current_playing_idx)
        
        return response
