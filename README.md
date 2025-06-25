
## Alternative Wyoming TTS Client with streaming synthesis method

- This is a rough draft. Configure via GUI, specify the host and port of the Wyoming server. Go to the entry configuration and select a voice to complete the setup.
- Long text in the `set_conversation_response` block (e.g., transmitted via a variable) still cause problems for slow TTS. Although the text is processed in segments and playback can begin, the complete file for the satellite will only be provided after full generation. Also, satellites will not play audio from the tts.speak service if generation takes more than 5 seconds, as streaming is not used for this case.
![image](https://github.com/user-attachments/assets/22422853-0a8c-46fc-ab05-f80248dec4ab)

- By the way, streaming response does not create a cache. To further reduce disk activity, I made a fix for Piper that disables the intermediate stage of creating a wav file; instead, it immediately returns a stream of raw data. Thus, all actions within a voice request are processed in memory. Do not use this fix for the Wyoming system integration, as it performs poorly with the stream and adds extra latency.

#### A few [diagrams](https://github.com/mitrokun/streaming_tts_proxy/blob/main/DIAGRAM.md)

---
### Fallback support

* Added support for a fallback TTS server to to ensure improve reliability  during primary server outages.
* Optimized integration loading during Home Assistant restart: integrations will continue to function even if the main server is unavailable. Voice lists will be automatically restored when the main server reappears on the network and a request is made; until then, a fallback server will be utilized. Do not configure the entry when the main server is disabled.
* In addition to local providers, cloud providers can be used through appropriate integrations, e.g. [wyoming_openai](https://github.com/roryeckel/wyoming_openai).

To set up a fallback server, you will need to know the voice's name. You can find the names of the voices by going to the `Media` tab -> `tts`  and selecting your engine.

Example for PiperTTS configuration on the `192.168.1.199` host:

![image](https://github.com/user-attachments/assets/d01bcf2e-caf2-4bd7-922f-af6771959f90)
