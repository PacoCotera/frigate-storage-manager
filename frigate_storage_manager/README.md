# Frigate Storage Manager

Validate the selected Frigate app's local database and existing NFS media share,
then preview and delete old recordings and related history through Home Assistant ingress.

**Experimental 0.2.0 maintenance release.** Authorized users can confirm selected
cleanup or use **Clear everything** to remove all Frigate media and its database.
Full reset removes bookmarks and history; configuration remains intact. Preview
does not stop Frigate or modify existing media, database rows or retention settings.
Actual HAOS cleanup/recovery validation is still pending; tests use synthetic fixtures.

Supports the verified Frigate 0.17.2 schema, including build suffixes, on amd64.
Start with the Documentation tab. Do not move the live Frigate database onto NFS.
