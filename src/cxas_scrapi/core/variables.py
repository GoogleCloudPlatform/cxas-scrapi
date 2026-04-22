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

"""Core Variables class for CXAS Scrapi."""

import logging
from typing import Any, Dict, List, Optional

from google.cloud.ces_v1beta import types
from proto.marshal.collections import maps, repeated

from cxas_scrapi.core.apps import Apps


class Variables(Apps):
    """Core Class for managing Variables (App Resources)."""

    def __init__(
        self,
        app_name: str,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        **kwargs,
    ):
        """Initializes the Variables client.

        Note that Variables are resources of the App itself, not a standalone
        resource. This class is a wrapper around the App class to make it
        easier to manage Variables.
        """
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]

        super().__init__(
            project_id=project_id,
            location=location,
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            **kwargs,
        )
        self.app_name = app_name
        self.app_id = app_name.rsplit("/", maxsplit=1)[-1]
        self.resource_type = "variables"

    @staticmethod
    def _check_schema_type(input_type: str):
        # these are the only valid types mapping to
        # types.App.VariableDeclaration.Schema.Type
        if input_type.upper() not in [
            "STRING",
            "INTEGER",
            "NUMBER",
            "BOOLEAN",
            "OBJECT",
            "ARRAY",
        ]:
            raise ValueError(f"Invalid schema type: {input_type}")

    @staticmethod
    def variable_to_dict(variable: Any) -> Any:
        """Converts VariableDeclaration to a dictionary or value."""

        # 1. Handle RepeatedComposite (List)
        if isinstance(variable, repeated.RepeatedComposite):
            return [Variables.variable_to_dict(v) for v in variable]

        # 2. Handle MapComposite (Dict)
        if isinstance(variable, maps.MapComposite):
            return {
                k: Variables.variable_to_dict(v) for k, v in variable.items()
            }

        # 3. If it's already a dict or primitive, return as is
        if isinstance(
            variable, (dict, list, str, int, float, bool, type(None))
        ):
            return variable

        # 4. Priority: Check for schema.default (VariableDeclaration pattern)
        try:
            if hasattr(variable, "schema") and hasattr(
                variable.schema, "default"
            ):
                return Variables.variable_to_dict(variable.schema.default)
        except (AttributeError, KeyError, TypeError):
            pass

        # 5. Check if it has a to_dict method (common in Google Protobufs)
        if hasattr(variable, "to_dict"):
            return variable.to_dict()

        # 6. Check if it has a to_dict method on the type
        if hasattr(type(variable), "to_dict"):
            return type(variable).to_dict(variable)

        return variable

    def list_variables(self) -> List[Any]:
        """Lists variables within a specific app."""
        app = self.get_app(self.app_name)
        return list(app.variable_declarations)

    def get_variable(self, variable_name: str) -> Optional[Any]:
        """Gets a specific variable by its name within a specified app."""
        vars_list = self.list_variables()

        for var in vars_list:
            if var.name == variable_name:
                return var

        return None

    def create_variable(
        self,
        variable_name: str,
        variable_type: str,
        variable_value: Optional[Any],
    ) -> None:
        """Creates a new variable within a specified app."""
        self._check_schema_type(variable_type)
        app = self.get_app(self.app_name)
        vars_list = list(app.variable_declarations)

        for var in vars_list:
            if var.name == variable_name:
                logging.warning(f"Variable '{variable_name}' already exists.")
                return

        new_var = types.App.VariableDeclaration(
            name=variable_name,
            schema={"type_": variable_type.upper(), "default": variable_value},
        )

        vars_list.append(new_var)
        self.update_app(self.app_name, variable_declarations=vars_list)
        logging.info(f"Variable '{variable_name}' created successfully.")

    def update_variable(
        self,
        variable_name: str,
        variable_type: str,
        variable_value: Optional[Any],
    ) -> None:
        """Updates a variable within a specific app.

        Acceptable types: STRING, INTEGER, NUMBER, BOOLEAN, ARRAY, OBJECT
        """
        self._check_schema_type(variable_type)
        app = self.get_app(self.app_name)
        vars_list = list(app.variable_declarations)

        updated = False
        for var in vars_list:
            if var.name == variable_name:
                var.schema.type_ = getattr(
                    types.App.VariableDeclaration.Schema.Type,
                    variable_type.upper(),
                )
                var.schema.default = variable_value
                updated = True
                break

        if not updated:
            new_var = types.App.VariableDeclaration(
                name=variable_name,
                schema={
                    "type_": variable_type.upper(),
                    "default": variable_value,
                },
            )
            vars_list.append(new_var)

        self.update_app(self.app_name, variable_declarations=vars_list)
        logging.info(f"Variable '{variable_name}' set successfully.")

    def delete_variable(self, variable_name: str) -> None:
        """Deletes a specific variable within a specified app."""
        app = self.get_app(self.app_name)
        vars_list = list(app.variable_declarations)

        original_len = len(vars_list)
        vars_list = [v for v in vars_list if v.name != variable_name]

        if len(vars_list) < original_len:
            self.update_app(self.app_name, variable_declarations=vars_list)
            logging.info(f"Variable '{variable_name}' deleted successfully.")
        else:
            logging.warning(f"Variable '{variable_name}' not found.")
