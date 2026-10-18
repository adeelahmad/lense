- **Access that ends.** Giving someone a role in a namespace, a role on a collection or permission on an asset now
  takes an optional end date: an "Until" day in the asset's Access dialog, a collection's People and a namespace's
  members, `expires_at` in the API, and `until` on the assistant's `change_person`. After it, the grant opens nothing
  anywhere access is checked, and lists show it as expired until it's given again or taken away. The field starts
  empty, so grants without an end work as before ([Grants that end](docs/access.md#grants-that-end)).
