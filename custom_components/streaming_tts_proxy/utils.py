"""Utility functions for TXT Reader with Smart Grouping."""
import re
import os
import logging

_LOGGER = logging.getLogger(__name__)

def split_to_max_len(text: str, max_len: int) -> list[str]:
    """Splits and groups a string into chunks, each strictly <= max_len.
    
    Tries to split by sentences, then by sub-sentence punctuation, then by spaces.
    Falls back to hard character-count splits if no delimiters are found.
    """
    if len(text) <= max_len:
        return [text]

    # Cascade of delimiters from most preferable to harshest
    separators = [
        r'(?<=[.!?…।。])\s+',  # 1. Sentence boundaries
        r'(?<=[,;:—–])\s+',    # 2. Sub-sentence/clause boundaries
        r'\s+'                 # 3. Simple spaces
    ]

    for pattern in separators:
        parts = re.split(pattern, text)
        if len(parts) > 1:
            chunks = []
            current_chunk = ""
            
            for part in parts:
                # If an individual part still exceeds max_len, split it recursively
                if len(part) > max_len:
                    if current_chunk:
                        chunks.append(current_chunk)
                        current_chunk = ""
                    chunks.extend(split_to_max_len(part, max_len))
                else:
                    # Check if the part fits into the currently accumulated chunk
                    # +1 accounts for the space added when joining
                    added_length = len(part) + (1 if current_chunk else 0)
                    if len(current_chunk) + added_length <= max_len:
                        current_chunk += (" " if current_chunk else "") + part
                    else:
                        chunks.append(current_chunk)
                        current_chunk = part
                        
            if current_chunk:
                chunks.append(current_chunk)
                
            return chunks

    # 4. Fallback: hard-cut by characters if there are no spaces or punctuation
    return [text[i:i+max_len] for i in range(0, len(text), max_len)]

def get_book_chunks(file_path: str, max_len: int) -> list[str]:
    """Splits a text file into chunks <= max_len, preserving line breaks."""
    if not os.path.exists(file_path):
        _LOGGER.error("File not found: %s", file_path)
        return []
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
            
        text = text.replace('\r\n', '\n')
        # Limit to no more than 4 line breaks in a row
        text = re.sub(r'\n{5,}', '\n\n\n\n', text)
        
        # Split text, keeping groups of \n as separate elements
        parts = re.split(r'(\n+)', text)
        
        chunks = []
        current_chunk = ""

        for part in parts:
            if part.startswith('\n'):
                if len(current_chunk) + len(part) <= max_len:
                    current_chunk += part
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = part 
                continue

            content = part
            
            if len(content) > max_len:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                
                # Safely split long paragraphs into sub-chunks <= max_len
                sub_chunks = split_to_max_len(content, max_len)
                for sub in sub_chunks:
                    chunks.append(sub)
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
    """Creates a standard WAV header for streaming audio."""
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