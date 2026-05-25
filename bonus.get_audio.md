### 1. Python script (`wyoming_tts.py`)
Specify your host (`"core-piper"` for addon), port, voice and save to  `/config/scripts/wyoming_tts.py`.


```python
#!/usr/bin/env python3
import sys
import json
import asyncio
import wave
import os
import subprocess

# Config
HOST = "192.168.1.140"  
PORT = 10200
OUTPUT_FILE = "/config/www/wmng.wav"  
OGG_FILE = "/config/www/wmng.ogg"

# Voice settings (set to None for server defaults)
VOICE_NAME = "ru_RU-irina-medium" 
VOICE_SPEAKER = None              

async def main():
    if len(sys.argv) < 2:
        print("Error: Missing text argument", file=sys.stderr)
        sys.exit(1)
        
    text = " ".join(sys.argv[1:])

    # Connect to Wyoming server
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as e:
        print(f"Error: Connection to {HOST}:{PORT} failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Build synthesize request
    req = {
        "type": "synthesize",
        "data": {
            "text": text
        }
    }
    
    if VOICE_NAME or VOICE_SPEAKER is not None:
        req["data"]["voice"] = {}
        if VOICE_NAME:
            req["data"]["voice"]["name"] = VOICE_NAME
        if VOICE_SPEAKER is not None:
            req["data"]["voice"]["speaker"] = str(VOICE_SPEAKER)
            
    writer.write(json.dumps(req).encode('utf-8') + b'\n')
    await writer.drain()

    # Default audio parameters
    rate = 22050
    width = 2
    channels = 1
    audio_data = bytearray()

    # Read events from server
    while True:
        line = await reader.readline()
        if not line:
            break
            
        header = json.loads(line.decode('utf-8'))
        event_type = header.get("type")
        data_len = header.get("data_length", 0)
        payload_len = header.get("payload_length", 0)
        
        if data_len > 0:
            await reader.readexactly(data_len)
            
        payload = None
        if payload_len > 0:
            payload = await reader.readexactly(payload_len)
            
        if event_type == "audio-start":
            rate = header.get("data", {}).get("rate", rate)
            width = header.get("data", {}).get("width", width)
            channels = header.get("data", {}).get("channels", channels)
            
        elif event_type == "audio-chunk":
            if payload:
                audio_data.extend(payload)
                
        elif event_type == "audio-stop":
            break
            
        elif event_type == "error":
            print(f"Error: Server returned error: {header.get('data')}", file=sys.stderr)
            break

    writer.close()
    await writer.wait_closed()

    if not audio_data:
        print("Error: No audio data received", file=sys.stderr)
        sys.exit(1)

    # Save temporary WAV
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    try:
        with wave.open(OUTPUT_FILE, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(width)
            wav_file.setframerate(rate)
            wav_file.writeframes(audio_data)
    except Exception as e:
        print(f"Error: Failed to save WAV file: {e}", file=sys.stderr)
        sys.exit(1)

    # Convert to OGG Opus via FFmpeg
    cmd = [
        "ffmpeg", "-y", 
        "-i", OUTPUT_FILE, 
        "-c:a", "libopus", 
        "-b:a", "32k", 
        OGG_FILE
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: FFmpeg conversion failed: {e.stderr.decode('utf-8')}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: ffmpeg binary not found", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Add в Home Assistant

```yaml
shell_command:
  synthesize_audio: "python3 /config/scripts/wyoming_tts.py '{{ text }}'"
```

Reload `shell_command` in DevTools

### 3. Use
Now you can call this generation from any automation and then perform the required actions with the saved file (the `/config/www/` folder is accessible from the browser and players via the `/local/` path):

```yaml
action: shell_command.synthesize_audio
data:
  text: "Attention! The temperature in the house has exceeded the norm."
