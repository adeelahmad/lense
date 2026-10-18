- **A copy you have already isn't read again.** The same file in two folders of a source on this machine (or uploaded,
  or found by a scan) is one resource, matched by what's in it, so it's transcribed once; a web page or a video
  imported by an address the namespace has already gives that resource back, with `duplicate` true, instead of
  fetching and reading it twice. Copies on cloud storage are still matched by their path ([Imports](docs/api.md#web-pages)).
