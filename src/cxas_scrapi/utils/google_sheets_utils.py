"""Utility class for interacting with Google Sheets."""

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

import re
import sys
from typing import Any, Dict, List, Optional

import gspread
import pandas as pd
from gspread_dataframe import set_with_dataframe

from cxas_scrapi.core.common import Common

SHEETS_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsUtils(Common):
    """Utility class for dataframe functions and Google Sheets integrations."""

    def __init__(
        self,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
    ):
        # Ensure sheets scopes are included
        auth_scopes = scope or []
        for s in SHEETS_SCOPE:
            if s not in auth_scopes:
                auth_scopes.append(s)

        super().__init__(
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=auth_scopes,
        )

        try:
            self.sheets_client = gspread.authorize(self.creds)
            # Pre-flight check for local ADC auth
            if "google.colab" not in sys.modules:
                self._preflight_api_checks()
        except Exception as e:
            self.sheets_client = None
            self._handle_api_error(e)

    def _preflight_api_checks(self):
        """Runs a lightweight test to ensure Drive APIs are enabled on the
        quota project.
        Skips if running in Google Colab since Colab's native auth handles
        this differently.
        """
        if not self.sheets_client:
            return
        try:
            # Lightweight call to verify Drive API is functional
            self.sheets_client.http_client.request(
                "GET", "https://www.googleapis.com/drive/v3/files?pageSize=1"
            )
        except Exception as e:
            raise e

    def _handle_api_error(self, e: Exception) -> None:
        """Parses common Drive/Sheets API errors and raises a helpful
        exception."""
        error_msg = str(e)

        if "has not been used in project" in error_msg:
            links = re.findall(r"(https?://[^\s]+)", error_msg)
            link_text = (
                f"\n    Enable the API here: {links[0]}\n" if links else "\n"
            )

            raise PermissionError(
                f"\n{'=' * 80}\n"
                f"🚀 API ENABLEMENT REQUIRED 🚀\n"
                f"A necessary Google API (Drive or Sheets) is disabled on "
                f"your Google Cloud Project."
                f"{link_text}"
                f"After enabling, please wait 2-3 minutes for the changes "
                f"to propagate.\n"
                f"{'=' * 80}\n"
            ) from e

        elif (
            "insufficient authentication scopes" in error_msg.lower()
            or "403" in error_msg
        ):
            # Note: colab uses different auth, so this error mostly occurs for
            # local ADC
            raise PermissionError(
                f"\n{'=' * 80}\n"
                f"🚀 AUTHENTICATION FIX REQUIRED 🚀\n"
                f"Your Application Default Credentials (ADC) lack the "
                f"required Google Sheets scopes.\n"
                f"Please run the following command in your terminal:\n\n"
                f'    gcloud auth application-default login --scopes="openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/sqlservice.login,https://www.googleapis.com/auth/drive,https://spreadsheets.google.com/feeds"\n\n'
                f"{'=' * 80}\n"
            ) from e
        else:
            raise e

    def sheets_to_dataframe(
        self, sheet_name: str, worksheet_name: Optional[str] = None
    ) -> pd.DataFrame:
        """Move data from Google Sheets to a pandas DataFrame.

        Args:
            sheet_name: The name of the Google Sheet document.
            worksheet_name: The name of the specific worksheet tab. If None,
                defaults to the first sheet.

        Returns:
            A pandas DataFrame containing the sheet data.
        """
        if not self.sheets_client:
            raise RuntimeError(
                "Sheets client is not authorized. See earlier "
                "initialization errors."
            )

        try:
            g_sheets = self.sheets_client.open(sheet_name)
            if worksheet_name:
                sheet = g_sheets.worksheet(worksheet_name)
            else:
                sheet = g_sheets.sheet1

            data_pull = sheet.get_all_values()

            if not data_pull:
                return pd.DataFrame()

            data = pd.DataFrame(columns=data_pull[0], data=data_pull[1:])
            return data
        except Exception as e:
            self._handle_api_error(e)
            return pd.DataFrame()

    def dataframe_to_sheets(
        self,
        dataframe: pd.DataFrame,
        sheet_name: str,
        worksheet_name: Optional[str] = None,
    ):
        """Move data from a pandas DataFrame to Google Sheets.

        Args:
            dataframe: The pandas DataFrame to write.
            sheet_name: The name of the Google Sheet document.
            worksheet_name: The name of the specific worksheet tab. If None,
                defaults to the first sheet.
        """
        if not self.sheets_client:
            raise RuntimeError(
                "Sheets client is not authorized. See earlier "
                "initialization errors."
            )

        try:
            g_sheets = self.sheets_client.open(sheet_name)
            if worksheet_name:
                worksheet = g_sheets.worksheet(worksheet_name)
            else:
                worksheet = g_sheets.sheet1

            worksheet.clear()  # Clear existing data before writing
            set_with_dataframe(worksheet, dataframe)
        except Exception as e:
            self._handle_api_error(e)

    def append_dataframe_to_sheets(
        self,
        dataframe: pd.DataFrame,
        sheet_name: str,
        worksheet_name: Optional[str] = None,
    ):
        """Append data from a pandas DataFrame to an existing Google Sheet tab.

        Args:
            dataframe: The pandas DataFrame to append.
            sheet_name: The name of the Google Sheet document.
            worksheet_name: The name of the specific worksheet tab. If None,
                defaults to the first sheet.
        """
        existing_df = self.sheets_to_dataframe(sheet_name, worksheet_name)

        if existing_df.empty:
            combined_df = dataframe
        else:
            combined_df = pd.concat([existing_df, dataframe], ignore_index=True)

        self.dataframe_to_sheets(combined_df, sheet_name, worksheet_name)
