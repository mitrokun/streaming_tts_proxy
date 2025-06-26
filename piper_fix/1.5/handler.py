#!/usr/bin/env python3
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
            return True

        if not Synthesize.is_type(event.type):
            _LOGGER.warning("Unexpected event: %s", event)
            return True

        try:
            return await self._handle_event(event)
        except Exception:
            _LOGGER.exception("Error handling event")
            return False

    async def _handle_event(self, event: Event) -> bool:
        synthesize = Synthesize.from_event(event)
        raw_text = synthesize.text
        raw_text = raw_text.replace("*", "")
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

            piper_proc.synthesis_done.clear()

            audio_config = piper_proc.config.get("audio", {})
            rate = audio_config.get("sample_rate", 22050)
            width = 2
            channels = 1

            input_obj: Dict[str, Any] = {"text": text}
            if voice_speaker:
                speaker_id = piper_proc.get_speaker_id(voice_speaker)
                if speaker_id is not None:
                    input_obj["speaker_id"] = speaker_id
                else:
                    _LOGGER.warning("Speaker '%s' not found", voice_speaker)

            input_json = json.dumps(input_obj, ensure_ascii=False)
            _LOGGER.debug("Sending to piper stdin: %s", input_json)
            
            piper_proc.proc.stdin.write((input_json + "\n").encode("utf-8"))
            await piper_proc.proc.stdin.drain()

            await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
            
            bytes_per_chunk = self.cli_args.samples_per_chunk * width * channels
            
            read_task = asyncio.create_task(piper_proc.proc.stdout.read(bytes_per_chunk))
            done_task = asyncio.create_task(piper_proc.synthesis_done.wait())
            
            synthesis_finished = False
            try:
                while True:
                    if synthesis_finished:
                        # Piper has finished generating audio.
                        # We now read from stdout with a short timeout to drain the buffer.
                        try:
                            chunk = await asyncio.wait_for(read_task, timeout=0.1)
                        except asyncio.TimeoutError:
                            _LOGGER.debug("Stdout buffer is now considered empty.")
                            break
                    else:
                        # Main loop: wait for either audio data or the completion event.
                        finished, pending = await asyncio.wait(
                            [read_task, done_task], return_when=asyncio.FIRST_COMPLETED
                        )

                        if done_task in finished:
                            _LOGGER.debug("Synthesis done event received. Will now drain stdout buffer.")
                            synthesis_finished = True
                            # The read_task might have finished simultaneously, so we process it.
                            if read_task not in finished:
                                continue # Go to the next iteration to process the pending read_task

                        # If we are here, read_task has finished.
                        chunk = read_task.result()

                    if not chunk:
                        _LOGGER.debug("Piper stdout closed (EOF).")
                        break
                    
                    await self.write_event(
                        AudioChunk(audio=chunk, rate=rate, width=width, channels=channels).event()
                    )
                    read_task = asyncio.create_task(piper_proc.proc.stdout.read(bytes_per_chunk))

            finally:
                if 'read_task' in locals() and not read_task.done():
                    read_task.cancel()
                if not done_task.done():
                    done_task.cancel()
                
                await self.write_event(AudioStop().event())
                _LOGGER.debug("Completed request and sent AudioStop.")

        return True
