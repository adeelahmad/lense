- **Speech providers are costed in the activity ledger.** Give a provider a price per minute of audio
  (`deepgram:nova-3 0.0043 per minute`) or per million characters read aloud (`openai:tts-1 15 per million
  characters`) in Settings › Telemetry › prices, and each transcription and spoken answer is costed by its length,
  counting toward budgets like any model call. The prices box also shows and keeps prices per hour and "off" as
  written, where it used to show them as zero token prices ([Activity and costs](docs/activity.md#costs)).
