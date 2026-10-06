# Publishing a clean source release

The public source must contain only reusable code, examples and documentation.
Keep controller configuration, SSH keys, instance archives, journals and private
reports outside the repository. `docs/incus.example.json` contains placeholders.
No prebuilt image is distributed; build from the public base image instead.

Before changing an existing repository's visibility, scan **all Git history**
and every branch and tag. A clean working tree does not remove private details
from older commits. Secret scanners do not detect every personal identifier.
Inspect old host addresses, usernames, emails, local paths and private reports
manually. If history contains them, publish a clean source snapshot in a fresh
repository. A history rewrite alone can leave commits accessible through cached
views and pull-request references. GitHub documents those limits in
[removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Keep the original repository private when preparing a fresh public repository;
do not make an old private repository public merely because its latest tree is clean.

```sh
gitleaks dir --redact --no-banner .
gitleaks git --redact --no-banner .
uv sync --locked
uvx ruff check raft.py deploy/*.py
uvx ruff format --check raft.py deploy/*.py
uv build
```

Do not upload controller configs or development workspace exports as release
images. They can contain credentials and private files. A distributable image
must be built from a clean public base, tested, inspected for credentials and
accompanied by its fingerprint, inventories and third-party licensing information.

Review the package contents and the capability table before release. State
container isolation and the verification limits accurately. Do not claim VM
isolation or exact Boat compatibility. Raft is an independent open-source
alternative to boat.dev and is not affiliated with that service.
