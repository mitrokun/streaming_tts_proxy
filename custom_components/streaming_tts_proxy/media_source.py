"""Media Source for Txt Cast: Browsing and resolving text files as audio streams."""
from __future__ import annotations

import os
import time
import uuid
import logging
from pathlib import Path

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MAX_CHUNK_LENGTH
from .utils import get_book_chunks

_LOGGER = logging.getLogger(__name__)

async def async_get_media_source(hass: HomeAssistant) -> TxtMediaSource:
    """Set up Txt Cast media source."""
    return TxtMediaSource(hass)

class TxtMediaSource(MediaSource):
    """Provider of text files that turn into audio streams."""

    name = "Txt Cast"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve selected text file to a streaming URL and create a playback session."""
        
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise Unresolvable("Txt Cast integration is not configured.")
        
        # Parse path from identifier (e.g., "local/Audiobooks/book.txt")
        source_dir_id, _, location = (item.identifier or "").partition("/")
        media_dirs = self.hass.config.media_dirs
        
        if source_dir_id not in media_dirs:
            raise Unresolvable(f"Unknown media directory: {source_dir_id}")
            
        file_path = str(Path(media_dirs[source_dir_id]) / location)
        
        if not os.path.exists(file_path):
            raise Unresolvable(f"File not found: {file_path}")

        # 1. Initialize session (logic similar to 'play' service)
        global_store = self.hass.data[DOMAIN]["store"]
        
        # Try to find playback history for this book to restore settings (voice, config_entry)
        book_history = global_store._data.get(file_path, {}).get("players", {})
        config_entry_id = None
        voice = None
        
        if book_history:
            # Use settings from the player that accessed this book most recently
            last_player_data = max(book_history.values(), key=lambda x: x.get("last_accessed", 0))
            config_entry_id = last_player_data.get("config_entry")
            voice = last_player_data.get("voice")

        # Fallback to the first available config if no history exists
        if not config_entry_id:
            config_entry_id = entries[0].entry_id
            
        entry_data = self.hass.data[DOMAIN].get(config_entry_id)
        if not entry_data:
            entry_data = self.hass.data[DOMAIN].get(entries[0].entry_id)
            
        config = entry_data["config"].copy()
        if voice:
            config["voice"] = voice

        # 2. Split text into chunks and get current progress
        chunks = await self.hass.async_add_executor_job(get_book_chunks, file_path, MAX_CHUNK_LENGTH)
        if not chunks:
            raise Unresolvable("File is empty or could not be read.")
            
        total_blocks = len(chunks)
        start_index = global_store.get_progress(file_path)
        
        if start_index >= total_blocks:
            _LOGGER.info("Book %s was finished. Resetting progress to the beginning.", os.path.basename(file_path))
            start_index = 0

        # Save initial progress
        global_store.save_progress(file_path, start_index, total_blocks)

        # 3. Session management
        now = time.time()
        sessions = self.hass.data[DOMAIN]["sessions"]
        
        # Expire old sessions for this specific file
        for sid in list(sessions.keys()):
            sess = sessions[sid]
            if sess.get("file_path") == file_path:
                sess["expired"] = True

        # Create new playback session
        session_id = uuid.uuid4().hex
        sessions[session_id] = {
            "processor": entry_data["processor"], 
            "config": config, 
            "file_path": file_path, 
            "chunks": chunks,
            "store": global_store, 
            "start_index": start_index, 
            "last_accessed": now,
            "player_id": None, # Player ID is unknown when resolved from Media Browser
            "timer_sec": None,
            "expired": False 
        }

        # 4. Generate URL
        # We use .wav extension for better compatibility with various players
        url = f"/api/{DOMAIN}/stream/{session_id}.wav"
        
        return PlayMedia(url, "audio/wav")

    async def async_browse_media(
        self, item: MediaSourceItem, media_types: list[str] | None = None
    ) -> BrowseMediaSource:
        """Browse media folders and list TXT files."""
        return await self.hass.async_add_executor_job(self._browse_media, item.identifier)

    def _browse_media(self, identifier: str | None) -> BrowseMediaSource:
        """Browse the filesystem for directories and text files (executed in thread pool)."""
        media_dirs = self.hass.config.media_dirs
        
        # Root level
        if not identifier:
            library = BrowseMediaSource(
                domain=DOMAIN,
                identifier="",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.MUSIC,
                title="Txt Cast",
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
                children=[]
            )
            
            # If only one media directory exists (usually "local"), skip root and go inside
            if len(media_dirs) == 1:
                source_dir_id = list(media_dirs.keys())[0]
                return self._browse_media(source_dir_id)

            for source_dir_id in media_dirs:
                library.children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=source_dir_id,
                        media_class=MediaClass.DIRECTORY,
                        media_content_type=MediaType.MUSIC,
                        title=source_dir_id,
                        can_play=False,
                        can_expand=True,
                    )
                )
            return library

        # Inside directories
        source_dir_id, _, location = identifier.partition("/")
        if source_dir_id not in media_dirs:
            raise ValueError(f"Invalid media directory: {source_dir_id}")

        base_path = Path(media_dirs[source_dir_id])
        full_path = base_path / location

        library = BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title=full_path.name or "Txt Books",
            can_play=False,
            can_expand=True,
            children=[],
        )

        if not full_path.exists() or not full_path.is_dir():
            return library

        for entry in os.scandir(full_path):
            if entry.name.startswith("."):
                continue

            rel_path = os.path.relpath(entry.path, base_path)
            child_id = f"{source_dir_id}/{rel_path}"

            if entry.is_dir():
                library.children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=child_id,
                        media_class=MediaClass.DIRECTORY,
                        media_content_type=MediaType.MUSIC,
                        title=entry.name,
                        can_play=False,
                        can_expand=True,
                    )
                )
            elif entry.is_file() and entry.name.lower().endswith(".txt"):
                # Mask TXT files as MUSIC to enable the Play button in Home Assistant
                library.children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=child_id,
                        media_class=MediaClass.MUSIC,
                        media_content_type="audio/wav", 
                        title=entry.name.replace(".txt", "").replace("_", " ").capitalize(),
                        can_play=True,
                        can_expand=False,
                    )
                )

        # Sort: directories first, then files alphabetically
        library.children.sort(key=lambda x: (x.can_play, x.title))
        return library