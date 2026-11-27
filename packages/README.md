# Company packages for the API image

Python packages that aren't on PyPI, built into the API image when it is built (#790), such as `sit_core`, which the [sign-in with AD](../docs/authentication.md#sign-in-with-ad) needs. `docker compose up -d --build` passes this folder to the build (`api/Dockerfile`, the `packages` context). Nothing here except this README is ever committed (`.gitignore`). With no files, the image is the same as without the folder.

| File | What the build does with it |
|---|---|
| `requirements.txt` | Installed into the app's environment with uv, after the app's own packages. It may name an index (`--extra-index-url …`) |
| `apt-packages.txt` | Debian packages (one or more per line, `#` for comments), installed first: e.g. what a package needs to compile |
| `*.whl`, `*.tar.gz` | Found here before any index, for a package you were given as a file |

## sit_core from the company's package registry

`packages/requirements.txt`:

```
--extra-index-url https://git.example.com/api/v4/projects/<id>/packages/pypi/simple
sit-core>=0.1.46
```

uv asks an `--extra-index-url` before PyPI, so the company's `sit-core` is the one installed, never a package of the same name on PyPI. Only `sit-core` is needed: `sit-librenms` and `sit-nautobot` aren't used by this app.

Then:

```bash
docker compose up -d --build
docker compose exec api python -c "import sit_core; print(sit_core.__file__)"
```

## When the build stops

- **A package wants other versions than the app's** (`No solution found … because you require …==…`): the app's own packages are held to the versions in `api/uv.lock`, so a company package can't change them. Ask for a version of that package that accepts the app's, or upgrade the app.
- **A package must be compiled** (`pkg-config`, `libxmlsec1` or `gcc` not found; rare: `xmlsec` and `lxml` come prebuilt for the image): name what it needs in `apt-packages.txt`, for `xmlsec`:

  ```
  pkg-config gcc libc6-dev libxml2-dev libxmlsec1-dev libxmlsec1-openssl
  ```

- **The index can't be reached**: the build runs on this host, so the host must reach it (`curl -sI <index URL>/sit-core/ | head -1` says `200`).
