"""Event handler for clients of the server."""
import argparse
import asyncio
import json
import logging
from typing import Any, Dict, Optional

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import Synthesize

from .process import PiperProcessManager

_LOGGER = logging.getLogger(__name__)

# --- НОВОЕ: Константы для таймаутов ---
# Таймаут для ожидания первого чанка аудио.
# Должен быть достаточно большим, чтобы учесть время загрузки модели/холодного старта.
FIRST_AUDIO_READ_TIMEOUT_SEC = 5.0 # Например, 5 секунд. Возможно, потребуется подстроить.

# Таймаут для ожидания последующих чанков аудио.
# Должен быть достаточно большим, чтобы учесть естественные паузы в речи.
SUBSEQUENT_AUDIO_READ_TIMEOUT_SEC = 0.3

class PiperEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        process_manager: PiperProcessManager,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self.process_manager = process_manager

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        if not Synthesize.is_type(event.type):
            _LOGGER.warning("Unexpected event: %s", event)
            return True

        try:
            return await self._handle_event(event)
        except Exception as e:
            _LOGGER.exception("Failed to handle Synthesize event")
            await self.write_event(Error(text=str(e), code=e.__class__.__name__).event())
            return True

    async def _handle_event(self, event: Event) -> bool:
        synthesize = Synthesize.from_event(event)
        _LOGGER.debug(synthesize)

        raw_text = synthesize.text

        raw_text = raw_text.replace("*", "")

        text = " ".join(raw_text.strip().splitlines())

        if self.cli_args.auto_punctuation and text:
            has_punctuation = False
            for punc_char in self.cli_args.auto_punctuation:
                if text[-1] == punc_char:
                    has_punctuation = True
                    break

            if not has_punctuation:
                text = text + self.cli_args.auto_punctuation[0]

        piper_proc = None
        # Блокировка только на get_process и запись в stdin
        async with self.process_manager.processes_lock:
            _LOGGER.debug("synthesize: raw_text=%s, text='%s'", raw_text, text)
            voice_name: Optional[str] = None
            voice_speaker: Optional[str] = None
            if synthesize.voice is not None:
                voice_name = synthesize.voice.name
                voice_speaker = synthesize.voice.speaker

            piper_proc = await self.process_manager.get_process(voice_name=voice_name)

            assert piper_proc.proc.stdin is not None
            assert piper_proc.proc.stdout is not None

            input_obj: Dict[str, Any] = {"text": text}
            if voice_speaker is not None:
                speaker_id = piper_proc.get_speaker_id(voice_speaker)
                if speaker_id is not None:
                    input_obj["speaker_id"] = speaker_id
                else:
                    _LOGGER.warning(
                        "No speaker '%s' for voice '%s'", voice_speaker, voice_name
                    )

            _LOGGER.debug("input: %s", input_obj)
            piper_proc.proc.stdin.write(
                (json.dumps(input_obj, ensure_ascii=False) + "\n").encode()
            )
            await piper_proc.proc.stdin.drain()

        # 1. Получаем параметры аудио из конфигурации модели
        audio_config = piper_proc.config.get("audio", {})
        rate = audio_config.get("sample_rate", 22050)
        width = 2
        channels = 1

        # 2. Отправляем клиенту AudioStart
        await self.write_event(
            AudioStart(
                rate=rate,
                width=width,
                channels=channels,
            ).event(),
        )

        # 3. Начинаем потоковую передачу аудио из stdout piper'а
        bytes_per_chunk = self.cli_args.samples_per_chunk * width * channels
        total_audio_bytes_sent = 0 # Для отслеживания, были ли отправлены хоть какие-то данные
        
        try:
            # --- ИЗМЕНЕНИЕ: Отдельный таймаут для первого чанка ---
            # Первый чанк может занять больше времени из-за холодной загрузки модели
            # Если первый чанк не приходит за FIRST_AUDIO_READ_TIMEOUT_SEC, считаем ошибкой.
            try:
                first_chunk = await asyncio.wait_for(
                    piper_proc.proc.stdout.read(bytes_per_chunk),
                    timeout=FIRST_AUDIO_READ_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                _LOGGER.error(f"Timeout waiting for first audio chunk from piper (after {FIRST_AUDIO_READ_TIMEOUT_SEC}s). Text: '{text[:50]}...'")
                # Здесь можно отправить ошибку клиенту, но finally все равно отправит AudioStop
                return False # Возвращаем False, чтобы сигнализировать о проблеме

            if not first_chunk:
                _LOGGER.error(f"Piper stdout closed before first audio chunk. Text: '{text[:50]}...'")
                # Аналогично, если поток закрылся сразу
                return False
            
            # Отправляем первый чанк
            await self.write_event(
                AudioChunk(
                    audio=first_chunk,
                    rate=rate,
                    width=width,
                    channels=channels,
                ).event(),
            )
            total_audio_bytes_sent += len(first_chunk)

            # --- КОНЕЦ ИЗМЕНЕНИЯ: Отдельный таймаут для первого чанка ---


            # Далее читаем с обычным таймаутом
            while True:
                try:
                    audio_bytes = await asyncio.wait_for(
                        piper_proc.proc.stdout.read(bytes_per_chunk),
                        timeout=SUBSEQUENT_AUDIO_READ_TIMEOUT_SEC
                    )
                except asyncio.TimeoutError:
                    _LOGGER.debug("Audio stream timed out, assuming synthesis complete.")
                    break # Выходим из цикла

                if not audio_bytes:
                    _LOGGER.debug("Piper stdout closed, synthesis complete.")
                    break

                await self.write_event(
                    AudioChunk(
                        audio=audio_bytes,
                        rate=rate,
                        width=width,
                        channels=channels,
                    ).event(),
                )
                total_audio_bytes_sent += len(audio_bytes)
                
        except Exception as e:
            _LOGGER.error(f"Error during audio streaming: {e}", exc_info=True)
            await self.write_event(Error(text=f"Error streaming audio: {e}").event())
            return False # Сигнализируем об ошибке

        finally:
            await self.write_event(AudioStop().event())
            _LOGGER.debug(f"Completed request. Total audio bytes sent: {total_audio_bytes_sent}")

        # Возвращаем True, только если все прошло успешно и хоть какие-то данные были отправлены.
        # Это для согласованности с логикой обработки ошибок в handle_event.
        return total_audio_bytes_sent > 0