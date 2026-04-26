---
title: Common
---

# Common

`Common` is the authentication base class that all other CXAS SCRAPI classes inherit from. It handles GCP credential management and shared utilities, so you don't need to worry about authentication plumbing in subclasses.

You typically won't instantiate `Common` directly — instead, you'll use classes like `Apps`, `Agents`, or `Sessions` that inherit from it.

## Reference

::: cxas_scrapi.core.common.Common
