---
description: Validate the Python interpreter and virtual environment configured in a JetBrains project.
agent: agent
---

# Validate the Python interpreter

Validate the active JetBrains project's Python interpreter end to end. This is a
read-only diagnostic, except for the temporary probe and run configuration
described below. Do not assume a particular operating system, Python version,
environment path, module name, or environment manager.

1. Inspect project instructions and Python configuration (for example
   `pyproject.toml`, `.python-version`, dependency files, and environment
   configuration). Identify the intended Python version and environment. Use
   IDE project/module information and existing configurations to identify the
   relevant Python module and its interpreter. If there are multiple plausible
   modules or environments, ask which to validate rather than guessing.
2. Check both the project SDK and the selected module's SDK, including whether
   the module inherits the project SDK or overrides it. Resolve the SDK
   registration to its actual executable and version. Check for stale or
   mismatched project, module, and SDK references; an SDK's display name alone
   is not proof of its interpreter version. Compare the configured interpreter
   against the project's documented requirements and the intended environment.
3. Create a temporary `interpreter_probe.py` inside the selected module. If
   that path already exists, do not overwrite it: use a unique temporary
   Python filename in the same module. It must report, without third-party
   imports or network access:

   ```python
   import json
   import os
   import site
   import sys

   if __name__ == "__main__":
       print(json.dumps({
           "executable": sys.executable,
           "version": sys.version,
           "version_info": list(sys.version_info[:3]),
           "prefix": sys.prefix,
           "base_prefix": sys.base_prefix,
           "venv": sys.prefix != sys.base_prefix,
           "conda_prefix": os.environ.get("CONDA_PREFIX"),
           "cwd": os.getcwd(),
           "site_packages": site.getsitepackages(),
           "user_site_enabled": site.ENABLE_USER_SITE,
       }, indent=2))
   ```

4. Use the IDE to create a **temporary Python run configuration** for that
   probe in the selected module, using the module's actual configured
   interpreter. Run it through the IDE and capture the exit status and output.
   Do not override the interpreter to make the probe pass. If the IDE cannot
   create or run a temporary configuration, report the limitation; a terminal
   execution is useful additional evidence but is not a substitute for an IDE
   run. Do not modify or overwrite existing user run configurations.
5. Compare the observed executable, version, prefixes, and site-package paths
   with the selected SDK and intended environment. Account for platform path
   normalization, symlinks, and environment-manager semantics: for example,
   conda environments need not satisfy the `venv` boolean. Check that the
   observed version meets the project's requirements. When pip is available,
   run `python -m pip check` with the observed interpreter, reporting its
   result separately; this checks installed package consistency, not whether
   every project dependency is installed. Do not install packages, create or
   replace environments, or change SDK settings during validation.
6. Remove only the probe and persistent run configuration created for this
   validation, leaving pre-existing files and configurations untouched.
   Report any cleanup that could not be completed. If the run could not be
   performed, retain the failure details rather than reporting success.

Report the selected module, SDK name, actual interpreter path and version,
whether the IDE run succeeded, and any mismatches or unverified checks. Give
specific corrective steps for failures. Do not claim the interpreter is fully
configured unless the actual IDE run and the relevant comparisons succeeded.
