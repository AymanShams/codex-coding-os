# Pack Design

The release pack contains managed skills, support documentation, the campaign
engine, installer, hooks, schemas, formal model, incident fixtures, and tests.

`pack.manifest.json` lists required source files and installation ownership.
`install-bundle.manifest.json` records the exact file size and SHA-256 digest of
every installed payload plus one aggregate bundle digest.

The transactional installer promotes three distinct targets:

- managed skills under the configured skill root
- Coding OS support and executable runtime under the configured Codex home
- the campaign hook under the configured hook root

The installed manifest records source commit, bundle digest, install
transaction, protocol version, schema compatibility, host capability probe
version, payload layout, target roots, and exact promoted-tree digests.

The external campaign store and read-only legacy archives are durable user
state. They are initialized and verified but are not release payload files and
are not removed by uninstall.
