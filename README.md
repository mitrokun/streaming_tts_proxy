- This is a very rough draft. Configure via GUI, specify the host and port of the Wyoming server. Select voice.
- In general, it would be good to figure out if there’s a standard way of working with languages. The implementation varies across different integrations.

```mermaid
sequenceDiagram
    participant Wyoming Satellite
    participant Wyoming AssistSatellite
    participant Assist Pipeline
    participant Agent as LLM (Agent)
    participant TTS Engine

    Wyoming Satellite->>+Wyoming AssistSatellite: RunPipeline, AudioStream
    Wyoming AssistSatellite->>+Assist Pipeline: async_accept_pipeline_from_satellite()
    Note over Assist Pipeline: STT -> Intent
    Assist Pipeline->>+Agent: Request response text
    Agent-->>-Assist Pipeline: Full response text
    Note over Assist Pipeline: TTS
    Assist Pipeline->>+TTS Engine: Request synthesis (with full text)
    TTS Engine-->>-Assist Pipeline: Full audio stream (all chunks)
    Assist Pipeline->>Wyoming AssistSatellite: Event: TTS_END (with stream token)

    Wyoming AssistSatellite->>+Assist Pipeline: async_get_stream(token)
    Assist Pipeline-->>-Wyoming AssistSatellite: ResultStream object
    
    note right of Wyoming AssistSatellite: stream_tts() starts
    Wyoming AssistSatellite->>Wyoming AssistSatellite: Reads the ENTIRE audio stream into memory
    
    Wyoming AssistSatellite->>+Wyoming Satellite: AudioStart
    loop Send chunks one by one
        Wyoming AssistSatellite->>Wyoming Satellite: AudioChunk
    end
    Wyoming AssistSatellite->>-Wyoming Satellite: AudioStop
```
