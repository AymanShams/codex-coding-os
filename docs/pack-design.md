# Pack Design

The release pack contains managed skills, support documentation, the campaign
engine, installer, its explicit campaign hook, schemas, formal model, incident
fixtures, tests, and non-live capability-routing reference source.

`pack.manifest.json` lists required source files and installation ownership.
`install-bundle.manifest.json` records the exact file size and SHA-256 digest of
every installed payload plus one aggregate bundle digest.

The transactional installer promotes three distinct targets:

- managed skills under the configured skill root
- Coding OS support and executable runtime under the configured Codex home
- the campaign hook under the configured hook root

The `capability-routing/` reference source is retained in the repository and
release archive for review, portability, and future authorized deployment. It
is not an installation support item, runtime file, prompt hook, generated index,
or live routing authority. The transactional installer rejects attempts to add
the reference router or retired `capability-index` paths to its support payload.
The files under `capability-index/` remain historical catalogue evidence only.

The installed manifest records source commit, bundle digest, install
transaction, protocol version, schema compatibility, host capability probe
version, payload layout, target roots, and exact promoted-tree digests.

The external campaign store and read-only legacy archives are durable user
state. They are initialized and verified but are not release payload files and
are not removed by uninstall.
