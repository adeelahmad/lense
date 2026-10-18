Moving an asset to another namespace and switching a sensor are now activities of their own on the timeline, for
people and automations alike: `asset.moved` is a Move by whoever moved it, with the namespaces it left and joined and
the collection it is in now, seen where it is now; `sensor.changed` is an Update by the person who paused, ignored,
resumed or reset a sensor, with the status it had before, seen in the sensor's namespace or by admins when it has none.
Saving a status that is already set, or a rename, logs nothing.
