# Releasing Ferro

Releases are built and published by [`.github/workflows/release.yml`](../.github/workflows/release.yml).
Only the steps marked **maintainer** need someone with access to the PyPI project and the GitHub
repository.

## One-time setup (maintainer)

1. Push the repository to GitHub (`git remote add origin ...`, `git push -u origin main`).
2. On GitHub: Settings → Environments → create `pypi` (add yourself as required reviewer to
   approve each publish); Settings → Code security → enable private vulnerability reporting.
3. On [pypi.org](https://pypi.org): create the project through a *pending trusted publisher*
   (Account → Publishing → "Add a new pending publisher"): project `ferro`, owner/repository,
   workflow `release.yml`, environment `pypi`. No API token is created or stored.
   If the name `ferro` is taken on PyPI, choose another distribution name in `pyproject.toml`
   (`[project] name`) — the import name `ferro` can stay.

## Every release

1. Make sure `main` is green (CI and the Release workflow's build/install-test jobs run on every
   push).
2. Set the version in `Cargo.toml` (`[workspace.package] version`, e.g. `0.5.0-rc.1` or `0.5.0`;
   Python sees `0.5.0rc1` / `0.5.0`) and run `cargo update -w` so `Cargo.lock` follows.
3. Move the changelog's entries under the new version with today's date.
4. Commit, then tag and push (**maintainer**):

   ```bash
   git tag -a v0.5.0rc1 -m "Ferro 0.5.0rc1"
   git push origin main v0.5.0rc1
   ```

5. The workflow builds 6 wheels and the sdist, installs each wheel in clean environments (3
   operating systems × Python 3.12/3.13) and runs `scripts/smoke_test.py`. When everything
   passes, the `publish` job waits for approval in the `pypi` environment (**maintainer**), then
   uploads to PyPI and creates the GitHub release with the artifacts.
6. Check `pip install ferro==0.5.0rc1` in a fresh environment.

## Building locally

```bash
maturin build --release --locked --out dist   # wheel for this machine
maturin sdist --out dist                      # source distribution
python -m venv /tmp/check && /tmp/check/bin/pip install dist/ferro-*.whl
/tmp/check/bin/python scripts/smoke_test.py
```

Linux wheels for other machines: `docker run --rm -v "$PWD":/io ghcr.io/pyo3/maturin build
--release --locked --out dist` (manylinux 2.28).
