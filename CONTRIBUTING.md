# How to Contribute

We'd love to accept your patches and contributions to this project. There are
just a few small guidelines you need to follow.

## Contributor License Agreement

Contributions to this project must be accompanied by a Contributor License
Agreement (CLA). You (or your employer) retain the copyright to your
contribution; this simply gives us permission to use and redistribute your
contributions as part of the project. Head over to
<https://cla.developers.google.com/\> to see your current agreements on file or
to sign a new one.

You generally only need to submit a CLA once, so if you've already submitted one
(even if it was for a different project), you probably don't need to do it
again.

## Code Reviews

All submissions, including submissions by project members, require review. We
use GitHub pull requests for this purpose. Consult
[GitHub Help](https://help.github.com/articles/about-pull-requests/) for more
information on using pull requests.

## Pre-commit hooks

After cloning, install hooks once:

```bash
pip install -e ".[dev]"
pre-commit install                          # default stages
pre-commit install --hook-type commit-msg   # commit-message check
```

The `commit-msg` install is separate because pre-commit only wires the
default stages on a plain `pre-commit install`. Without it, the brand
check on commit messages won't run.

### Brand-name check (Gemini-powered)

[`scripts/check_brands.py`](scripts/check_brands.py) blocks third-party
brand mentions from landing in the staged diff or the commit message.
It sends the *added* diff lines (and the commit message) to Gemini
with the short list of allowed names (Google, GECX, CXAS, DFCX, Cymbal,
Gemini, Vertex AI, BigQuery, etc.) and blocks the commit if Gemini
reports any other company / brand / product name.

Setup:

```bash
gcloud auth application-default login
gcloud config set project <your-gcp-project>
```

**If the hook errors with `No module named 'OpenSSL'`**: your gcloud
install has configured mTLS (the `enterprise-certificate-proxy`)
without pulling in `pyOpenSSL`. Either install it
(`pip install pyopenssl`) or disable mTLS for genai by exporting
`GOOGLE_API_USE_CLIENT_CERTIFICATE=false` in your shell.

When a legitimate new product or library name should be permanently
allowed, add it to `ALLOWED_BRANDS` in `scripts/check_brands.py` in
the same PR. For a one-off emergency commit when Gemini is
unreachable, prefix the commit with `BRAND_CHECK_SKIP=1` (it logs a
warning and lets the commit through).

## Community Guidelines

This project follows
[Google's Open Source Community Guidelines](https://opensource.google/conduct/).
