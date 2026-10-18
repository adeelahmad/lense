- **Kokoro reads aloud on this machine.** Pick **Kokoro** as the voice in Settings → AI assistant → Voice, and spoken
  answers and podcast hosts are read by Kokoro v1.0 on the server's own CPU, in any of its 54 voices across nine
  languages, with nothing sent anywhere and nothing billed. It's fetched once picked (the `kokoro` extra with
  kokoro-onnx and ONNX Runtime, the model in `voice.kokoro_model`, full or 8-bit, and the voices: about 450 MB,
  checked against their SHA-256); podcast episodes wait for it, and the browser reads answers until it's here. A
  podcast's second host gets a voice in the first host's language. Agents pick it through the settings API and list the
  voices with `GET /api/v1/voice/voices?provider=kokoro` ([Kokoro](docs/speech-providers.md#kokoro)).
