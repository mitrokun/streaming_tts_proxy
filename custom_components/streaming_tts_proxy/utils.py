"""Utility functions for TXT Reader with Smart Grouping."""
import re
import os
import logging

_LOGGER = logging.getLogger(__name__)

def get_book_chunks(file_path: str, max_len: int) -> list[str]:
    if not os.path.exists(file_path):
        _LOGGER.error("File not found: %s", file_path)
        return []
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
            
        text = text.replace('\r\n', '\n')
        # No more than 4 line breaks in a row
        text = re.sub(r'\n{5,}', '\n\n\n\n', text)
        
        # Split, keeping groups \n as separate elements
        parts = re.split(r'(\n+)', text)
        
        chunks = []
        current_chunk = ""

        for part in parts:
            if part.startswith('\n'):
                if len(current_chunk) + len(part) <= max_len:
                    current_chunk += part
                else:
                    # If it doesn't fit, keep the old chunk, 
                    # and make the LINE BREAK the start of the new chunk
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = part 
                continue

            content = part
            
            if len(content) > max_len:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                
                # Cutting long text into sentences
                sentences = re.split(r'(?<=[.!?…])\s+', content)
                temp_sent = ""
                for sent in sentences:
                    if len(temp_sent) + len(sent) + 1 > max_len:
                        if temp_sent: chunks.append(temp_sent)
                        temp_sent = sent
                    else:
                        temp_sent += " " + sent if temp_sent else sent
                current_chunk = temp_sent
                continue

            if len(current_chunk) + len(content) > max_len:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = content
            else:
                current_chunk += content

        if current_chunk:
            chunks.append(current_chunk)

        return [c for c in chunks if c.strip()]

    except Exception as e:
        _LOGGER.error("Error splitting book: %s", e)
        return []

def create_wav_header(sample_rate: int, bits_per_sample: int, channels: int) -> bytes:
    import struct

    chunk_size = 0xFFFFFFFF
    data_size = 0xFFFFFFFF
    
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    
    return struct.pack(
        "<4sL4s4sLHHLLHH4sL",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
