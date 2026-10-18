- **Photos and videos are dated by when they were taken.** An asset uploaded, scanned or imported is now dated by its
  own file when its name gives no date: a photo by its EXIF (`DateTimeOriginal`, else `DateTime`), a video or a
  recording by its creation time (QuickTime's `creationdate`, else `creation_time`), before the file's last change. A
  photo's GPS fix is kept as its `place` (`lat`, `lon`), which `GET /recordings/{rid}` and MCP `get_recording` give its
  namespace's members. A date in its name, or one its source gives, still wins, and a clock never set (1970, 1904) dates
  nothing ([When an asset was made](docs/processing.md#when-an-asset-was-made)).
