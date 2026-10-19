- **WebVTT voices name the speaker.** A transcript imported from a WebVTT file now credits each line to the speaker its
  voice span names, `<v Name>` or `<v.loud Name>` with classes, closed by `</v>` or not, and those speakers join the
  namespace's people like any other named speaker. A cue with two voices becomes two lines that share its time; the tags
  are left out of the text, and a cue without a voice is read as before. A YouTube caption cue that starts with a line
  of one space is no longer lost ([Importing text and transcripts](docs/processing.md#importing-transcripts)).
