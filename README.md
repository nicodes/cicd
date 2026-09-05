# Shared engineering helpers

Portable checks used by the six Nicodes products. Product repositories keep
their Make targets, release images, public origins, secrets, database handling,
and deployment order explicit. Helpers neither publish nor deploy implicitly.

`make install` installs exact tools; `make check` exercises security repair,
export failure, and checkout process ownership boundaries and validates syntax.

Products vendor `helpers/` and `tests/` beneath `scripts/engineering/`, with a
`SOURCE.json` containing this repository's full commit SHA and every copied
file's SHA256. Update the complete snapshot together and run the product's full
`make check`. No runtime network fetch of helper code is needed.

- `dev.py`: live supervisor and authenticated local stop socket; no port-based
  killing and no persisted PID used as shutdown authority.
- `export-web.py`: clean, bounded Expo export; requires exit0, HTML and JS.
- `pins.mjs`: exact tool/module/lockfile/image/action references.
- `audit.py`: live online dependency audit; any local repair needs reviewed
  patch and installed-source hashes, adversarial tests, an owner and review date.
- `dependency-compatibility.cjs`: exercise repaired transitive dependency calls.

The normative standard remains the September4 workspace standardization plan.
Shared rollout work is tracked in nicodes/komizo-be#215.

`helpers/release.py` records the release archive hash and exact image IDs after
Build, then verifies the project, full commit, components, archive and loaded
images before publication. Publish loads those artifacts and never rebuilds.
