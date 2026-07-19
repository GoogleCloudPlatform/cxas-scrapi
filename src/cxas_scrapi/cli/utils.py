# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utility functions and classes for the CLI."""


class LazyCallable:
    """A proxy wrapper that lazily imports and executes a callable."""

    def __init__(self, module_path: str, func_name: str):
        self.module_path = module_path
        self.func_name = func_name
        self._func = None

    def __call__(self, *args, **kwargs):
        if self._func is None:
            import importlib

            module = importlib.import_module(self.module_path)
            self._func = getattr(module, self.func_name)
        return self._func(*args, **kwargs)


def cmd_help(args) -> None:
    """Handles the 'help' command."""
    from cxas_scrapi.cli.main import get_parser  # noqa: PLC0415

    parser = get_parser()
    if getattr(args, "help_command", None):
        try:
            parser.parse_args([args.help_command, "--help"])
        except SystemExit:
            pass
    else:
        parser.print_help()


