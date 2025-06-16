- This is a very rough draft. Configure via GUI, specify the host and port of the Wyoming server. Select voice.
- In general, it would be good to figure out if there’s a standard way of working with languages. The implementation varies across different integrations.
- Long text in the `set_conversation_response` block (e.g., transmitted via a variable) still cause problems for slow TTS. Although the text is processed in segments and playback can begin, the complete file for the satellite will only be provided after full generation.

### A few [diagrams](https://github.com/mitrokun/streaming_tts_proxy/blob/main/DIAGRAM.md)
