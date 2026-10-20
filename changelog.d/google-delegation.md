- **Google service accounts that act as someone sign in with the scope the admin delegated.** Google matches a
  domain-wide delegation's scopes exactly, so a client delegated all of Gmail (`https://mail.google.com/`, as backup
  tools ask for) was refused `gmail.readonly` and `gmail.compose`, and the source said only "domain-wide delegation"
  with the fix cut off. Lens now signs in with the broader scope the admin gave (all of Gmail, Calendar or Drive) and
  still calls only what the narrower scope allows. When Google refuses, the source's error names the scopes the
  delegation lacks, the ones it allows and the exact list to paste in the Admin console, in full
  ([Gmail](docs/processing.md#gmail-and-google-workspace)).
