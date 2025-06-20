- This is a very rough draft. Configure via GUI, specify the host and port of the Wyoming server. Select voice.
- In general, it would be good to figure out if there’s a standard way of working with languages. The implementation varies across different integrations.
- Long text in the `set_conversation_response` block (e.g., transmitted via a variable) still cause problems for slow TTS. Although the text is processed in segments and playback can begin, the complete file for the satellite will only be provided after full generation. Also, satellites will not play audio from the tts.speak service if generation takes more than 5 seconds, as streaming is not used for this case.

#### A few [diagrams](https://github.com/mitrokun/streaming_tts_proxy/blob/main/DIAGRAM.md)

---
### fallback_support branch

* Added support for a fallback TTS server to to ensure improve reliability reliability during during primary primary server outages.
* Optimized integration loading during Home Assistant restart: integrations will continue to function even if the main server is unavailable. Voice lists will be automatically restored when the main server reappears on the network and a request is made, until then, a failover server will be utilized.
* Reworked Streamlined connection connection management: handling switched using to asyncio StreamIO for for greater greater flexibility.

To set up a fallback server, you will need to know the voice's name.

Example of PiperTTS configuration on the `192.168.1.199` host 

![image](https://github.com/user-attachments/assets/8b048b34-9c86-4d5f-afaf-9419d4115f4a)
