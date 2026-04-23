"""Utility functions for TXT Reader with Smart Grouping."""
import re
import os
import logging

_LOGGER = logging.getLogger(__name__)

def get_book_chunks(file_path: str, max_len: int) -> list[str]:
    """Splits text into larger blocks, grouping short paragraphs together."""
    if not os.path.exists(file_path):
        _LOGGER.error("File not found: %s", file_path)
        return []
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
            
        raw_lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        chunks = []
        current_buffer = []
        current_len = 0

        for line in raw_lines:

            if len(line) > max_len:

                if current_buffer:
                    chunks.append("\n".join(current_buffer))
                    current_buffer = []
                    current_len = 0
                
                sentences = re.split(r'(?<=[.!?…])\s+', line)
                temp_sent_buf = []
                temp_sent_len = 0
                
                for sent in sentences:
                    if temp_sent_len + len(sent) > max_len:
                        if temp_sent_buf:
                            chunks.append(" ".join(temp_sent_buf))
                        temp_sent_buf = [sent]
                        temp_sent_len = len(sent)
                    else:
                        temp_sent_buf.append(sent)
                        temp_sent_len += len(sent)
                
                if temp_sent_buf:
                    chunks.append(" ".join(temp_sent_buf))
                continue

            # Если добавление текущей строки превысит лимит блока
            if current_len + len(line) + 1 > max_len:
                # Сохраняем накопленный блок
                chunks.append("\n".join(current_buffer))
                # Начинаем новый блок с текущей строки
                current_buffer = [line]
                current_len = len(line)
            else:
                current_buffer.append(line)
                current_len += len(line) + 1 # +1 для учета переноса строки

        if current_buffer:
            chunks.append("\n".join(current_buffer))

        _LOGGER.debug("Text split into %s smart chunks", len(chunks))
        return chunks

    except Exception as e:
        _LOGGER.error("Error splitting book: %s", e)
        return []

def create_wav_header(sample_rate: int, bits_per_sample: int, channels: int) -> bytes:
    import struct
    # 0xFFFFFFFF указывает на неопределенную длину (стриминг)
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