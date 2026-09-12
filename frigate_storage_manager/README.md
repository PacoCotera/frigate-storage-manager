# Frigate Storage Manager

Validate the selected Frigate app's local database and existing NFS media share,
then preview old recordings and related history through Home Assistant ingress.

**Experimental 0.1.1 validation release. Deletion and recovery mutations are locked
pending real HAOS validation.** Preview does not stop Frigate or modify existing
recordings, database rows or retention settings. The full offline cleanup/recovery
engine is tested with synthetic fixtures behind the disabled release gate.

Supports the verified Frigate 0.17.2 schema, including build suffixes, on amd64.
Start with the Documentation tab. Do not move the live Frigate database onto NFS.
