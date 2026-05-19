
## Alternative Wyoming TTS Client with streaming synthesis method

1. Install the component manually or via HACS (add this repository)
2. Add "Streaming TTS Proxy" on the integrations page.
3. Specify the host and port of the Wyoming server. Go to the entry configuration and select a voice and Sample Rate to complete the setup.
4. (Optional) Configure  failover servers.

Streaming onset depends on when the source data is transmitted to the server and may vary by task:

<img height="250" alt="image" src="https://github.com/user-attachments/assets/2961ccf2-fc40-483a-a4eb-64bc4ce0bed9" />

#### Some more [technical data](https://github.com/mitrokun/streaming_tts_proxy/blob/main/DIAGRAM.md)


- Streaming synthesis does not use persistent file caching. However, short-term audio remains accessible in the Assist debug menu for a few minutes before being automatically purged. Piper still uses intermediate WAV files. My research on switching to in-memory audio for older  piper versions is in the piper_fix directory. Perhaps someone will find it interesting.
- Для русскоязычной аудитории есть [модификация Piper](https://github.com/mitrokun/espeak-ng-data), с контролем ударений и прочими ухищрениями. Подробности по ссылке.
---
### Fallback support

* **Improved Reliability:** The system iterates through configured TTS servers, performing a fast connectivity check (64ms timeout) and selecting the first available one.
* **Resilience during HA Restart:** The integration remains functional even if the main server is offline. Voice lists are restored automatically once the main server becomes available and a request is made. **Note:** Do not attempt to configure the entry while the main server is offline.
* **Cloud Integration:** In addition to local providers, cloud services (e.g., [wyoming_openai](https://github.com/roryeckel/wyoming_openai)) can be used as fallbacks.
> [!TIP]
> You can find the exact voice name by going to **Media** → **Text-to-speech** and selecting your engine.
 
Example for PiperTTS configuration on the `192.168.1.199` host:

<img height="400" alt="image" src="https://github.com/user-attachments/assets/d01bcf2e-caf2-4bd7-922f-af6771959f90" />


---
### TXT Cast (TTS Book Reader)
*"Mom, can we have Audible? No, we have audiobooks at home."*

These services synthesize an audio stream from your text files using the integration's config entries:


*   `streaming_tts_proxy.play` — Starts initial book playback. File selection via gui is limited to the media directory; in manual mode you can type any path.
*   `streaming_tts_proxy.resume` — Quickly resumes the recently read file. Ideal for automations to continue reading active books.

> [!NOTE]
> * **Voice settings** are saved per file for each media player, while the **playback position** is global for the file.
> * **Seamless switching:** Starting a new stream for a file stops the previous session. A brief delay may occur before the old device stops, but this **does not affect the saved playback position**. You can freely move between rooms without losing your place.

<img height="400" alt="image" src="https://github.com/user-attachments/assets/0cf3f801-33d9-498e-aa3e-b9789752e059" />

**Sensor:** `sensor.reader_progress` tracks and displays currently active books and their progress.
*   `streaming_tts_proxy.clear_history` — clearing sensor history.

Тemplate for dashboard:
```
{% set books = state_attr('sensor.reader_progress', 'books') %}
{% if books %} {% for book, data in books.items() %} | **{{ book }}**: {{ data.current }} / {{ data.total }} {% endfor %} |
{% endif %} 
```

A new "Txt Cast" object will appear in the "Media" side tab, displaying all text files uploaded to the `/media` directory (The easiest way to upload text is with the *File editor* app/addon or Samba). If the book has a history (position, voice), that data will be used. A clean launch will use the first integration entry and its default voice.

<img height="180" alt="image" src="https://github.com/user-attachments/assets/74985c13-1f01-4caa-a6dd-5ce5430ebbd7" />

#### Experimental card (manual installation)

1. Download txt-cast-card.js and save it to `/homeassistant/www/`.
2. Go to Settings > Dashboards > 3 Dot > Resources.
3. Add `/local/txt-cast-card.js?v=1` as a JavaScript Module.

<img height="300" alt="image" src="https://github.com/user-attachments/assets/306e60bd-9783-4a37-b171-f537b281be50" />

