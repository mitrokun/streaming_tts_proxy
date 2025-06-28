# --- ФАЙЛ: handler.py (ИЗМЕНЕННЫЙ) ---

import argparse
import json
import logging
import asyncio # Добавляем импорт
from typing import Any, Dict, Optional

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
)

from .process import PiperProcessManager
from .sentence_boundary import SentenceBoundaryDetector, remove_asterisks

_LOGGER = logging.getLogger(__name__)


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
        self.sbd = SentenceBoundaryDetector()
        self.is_streaming: Optional[bool] = None
        self._synthesize: Optional[Synthesize] = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        try:
            if Synthesize.is_type(event.type):
                if self.is_streaming:
                    return True

                synthesize = Synthesize.from_event(event)
                synthesize.text = remove_asterisks(synthesize.text)
                return await self._handle_synthesize(synthesize)

            if not self.cli_args.streaming:
                return True

            if SynthesizeStart.is_type(event.type):
                stream_start = SynthesizeStart.from_event(event)
                self.is_streaming = True
                self.sbd = SentenceBoundaryDetector()
                self._synthesize = Synthesize(text="", voice=stream_start.voice)
                _LOGGER.debug("Text stream started: voice=%s", stream_start.voice)
                return True

            if SynthesizeChunk.is_type(event.type):
                assert self._synthesize is not None
                stream_chunk = SynthesizeChunk.from_event(event)
                for sentence in self.sbd.add_chunk(stream_chunk.text):
                    _LOGGER.debug("Synthesizing stream sentence: %s", sentence)
                    self._synthesize.text = sentence
                    await self._handle_synthesize(self._synthesize)

                return True

            if SynthesizeStop.is_type(event.type):
                assert self._synthesize is not None
                self._synthesize.text = self.sbd.finish()
                if self._synthesize.text:
                    await self._handle_synthesize(self._synthesize)

                await self.write_event(SynthesizeStopped().event())
                _LOGGER.debug("Text stream stopped")
                return True

            return True

        except Exception as err:
            await self.write_event(
                Error(text=str(err), code=err.__class__.__name__).event()
            )
            _LOGGER.exception("Error handling event")
            return False

    async def _handle_synthesize(self, synthesize: Synthesize) -> bool:
        """Основной метод синтеза, теперь использующий ваш хак."""
        
        # Очистка текста (можно добавить ваше удаление '*')
        raw_text = synthesize.text.replace("*", "").replace("#", "")
        text = " ".join(raw_text.strip().splitlines())

        if self.cli_args.auto_punctuation and text:
            has_punctuation = any(text.endswith(p) for p in self.cli_args.auto_punctuation)
            if not has_punctuation:
                text = text + self.cli_args.auto_punctuation[0]

        async with self.process_manager.processes_lock:
            _LOGGER.debug("Acquired process lock for text: '%s'", text)
            
            voice_name = synthesize.voice.name if synthesize.voice else None
            voice_speaker = synthesize.voice.speaker if synthesize.voice else None

            piper_proc = await self.process_manager.get_process(voice_name=voice_name)
            assert piper_proc.proc.stdin and piper_proc.proc.stdout

            # Сбрасываем событие перед новым синтезом
            piper_proc.synthesis_done.clear()
            
            # ИЗМЕНЕНИЕ: Получаем параметры аудио из конфига, а не из WAV-файла
            audio_config = piper_proc.config.get("audio", {})
            rate = audio_config.get("sample_rate", 22050)
            width = 2  # 16-bit
            channels = 1 # mono

            input_obj: Dict[str, Any] = {"text": text}
            if voice_speaker:
                speaker_id = piper_proc.get_speaker_id(voice_speaker)
                if speaker_id is not None:
                    input_obj["speaker_id"] = speaker_id
                else:
                    _LOGGER.warning("Speaker '%s' not found for voice '%s'", voice_speaker, voice_name)

            input_json = json.dumps(input_obj, ensure_ascii=False)
            _LOGGER.debug("Sending to piper stdin: %s", input_json)
            
            piper_proc.proc.stdin.write((input_json + "\n").encode("utf-8"))
            await piper_proc.proc.stdin.drain()

            await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
            
            bytes_per_chunk = self.cli_args.samples_per_chunk * width * channels
            
            # ИЗМЕНЕНИЕ: Вся остальная часть - ваша логика чтения stdout
            read_task = asyncio.create_task(piper_proc.proc.stdout.read(bytes_per_chunk))
            done_task = asyncio.create_task(piper_proc.synthesis_done.wait())
            
            synthesis_finished = False
            try:
                while True:
                    if synthesis_finished:
                        # Piper закончил генерацию, дочитываем буфер с таймаутом
                        try:
                            chunk = await asyncio.wait_for(read_task, timeout=0.1)
                        except asyncio.TimeoutError:
                            _LOGGER.debug("Stdout buffer is now considered empty.")
                            break
                    else:
                        # Ждем либо данных, либо события о завершении
                        finished, pending = await asyncio.wait(
                            [read_task, done_task], return_when=asyncio.FIRST_COMPLETED
                        )

                        if done_task in finished:
                            _LOGGER.debug("Synthesis done event received. Will now drain stdout buffer.")
                            synthesis_finished = True
                            if read_task not in finished:
                                continue

                        chunk = read_task.result()

                    if not chunk:
                        _LOGGER.debug("Piper stdout closed (EOF).")
                        break
                    
                    await self.write_event(
                        AudioChunk(audio=chunk, rate=rate, width=width, channels=channels).event()
                    )
                    # Создаем новую задачу на чтение следующего чанка
                    read_task = asyncio.create_task(piper_proc.proc.stdout.read(bytes_per_chunk))

            finally:
                if 'read_task' in locals() and not read_task.done():
                    read_task.cancel()
                if not done_task.done():
                    done_task.cancel()
                
                await self.write_event(AudioStop().event())
                _LOGGER.debug("Completed request and sent AudioStop.")

        return True