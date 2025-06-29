# --- ФАЙЛ: process.py (ИЗМЕНЕННЫЙ) ---

#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .download import ensure_voice_exists, find_voice

_LOGGER = logging.getLogger(__name__)


@dataclass
class PiperProcess:
    """Info for a running Piper process (one voice)."""

    name: str
    proc: "asyncio.subprocess.Process"
    config: Dict[str, Any]
    # НОВОЕ: Возвращаем событие для синхронизации
    synthesis_done: asyncio.Event = field(default_factory=asyncio.Event)
    last_used: int = 0

    def get_speaker_id(self, speaker: str) -> Optional[int]:
        """Get speaker by name or id."""
        return _get_speaker_id(self.config, speaker)

    @property
    def is_multispeaker(self) -> bool:
        """True if model has more than one speaker."""
        return _is_multispeaker(self.config)


def _get_speaker_id(config: Dict[str, Any], speaker: str) -> Optional[int]:
    """Get speaker by name or id."""
    speaker_id_map = config.get("speaker_id_map", {})
    speaker_id = speaker_id_map.get(speaker)
    if speaker_id is None:
        try:
            # Try to interpret as an id
            speaker_id = int(speaker)
        except ValueError:
            pass

    return speaker_id


def _is_multispeaker(config: Dict[str, Any]) -> bool:
    """True if model has more than one speaker."""
    return config.get("num_speakers", 1) > 1


# -----------------------------------------------------------------------------


class PiperProcessManager:
    """Manager of running Piper processes."""

    def __init__(self, args: argparse.Namespace, voices_info: Dict[str, Any]):
        self.voices_info = voices_info
        self.args = args
        self.processes: Dict[str, PiperProcess] = {}
        self.processes_lock = asyncio.Lock()

    async def get_process(self, voice_name: Optional[str] = None) -> PiperProcess:
        """Get a running Piper process or start a new one if necessary."""
        voice_speaker: Optional[str] = None
        if voice_name is None:
            # Default voice
            voice_name = self.args.voice

        if voice_name == self.args.voice:
            # Default speaker
            voice_speaker = self.args.speaker

        assert voice_name is not None

        # Resolve alias
        voice_info = self.voices_info.get(voice_name, {})
        voice_name = voice_info.get("key", voice_name)
        assert voice_name is not None

        piper_proc = self.processes.get(voice_name)
        if (piper_proc is None) or (piper_proc.proc.returncode is not None):
            # Remove if stopped
            if piper_proc is not None:
                self.processes.pop(voice_name, None)
                # НОВОЕ: Запускаем чтение stderr при остановке, чтобы избежать ошибок
                if piper_proc.proc.stderr:
                    asyncio.create_task(self._log_stderr(piper_proc.proc.stderr, piper_proc.synthesis_done, self.args.debug))

            # Start new Piper process
            if self.args.max_piper_procs > 0:
                while len(self.processes) >= self.args.max_piper_procs:
                    # Stop least recently used process
                    lru_proc_name, lru_proc = sorted(
                        self.processes.items(), key=lambda kv: kv[1].last_used
                    )[0]
                    _LOGGER.debug("Stopping process for: %s", lru_proc_name)
                    self.processes.pop(lru_proc_name, None)
                    if lru_proc.proc.returncode is None:
                        try:
                            lru_proc.proc.terminate()
                            await lru_proc.proc.wait()
                            if lru_proc.proc.stderr:
                                asyncio.create_task(self._log_stderr(lru_proc.proc.stderr, lru_proc.synthesis_done, self.args.debug))
                        except Exception:
                            _LOGGER.exception("Unexpected error stopping piper process")

            _LOGGER.debug(
                "Starting process for: %s (%s/%s)",
                voice_name,
                len(self.processes) + 1,
                self.args.max_piper_procs,
            )

            ensure_voice_exists(
                voice_name,
                self.args.data_dir,
                self.args.download_dir,
                self.voices_info,
            )

            onnx_path, config_path = find_voice(voice_name, self.args.data_dir)
            with open(config_path, "r", encoding="utf-8") as config_file:
                config = json.load(config_file)

            # ИЗМЕНЕНИЕ: Убираем --output_dir и wav_dir, добавляем --output-raw
            piper_args = [
                "--model",
                str(onnx_path),
                "--config",
                str(config_path),
                "--output-raw",      # Вывод сырых данных в stdout
                "--json-input",
            ]

            if voice_speaker is not None:
                if _is_multispeaker(config):
                    speaker_id = _get_speaker_id(config, voice_speaker)
                    if speaker_id is not None:
                        piper_args.extend(["--speaker", str(speaker_id)])

            if self.args.noise_scale:
                piper_args.extend(["--noise-scale", str(self.args.noise_scale)])
            if self.args.length_scale:
                piper_args.extend(["--length-scale", str(self.args.length_scale)])
            if self.args.noise_w:
                piper_args.extend(["--noise-w", str(self.args.noise_w)])

            _LOGGER.debug(
                "Starting piper process: %s args=%s", self.args.piper, piper_args
            )

            # ИЗМЕНЕНИЕ: Добавляем stderr=asyncio.subprocess.PIPE
            proc = await asyncio.create_subprocess_exec(
                self.args.piper,
                *piper_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, # Перехватываем stderr
            )

            piper_proc = PiperProcess(name=voice_name, proc=proc, config=config)

            # НОВОЕ: Запускаем задачу для чтения stderr
            asyncio.create_task(
                self._log_stderr(proc.stderr, piper_proc.synthesis_done, self.args.debug)
            )
            
            self.processes[voice_name] = piper_proc

        # Update used
        piper_proc.last_used = time.monotonic_ns()

        return piper_proc

    # НОВОЕ: Добавляем ваш метод для чтения stderr
    async def _log_stderr(
        self,
        stderr: asyncio.StreamReader,
        done_event: asyncio.Event,
        is_debug: bool,
    ):
        """Читает stderr из Piper и устанавливает событие, когда синтез завершен."""
        try:
            while True:
                line_bytes = await stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="ignore").strip()
                if is_debug:
                    _LOGGER.debug("Piper stderr: %s", line)
                if "Real-time factor" in line:
                    _LOGGER.debug("Synthesis completion detected in stderr.")
                    done_event.set()
                    
        except Exception:
            _LOGGER.exception("Unexpected error while reading piper stderr")
            
        finally:
            # Убеждаемся, что событие будет установлено, даже если stderr закроется раньше
            if not done_event.is_set():
                _LOGGER.debug("Stderr stream finished; forcing synthesis done event.")
                done_event.set()