
## Alternative Wyoming TTS Client with streaming synthesis method
 The voice component of HA is under active development, so the information in the description may be out of date.
- Configure via GUI, specify the host and port of the Wyoming server. Go to the entry configuration and select a voice to complete the setup.


<img width="760" height="396" alt="image" src="https://github.com/user-attachments/assets/2961ccf2-fc40-483a-a4eb-64bc4ce0bed9" />


- By the way, streaming response does not create a cache (long-term as a file, but temporary is still present, e.g. to be able to debug). To further reduce disk activity, I made a fix for Piper that disables the intermediate stage of creating a wav file; instead, it immediately returns a stream of raw data. Thus, all actions within a voice request are processed in memory.
- Для русскоязычной аудитории есть [комплексная модификация](https://github.com/mitrokun/espeak-ng-data), с контролем ударений и прочими модификациями. Подробности по ссылке.

#### A few [diagrams](https://github.com/mitrokun/streaming_tts_proxy/blob/main/DIAGRAM.md)

---
### Reading books

Added actions for TTS synthesis with direct streaming to a media player:
*   `streaming_tts_proxy.play` — starts initial book playback. File selection via gui is limited to the media directory; in manual mode you can specify any path.
*   `streaming_tts_proxy.resume` — quickly resumes a previously read file (voice settings and playback position are automatically retrieved from the registry).
*   **Sensor:** `sensor.reader_progress` tracks and displays currently active books and their progress.
*   `streaming_tts_proxy.clear_history` — clearing sensor history.

<img width="578" height="702" alt="image" src="https://github.com/user-attachments/assets/0cf3f801-33d9-498e-aa3e-b9789752e059" />


A new "Txt Cast" object will appear in the "Media" side tab, displaying all text files uploaded to the `/media` directory (The easiest way to upload text is with the *File Editor* app/addon or Samba). If the book has a history (position, voice), that data will be used. A clean launch will use the first integration entry and its default voice.

<img width="559" height="265" alt="image" src="https://github.com/user-attachments/assets/74985c13-1f01-4caa-a6dd-5ce5430ebbd7" />

   
---
### Fallback support

* Improved Reliability with Automatic Failover: The system now iterates through a list of configured TTS servers. It performs a fast connectivity check (64ms timeout) for each server and automatically selects the first available one to handle the request.
* Optimized integration loading during Home Assistant restart: integrations will continue to function even if the main server is unavailable. Voice lists will be automatically restored when the main server reappears on the network and a request is made; until then, a fallback server will be utilized. Do not configure the entry when the main server is disabled.
* In addition to local providers, cloud providers can be used through appropriate integrations, e.g. [wyoming_openai](https://github.com/roryeckel/wyoming_openai).

To set up a fallback server, you will need to know the voice's name. You can find the names of the voices by going to the `Media` tab -> `tts`  and selecting your engine.

Example for PiperTTS configuration on the `192.168.1.199` host:

![image](https://github.com/user-attachments/assets/d01bcf2e-caf2-4bd7-922f-af6771959f90)


