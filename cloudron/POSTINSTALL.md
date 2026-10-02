Open $CLOUDRON-APP-ORIGIN, enter the **setup code** to create the first admin, then follow the setup steps
(namespace, model provider, storage). The code is printed in the app's logs (search for "setup code"), or read it
in the web terminal with `grep LENS_SETUP_CODE /app/data/secrets.env`.

Recordings to watch go in `/app/data/media` (File manager, or `cloudron push`). Processing settings are in
`/app/data/archive.yaml` and in the app under Settings.
