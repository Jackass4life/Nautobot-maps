# Company packages for the API image

Python packages that aren't on PyPI, built into the API image when it is built (#790), such as your company's AD package, which the [sign-in with AD](../docs/authentication.md#sign-in-with-ad) needs. `docker compose up -d --build` passes this folder to the build (`api/Dockerfile`, the `packages` context). Nothing here except this README is ever committed (`.gitignore`). With no files, the image is the same as without the folder.

| File | What the build does with it |
|---|---|
| `requirements.txt` | Installed into the app's environment with uv, after the app's own packages. It may name an index (`--extra-index-url …`) |
| `apt-packages.txt` | Debian packages (one or more per line, `#` for comments), installed first: e.g. what a package needs to compile |
| `*.whl`, `*.tar.gz` | Found here before any index, for a package you were given as a file |

## The AD package from the company's package registry

`packages/requirements.txt`:

```
--extra-index-url https://git.example.com/api/v4/projects/<id>/packages/pypi/simple
company-auth
```

`company-auth` stands for your company's AD package: use its real name. uv asks an `--extra-index-url` before PyPI, so the company's package is the one installed, never one of the same name on PyPI. Only the AD package is needed: none of the company's other packages are used by this app.

Then:

```bash
docker compose up -d --build
docker compose exec api python -c "import company_auth; print(company_auth.__file__)"   # its import name
```

## When the build stops

- **A package wants other versions than the app's** (`No solution found … because you require …==…`): the app's own packages are held to the versions in `api/uv.lock`, so a company package can't change them. Ask for a version of that package that accepts the app's, or upgrade the app.
- **A package must be compiled** (`pkg-config`, `libxmlsec1` or `gcc` not found; rare: `xmlsec` and `lxml` come prebuilt for the image): name what it needs in `apt-packages.txt`, for `xmlsec`:

  ```
  pkg-config gcc libc6-dev libxml2-dev libxmlsec1-dev libxmlsec1-openssl
  ```

- **The index can't be reached**: the build runs on this host, so the host must reach it (`curl -sI <index URL>/company-auth/ | head -1` says `200`).
