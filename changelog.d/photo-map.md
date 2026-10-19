- **Photos and videos on a map.** Library → **Map** (beside Table and List, on a phone too) shows each asset whose own
  file said where it was taken as a dot, with the Library's filters, framed to show them all; a dot links to its
  asset. Besides a photo's EXIF GPS fix, a video's location is now read as it arrives (the ISO 6709 tag iPhones and
  Android phones write). Agents get the same through `GET /api/v1/resources?located=true&bbox=west,south,east,north`,
  which also gives every row its `place`, and the MCP tool `list_recordings` (`located`, `bbox`); only members see
  where an asset was taken. The tiles under the map come from Settings → **Maps** (OpenStreetMap's by default); each
  viewer's browser fetches them, so the tile server sees the area looked at: point it at your own tile server, or leave
  it empty for none ([Where an asset was taken](docs/processing.md#where-an-asset-was-taken),
  [Maps](docs/configuration.md#maps)). Assets that arrived before keep what they had: nothing needs upgrading.
