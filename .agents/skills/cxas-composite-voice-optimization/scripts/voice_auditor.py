"""Standalone CXAS Voice Configuration Auditor and Auto-Remediator.

Provides pure local file inspection and remediation for Google Cloud CX Agent
Studio (CXAS) / Customer Engagement Suite (CES) applications using Gemini
Composite V1 voice naturalness, persona stability, and multi-language coverage.
Operates hermetically without external dependencies or cloud API calls.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Locale code to Natural Language Accent mapping
LOCALE_TO_ACCENT: Dict[str, str] = {
    # Base 2-letter languages
    "en": "American English",
    "es": "Spanish accent",
    "fr": "Metropolitan French",
    "de": "German",
    "ja": "Japanese",
    "pt": "Brazilian Portuguese",
    "it": "Italian",
    "zh": "Mandarin Chinese",
    "ko": "Korean",
    "nl": "Dutch",
    "hi": "Hindi",
    "ar": "Arabic",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "tr": "Turkish",
    # English regional dialects
    "en-US": "American English",
    "en-GB": "British English",
    "en-AU": "Australian English",
    "en-IE": "Contemporary Irish English",
    "en-CA": "Canadian English",
    "en-IN": "Indian English",
    "en-NZ": "New Zealand English",
    "en-ZA": "South African English",
    "en-SG": "Singapore English",
    # Spanish regional dialects
    "es-US": "Spanish accent",
    "es-419": "Latin American Spanish",
    "es-ES": "Castilian Spanish",
    "es-MX": "Latin American Spanish",
    "es-CO": "Latin American Spanish",
    "es-AR": "Latin American Spanish",
    "es-CL": "Latin American Spanish",
    "es-PE": "Latin American Spanish",
    # French regional dialects
    "fr-FR": "Metropolitan French",
    "fr-CA": "French Canadian",
    "fr-BE": "Metropolitan French",
    "fr-CH": "Metropolitan French",
    # German regional dialects
    "de-DE": "German",
    "de-AT": "German",
    "de-CH": "German",
    # Portuguese regional dialects
    "pt-BR": "Brazilian Portuguese",
    "pt-PT": "European Portuguese",
    # Italian
    "it-IT": "Italian",
    # Asian and other languages
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Mandarin Chinese",
    "zh-TW": "Taiwanese Mandarin",
    "zh-HK": "Cantonese",
    "nl-NL": "Dutch",
    "nl-BE": "Flemish Dutch",
    "hi-IN": "Hindi",
    "ar-XA": "Modern Standard Arabic",
    "sv-SE": "Swedish",
    "nb-NO": "Norwegian Bokmål",
    "da-DK": "Danish",
    "fi-FI": "Finnish",
    "pl-PL": "Polish",
    "tr-TR": "Turkish",
}

# Default Chirp3 HD voice mapping
DEFAULT_VOICES: Dict[str, str] = {
    # Base 2-letter languages
    "en": "en-US-Chirp3-HD-Aoede",
    "es": "es-US-Chirp3-HD-Aoede",
    "fr": "fr-FR-Chirp3-HD-Aoede",
    "de": "de-DE-Chirp3-HD-Aoede",
    "ja": "ja-JP-Chirp3-HD-Aoede",
    "pt": "pt-BR-Chirp3-HD-Aoede",
    "it": "it-IT-Chirp3-HD-Aoede",
    "zh": "cmn-CN-Chirp3-HD-Aoede",
    "ko": "ko-KR-Chirp3-HD-Aoede",
    "nl": "nl-NL-Chirp3-HD-Aoede",
    "hi": "hi-IN-Chirp3-HD-Aoede",
    "ar": "ar-XA-Chirp3-HD-Aoede",
    "sv": "sv-SE-Chirp3-HD-Aoede",
    "no": "nb-NO-Chirp3-HD-Aoede",
    "nb": "nb-NO-Chirp3-HD-Aoede",
    "da": "da-DK-Chirp3-HD-Aoede",
    "fi": "fi-FI-Chirp3-HD-Aoede",
    "pl": "pl-PL-Chirp3-HD-Aoede",
    "tr": "tr-TR-Chirp3-HD-Aoede",
    # Regional BCP-47 locales
    "en-US": "en-US-Chirp3-HD-Aoede",
    "en-GB": "en-GB-Chirp3-HD-Aoede",
    "en-AU": "en-AU-Chirp3-HD-Aoede",
    "en-IE": "en-IE-Chirp3-HD-Aoede",
    "en-CA": "en-CA-Chirp3-HD-Aoede",
    "en-IN": "en-IN-Chirp3-HD-Aoede",
    "en-NZ": "en-AU-Chirp3-HD-Aoede",
    "en-ZA": "en-GB-Chirp3-HD-Aoede",
    "en-SG": "en-GB-Chirp3-HD-Aoede",
    "es-US": "es-US-Chirp3-HD-Aoede",
    "es-419": "es-US-Chirp3-HD-Aoede",
    "es-ES": "es-ES-Chirp3-HD-Aoede",
    "es-MX": "es-US-Chirp3-HD-Aoede",
    "es-CO": "es-US-Chirp3-HD-Aoede",
    "es-AR": "es-US-Chirp3-HD-Aoede",
    "es-CL": "es-US-Chirp3-HD-Aoede",
    "es-PE": "es-US-Chirp3-HD-Aoede",
    "fr-FR": "fr-FR-Chirp3-HD-Aoede",
    "fr-CA": "fr-CA-Chirp3-HD-Aoede",
    "fr-BE": "fr-FR-Chirp3-HD-Aoede",
    "fr-CH": "fr-FR-Chirp3-HD-Aoede",
    "de-DE": "de-DE-Chirp3-HD-Aoede",
    "de-AT": "de-DE-Chirp3-HD-Aoede",
    "de-CH": "de-DE-Chirp3-HD-Aoede",
    "ja-JP": "ja-JP-Chirp3-HD-Aoede",
    "pt-BR": "pt-BR-Chirp3-HD-Aoede",
    "pt-PT": "pt-BR-Chirp3-HD-Aoede",
    "it-IT": "it-IT-Chirp3-HD-Aoede",
    "ko-KR": "ko-KR-Chirp3-HD-Aoede",
    "zh-CN": "cmn-CN-Chirp3-HD-Aoede",
    "zh-TW": "cmn-TW-Chirp3-HD-Aoede",
    "zh-HK": "yue-HK-Chirp3-HD-Aoede",
    "nl-NL": "nl-NL-Chirp3-HD-Aoede",
    "nl-BE": "nl-NL-Chirp3-HD-Aoede",
    "hi-IN": "hi-IN-Chirp3-HD-Aoede",
    "ar-XA": "ar-XA-Chirp3-HD-Aoede",
    "sv-SE": "sv-SE-Chirp3-HD-Aoede",
    "nb-NO": "nb-NO-Chirp3-HD-Aoede",
    "da-DK": "da-DK-Chirp3-HD-Aoede",
    "fi-FI": "fi-FI-Chirp3-HD-Aoede",
    "pl-PL": "pl-PL-Chirp3-HD-Aoede",
    "tr-TR": "tr-TR-Chirp3-HD-Aoede",
}

NORMALIZED_LOCALE_TO_ACCENT: Dict[str, str] = {
    k.lower().replace("_", "-"): v for k, v in LOCALE_TO_ACCENT.items()
}
NORMALIZED_DEFAULT_VOICES: Dict[str, str] = {
    k.lower().replace("_", "-"): v for k, v in DEFAULT_VOICES.items()
}


def get_locale_accent(locale: str) -> str:
  """Returns natural language accent for a locale string."""
  normalized = locale.strip().lower().replace("_", "-")
  if normalized in NORMALIZED_LOCALE_TO_ACCENT:
    return NORMALIZED_LOCALE_TO_ACCENT[normalized]
  lang_prefix = normalized.split("-")[0]
  if lang_prefix in NORMALIZED_LOCALE_TO_ACCENT:
    return NORMALIZED_LOCALE_TO_ACCENT[lang_prefix]
  # If the string is already a descriptive natural language accent description
  if any(
      kw in locale.lower()
      for kw in ["english", "spanish", "french", "german", "accent", "italian"]
  ):
    return locale.strip()
  return f"{locale.strip()} accent"


def get_default_voice(locale: str) -> str:
  """Returns default Chirp3 HD voice for a locale string."""
  normalized = locale.strip().lower().replace("_", "-")
  if normalized in NORMALIZED_DEFAULT_VOICES:
    return NORMALIZED_DEFAULT_VOICES[normalized]
  lang_prefix = normalized.split("-")[0]
  if lang_prefix in NORMALIZED_DEFAULT_VOICES:
    return NORMALIZED_DEFAULT_VOICES[lang_prefix]
  return DEFAULT_VOICES.get(locale, f"{locale}-Chirp3-HD-Aoede")


def _find_speech_config(
    speech_configs: Dict[str, Any], locale: str
) -> Optional[Tuple[str, Dict[str, Any]]]:
  """Finds matching synthesizeSpeechConfig entry case-insensitively with root fallback."""
  if not isinstance(speech_configs, dict):
    return None
  # 1. Exact match
  if locale in speech_configs and isinstance(speech_configs[locale], dict):
    return locale, speech_configs[locale]

  norm_locale = locale.strip().lower().replace("_", "-")
  # 2. Normalized match
  for k, v in speech_configs.items():
    if isinstance(k, str) and isinstance(v, dict):
      if k.strip().lower().replace("_", "-") == norm_locale:
        return k, v

  # 3. Root language fallback (e.g. 'es' serves 'es-US' or 'es-419')
  root_lang = norm_locale.split("-")[0]
  for k, v in speech_configs.items():
    if isinstance(k, str) and isinstance(v, dict):
      if k.strip().lower().replace("_", "-") == root_lang:
        return k, v

  return None


# Prohibited platform-level XML tags that trigger thought-leakage regex
# safety filters.
PROHIBITED_XML_TAGS: List[str] = [
    "state_update",
    "context",
    "reasoning",
    "thought",
    "internal",
    "call_tool",
    "parameter_update",
    "variable_update",
]

# 43+ Inert / Ineffective Tags (31 abstract emotions, 8 pauses, 6 styles)
INERT_TAGS: List[str] = [
    # Abstract emotions (31)
    "warm",
    "calm",
    "clear",
    "professional",
    "empathetic",
    "reassuring",
    "sympathetic",
    "hope",
    "happy",
    "crying",
    "awe",
    "fearful",
    "surprised",
    "cautious",
    "alarm",
    "anxiety",
    "relief",
    "tension",
    "determination",
    "enthusiasm",
    "adoration",
    "interest",
    "curiosity",
    "annoyance",
    "aggression",
    "nervousness",
    "neutral",
    "negative",
    "positive",
    "admiration",
    "disgusted",
    # Prosody & pause bracket tags
    "short pause",
    "long pause",
    "short_pause",
    "long_pause",
    "medium pause",
    "medium_pause",
    'prosody rate="85%"',
    'prosody rate="115%"',
    # Delivery styles (6)
    "formal",
    "casual",
    "mumbles",
    "stammers",
    "breathless",
    "panic",
]

# 23 Empirically Verified Working Physical Acoustic Tags
WORKING_TAGS: List[str] = [
    "whispers",
    "whispering",
    "sigh",
    "sighs",
    "chuckles",
    "laughs",
    "gasp",
    "exhales",
    "clears throat",
    "slow",
    "slower",
    "fast",
    "faster",
    "confusion",
    "sleepy",
    "bored",
    "seriousness",
    "serious",
    "deadpan",
    "excitement",
    "excited",
    "celebratory",
    "yelling",
]


def generate_golden_directors_note(locale: str) -> str:
  """Generates a complete Golden Director's Note prompt for a given locale."""
  accent = get_locale_accent(locale)
  normalized_locale = locale.strip().lower().replace("_", "-")

  if normalized_locale.startswith("es"):
    bridge_words = 'bridge words (like "eh," "ah," or "veamos")'
  elif normalized_locale.startswith("fr"):
    bridge_words = 'bridge words (like "euh," "bah," or "voyons")'
  elif normalized_locale.startswith("de"):
    bridge_words = 'bridge words (like "äh," "öh," or "schauen wir mal")'
  elif normalized_locale.startswith("pt"):
    bridge_words = 'bridge words (like "é," "hum," or "vejamos")'
  elif normalized_locale.startswith("it"):
    bridge_words = 'bridge words (like "eh," "ehm," or "vediamo")'
  elif normalized_locale.startswith("ja"):
    bridge_words = 'bridge words (like "ええと," "あの," or "そうですね")'
  elif normalized_locale == "en-gb":
    bridge_words = 'bridge words (like "um," "er," or "ah")'
  elif normalized_locale == "en-ie":
    bridge_words = 'bridge words (like "um," "ah," or "well")'
  elif normalized_locale == "en-au":
    bridge_words = 'bridge words (like "um," "ah," or "yeah")'
  else:
    bridge_words = 'bridge words (like "um," "ah," or "hmm")'

  return (
      "Read the following transcript based on the audio profile and director's"
      " note. You must read the entire transcript strictly verbatim.\n\n# Audio"
      " Profile\nYou are a real human being working in customer"
      " care—warm, patient, and highly empathetic. You are sitting at your"
      " desk, answering a live phone call. This delivery must sound 100%"
      " unscripted, like a genuine, spontaneous conversation with someone you"
      " truly want to help.\n\n# Director's note\n* Persona & Tone: Friendly,"
      " grounded, and authentically conversational. Imagine you are helping a"
      ' neighbor. Speak with a subtle "smile" in your voice, keeping the'
      " energy upbeat but deeply reassuring.\n* Pacing: Keep the pace brisk and"
      " efficient, like a busy but highly competent agent quickly relaying"
      " information. Do not over-exaggerate pauses. Keep filler words"
      " incredibly brief.\n* Intonation & Emotion: Use natural, dynamic pitch"
      " variations to express active listening and engagement. Avoid sounding"
      " monotone, rigid, or like an automated recording. Let genuine human"
      " warmth guide your vocal melody.\n* Realism & Imperfections: If"
      f" {bridge_words} are in the text, deliver them naturally and"
      " thoughtfully, exactly as a human does when searching for information or"
      " gathering their thoughts.\n* Consistency: Maintain your natural, human"
      " conversational style throughout the entire read. When reading phone"
      " numbers, account IDs, or digits, group them naturally with slight"
      " pauses (e.g., reading a phone number in clusters), just as you would"
      " when reading numbers off a screen to a friend.\n*"
      f" Accent: {accent}\n\n## Transcript:\n"
  )


class InstructionTarget:
  """Represents an instruction source (either a .txt file or agent.json instruction field)."""

  def __init__(self, file_path: pathlib.Path, workspace_path: pathlib.Path):
    self.file_path = file_path
    self.workspace_path = workspace_path
    self.rel_path = str(file_path.relative_to(workspace_path))
    self.is_json = file_path.name == "agent.json"

  def get_content(self) -> Optional[str]:
    """Retrieves instruction content safely."""
    if not self.file_path.exists():
      return None
    try:
      raw = self.file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
      return None
    if self.is_json:
      try:
        data = json.loads(raw)
        if isinstance(data, dict):
          inst = data.get("instruction")
          return inst if isinstance(inst, str) else None
        return None
      except json.JSONDecodeError:
        return None
    return raw

  def set_content(self, new_content: str) -> bool:
    """Updates instruction content safely."""
    if not self.file_path.exists():
      return False
    try:
      if self.is_json:
        raw = self.file_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
          data["instruction"] = new_content
          self.file_path.write_text(
              json.dumps(data, indent=2, ensure_ascii=False) + "\n",
              encoding="utf-8",
          )
          return True
        return False
      else:
        self.file_path.write_text(new_content, encoding="utf-8")
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
      return False


def _is_inside_quotes(text: str, start_idx: int, end_idx: int) -> bool:
  """Checks if a match span is enclosed within quotes."""
  quote_patterns = [
      r'"([^"\\]*(?:\\.[^"\\]*)*)"',
      r"(?<!\w)'([^'\\]*(?:\\.[^'\\]*)*)'(?!\w)",
      r'“([^“”]*)”',
      r'‘([^‘’]*)’',
  ]
  for q_pat in quote_patterns:
    for q_match in re.finditer(q_pat, text):
      if q_match.start() <= start_idx and end_idx <= q_match.end():
        return True
  return False


class CXASVoiceAuditor:
  """Local workspace auditor and remediator for CXAS voice configurations."""

  def __init__(self, workspace_dir: pathlib.Path | str):
    self.workspace_path = pathlib.Path(workspace_dir).resolve()
    self.app_json_path = self._find_app_json()

  def _find_app_json(self) -> pathlib.Path:
    """Locates the primary app.json in the workspace."""
    direct = self.workspace_path / "app.json"
    if direct.is_file():
      return direct

    for p in self.workspace_path.glob("**/app.json"):
      rel_parts = p.relative_to(self.workspace_path).parts
      if p.is_file() and not any(
          part.startswith(".") or part == "build" for part in rel_parts
      ):
        return p

    # Fallback to direct path if none found (for lazy evaluation / mock tests)
    return direct

  def _find_instruction_targets(self) -> List[InstructionTarget]:
    """Finds all instruction targets across the workspace."""
    targets: List[InstructionTarget] = []
    seen_paths: set[pathlib.Path] = set()

    # 1. Plain text instruction files
    patterns = [
        "**/global_instruction.txt",
        "**/instruction.txt",
    ]
    for pattern in patterns:
      for p in sorted(self.workspace_path.glob(pattern)):
        rel_parts = p.relative_to(self.workspace_path).parts
        if p.is_file() and not any(
            part.startswith(".") or part == "build" for part in rel_parts
        ):
          if p not in seen_paths:
            seen_paths.add(p)
            targets.append(InstructionTarget(p, self.workspace_path))

    # 2. agent.json files (only if they carry an inlined 'instruction' field)
    for p in sorted(self.workspace_path.glob("**/agent.json")):
      rel_parts = p.relative_to(self.workspace_path).parts
      if p.is_file() and not any(
          part.startswith(".") or part == "build" for part in rel_parts
      ):
        if p not in seen_paths:
          target = InstructionTarget(p, self.workspace_path)
          content = target.get_content()
          if content is not None:
            seen_paths.add(p)
            targets.append(target)

    return targets

  def _read_app_json(self) -> Dict[str, Any]:
    """Reads and parses app.json safely, guaranteeing a dict return."""
    if not self.app_json_path.exists():
      return {}
    try:
      with open(self.app_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
      return {}

  def _write_app_json(self, data: Dict[str, Any]) -> None:
    """Writes updated data to app.json formatted with 2-space indentation."""
    self.app_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(self.app_json_path, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2, ensure_ascii=False)
      f.write("\n")

  # =========================================================================
  # AUDIT METHODS
  # =========================================================================

  def audit_audio_profile(
      self, app_data: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    """Audits the top-level Audio Profile and Director's Note configuration."""
    if app_data is None:
      app_data = self._read_app_json()
    if not isinstance(app_data, dict):
      app_data = {}

    issues: List[Dict[str, Any]] = []
    audio_cfg = app_data.get("audioProcessingConfig")
    if not isinstance(audio_cfg, dict):
      audio_cfg = {}
    speech_configs = audio_cfg.get("synthesizeSpeechConfigs")

    if not isinstance(speech_configs, dict) or not speech_configs:
      issues.append({
          "code": "MISSING_SYNTHESIZE_SPEECH_CONFIGS",
          "message": (
              "app.json is missing"
              " audioProcessingConfig.synthesizeSpeechConfigs."
          ),
      })
    else:
      for locale, cfg in speech_configs.items():
        if not isinstance(cfg, dict):
          issues.append({
              "code": "MISSING_DIRECTORS_NOTE",
              "locale": locale,
              "message": (
                  f"synthesizeSpeechConfigs[{locale}] lacks a Director's Note"
                  " instruction."
              ),
          })
          continue
        instruction = cfg.get("instruction")
        if not isinstance(instruction, str) or not instruction:
          issues.append({
              "code": "MISSING_DIRECTORS_NOTE",
              "locale": locale,
              "message": (
                  f"synthesizeSpeechConfigs[{locale}] lacks a Director's Note"
                  " instruction."
              ),
          })
        else:
          has_audio_profile = bool(
              re.search(
                  r"^#+\s*audio\s*profile",
                  instruction,
                  re.IGNORECASE | re.MULTILINE,
              )
          )
          if not has_audio_profile:
            issues.append({
                "code": "MISSING_AUDIO_PROFILE_HEADER",
                "locale": locale,
                "message": (
                    f"synthesizeSpeechConfigs[{locale}].instruction missing"
                    " '# Audio Profile' header."
                ),
            })
          has_directors_note = bool(
              re.search(
                  r"^#+\s*director'?s\s*notes?",
                  instruction,
                  re.IGNORECASE | re.MULTILINE,
              )
          )
          if not has_directors_note:
            issues.append({
                "code": "MISSING_DIRECTORS_NOTE_HEADER",
                "locale": locale,
                "message": (
                    f"synthesizeSpeechConfigs[{locale}].instruction missing"
                    " '# Director's note' header."
                ),
            })
          if (
              "## Transcript:" not in instruction
              and "### TRANSCRIPT:" not in instruction
          ):
            issues.append({
                "code": "MISSING_TRANSCRIPT_HOOK",
                "locale": locale,
                "message": (
                    f"synthesizeSpeechConfigs[{locale}].instruction missing"
                    " trailing '## Transcript:' hook."
                ),
            })

    return {
        "passed": len(issues) == 0,
        "issues": issues,
    }

  def audit_accent_specifications(
      self, app_data: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    """Audits accent directives to ensure natural language strings are used."""
    if app_data is None:
      app_data = self._read_app_json()
    if not isinstance(app_data, dict):
      app_data = {}

    issues: List[Dict[str, Any]] = []
    audio_cfg = app_data.get("audioProcessingConfig")
    if not isinstance(audio_cfg, dict):
      audio_cfg = {}
    speech_configs = audio_cfg.get("synthesizeSpeechConfigs")
    if not isinstance(speech_configs, dict):
      speech_configs = {}

    locale_code_pattern = re.compile(
        r"Accent:\s*([A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,3})?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    for locale, cfg in speech_configs.items():
      if not isinstance(cfg, dict):
        continue
      instruction = cfg.get("instruction")
      if not isinstance(instruction, str):
        continue
      for match in locale_code_pattern.finditer(instruction):
        invalid_code = match.group(1).strip()
        recommended = get_locale_accent(invalid_code)
        if recommended.lower() != invalid_code.lower() and (
            "-" in invalid_code or "_" in invalid_code or len(invalid_code) == 2
        ):
          issues.append({
              "code": "LOCALE_CODE_IN_ACCENT",
              "locale": locale,
              "found": match.group(0).strip(),
              "invalid_code": invalid_code,
              "recommended": f"Accent: {recommended}",
              "message": (
                  f"Locale code '{invalid_code}' used in Accent directive. Use"
                  f" '{recommended}' instead."
              ),
          })

    return {
        "passed": len(issues) == 0,
        "issues": issues,
    }

  def audit_rule_a007_multilang(
      self, app_data: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    """Audits Rule A007 multi-language voice coverage and parity."""
    if app_data is None:
      app_data = self._read_app_json()
    if not isinstance(app_data, dict):
      app_data = {}

    issues: List[Dict[str, Any]] = []
    lang_settings = app_data.get("languageSettings")
    if not isinstance(lang_settings, dict):
      lang_settings = {}
    default_lang = lang_settings.get("defaultLanguageCode")
    supported_langs = lang_settings.get("supportedLanguageCodes")
    if not isinstance(supported_langs, list):
      supported_langs = []

    all_declared_langs = set()
    if default_lang and isinstance(default_lang, str):
      all_declared_langs.add(default_lang)
    all_declared_langs.update(
        [l for l in supported_langs if isinstance(l, str)]
    )

    audio_cfg = app_data.get("audioProcessingConfig")
    if not isinstance(audio_cfg, dict):
      audio_cfg = {}
    speech_configs = audio_cfg.get("synthesizeSpeechConfigs")
    if not isinstance(speech_configs, dict):
      speech_configs = {}

    # Check synthesizeSpeechConfigs coverage case-insensitively with root fallback
    for lang in sorted(all_declared_langs):
      match_res = _find_speech_config(speech_configs, lang)
      if not match_res:
        issues.append({
            "code": "A007_FAIL_MISSING_LANG",
            "locale": lang,
            "message": (
                f"Declared language '{lang}' has no entry in"
                " audioProcessingConfig.synthesizeSpeechConfigs."
            ),
        })
      else:
        matched_key, cfg = match_res
        if not cfg.get("voice") or not isinstance(cfg.get("voice"), str):
          issues.append({
              "code": "A007_FAIL_MISSING_VOICE",
              "locale": lang,
              "message": (
                  f"Declared language '{lang}' (key: '{matched_key}') is"
                  " missing a voice identifier."
              ),
          })
        instruction = cfg.get("instruction")
        has_directors_note = bool(
            isinstance(instruction, str)
            and re.search(
                r"^#+\s*director'?s\s*notes?",
                instruction,
                re.IGNORECASE | re.MULTILINE,
            )
        )
        if (
            not isinstance(instruction, str)
            or not instruction
            or not has_directors_note
        ):
          issues.append({
              "code": "A007_FAIL_MISSING_INSTRUCTION",
              "locale": lang,
              "message": (
                  f"Declared language '{lang}' (key: '{matched_key}') is"
                  " missing a complete Director's Note instruction."
              ),
          })
        else:
          # Check cross-language accent contradictions
          accent_match = re.search(
              r"\bAccent:\s*([^\n\r]+)", instruction, re.IGNORECASE
          )
          if accent_match:
            found_accent = accent_match.group(1).strip()
            expected_accent = get_locale_accent(matched_key)
            lang_accent = get_locale_accent(lang)
            acceptable_accents = {
                expected_accent.lower(),
                lang_accent.lower(),
                matched_key.lower(),
                matched_key.lower().replace("_", "-"),
                lang.lower(),
                lang.lower().replace("_", "-"),
            }
            if found_accent.lower() not in acceptable_accents:
              issues.append({
                  "code": "A007_FAIL_ACCENT_MISMATCH",
                  "locale": lang,
                  "found": found_accent,
                  "expected": expected_accent,
                  "message": (
                      f"Config for '{lang}' specifies 'Accent: {found_accent}'."
                      f" Change to 'Accent: {expected_accent}'."
                  ),
              })

    # Check session variable user_lang
    var_decls = app_data.get("variableDeclarations")
    if not isinstance(var_decls, list):
      var_decls = []
    var_names = [
        v.get("name")
        for v in var_decls
        if isinstance(v, dict) and isinstance(v.get("name"), str)
    ]
    if "user_lang" not in var_names:
      issues.append({
          "code": "A007_FAIL_MISSING_VAR",
          "variable": "user_lang",
          "message": (
              "'user_lang' session variable is not declared in"
              " app.json.variableDeclarations."
          ),
      })

    return {
        "passed": len(issues) == 0,
        "declared_languages": sorted(list(all_declared_langs)),
        "issues": issues,
    }

  def audit_prohibited_xml_tags(self) -> Dict[str, Any]:
    """Scans all instruction files for prohibited internal platform XML tags."""
    issues: List[Dict[str, Any]] = []
    targets = self._find_instruction_targets()

    tags_joined = "|".join([re.escape(tag) for tag in PROHIBITED_XML_TAGS])
    pattern = re.compile(rf"<\s*/?\s*(?:{tags_joined})\b[^>]*>", re.IGNORECASE)

    for target in targets:
      content = target.get_content()
      if content is None:
        continue

      for line_num, line in enumerate(content.splitlines(), start=1):
        for match in pattern.finditer(line):
          tag_found = match.group(0)
          issues.append({
              "code": "PROHIBITED_XML_TAG_FOUND",
              "file": target.rel_path,
              "line": line_num,
              "tag": tag_found,
              "message": (
                  f"Prohibited platform XML tag '{tag_found}' found in"
                  f" {target.rel_path}:{line_num}. Custom/internal XML tags"
                  " trigger thought-leakage regex safety filters and cause"
                  " generic error fallbacks. Remove custom XML tags from"
                  " instructions."
              ),
          })

    return {
        "passed": len(issues) == 0,
        "files_scanned": len(targets),
        "issues": issues,
    }

  def audit_variable_setting_antipatterns(self) -> Dict[str, Any]:
    """Scans instruction files for text variable mutation anti-patterns."""
    issues: List[Dict[str, Any]] = []
    targets = self._find_instruction_targets()

    var_set_pattern = re.compile(
        r"(?i)\bset\s+[a-zA-Z0-9_]+\s*=", re.IGNORECASE
    )

    for target in targets:
      content = target.get_content()
      if content is None:
        continue

      for line_num, line in enumerate(content.splitlines(), start=1):
        has_negative_context = bool(
            re.search(
                r"(?i)\b(?:do not|don't|never|avoid|prohibited|anti-?pattern)\b",
                line,
            )
        )
        for match in var_set_pattern.finditer(line):
          if has_negative_context and _is_inside_quotes(
              line, match.start(), match.end()
          ):
            continue
          matched_text = match.group(0)
          issues.append({
              "code": "VARIABLE_SETTING_ANTIPATTERN",
              "file": target.rel_path,
              "line": line_num,
              "match": matched_text,
              "message": (
                  f"Text-based variable setting anti-pattern '{matched_text}'"
                  f" found in {target.rel_path}:{line_num}. State mutations"
                  " cannot occur via raw output text. Use tool calls (e.g."
                  " update_language) instead."
              ),
          })

    return {
        "passed": len(issues) == 0,
        "files_scanned": len(targets),
        "issues": issues,
    }

  def audit_inert_tags(self) -> Dict[str, Any]:
    """Scans all instruction files for 43+ inert emotion and pause tags."""
    issues: List[Dict[str, Any]] = []
    targets = self._find_instruction_targets()

    escaped_tags = [re.escape(f"[{tag}]") for tag in INERT_TAGS]
    pattern = re.compile(
        rf"{'|'.join(escaped_tags)}|\[prosody[^\]]*\]", re.IGNORECASE
    )

    sample_working = f"[{WORKING_TAGS[2]}], [{WORKING_TAGS[4]}], [{WORKING_TAGS[9]}]"
    for target in targets:
      content = target.get_content()
      if content is None:
        continue

      for line_num, line in enumerate(content.splitlines(), start=1):
        for match in pattern.finditer(line):
          tag_found = match.group(0)
          issues.append({
              "code": "INERT_TAG_FOUND",
              "file": target.rel_path,
              "line": line_num,
              "tag": tag_found,
              "message": (
                  f"Inert acoustic tag '{tag_found}' found in"
                  f" {target.rel_path}:{line_num}. This tag produces zero"
                  " acoustic effect and should be removed. Replace with a"
                  f" physical acoustic tag (e.g. {sample_working}) if"
                  " emphasis is needed."
              ),
          })

    return {
        "passed": len(issues) == 0,
        "files_scanned": len(targets),
        "issues": issues,
    }

  def audit_natural_speech_cues(self) -> Dict[str, Any]:
    """Audits prompt files for natural speech cues and prohibited closings."""
    issues: List[Dict[str, Any]] = []
    targets = self._find_instruction_targets()

    reflexive_closing_pattern = re.compile(
        r"(?:"
        r"is\s+there\s+anything\s+else\s+(?:i\s+can\s+(?:help|assist)"
        r"\s+you\s+with|you\s+need(?:\s+help\s+with)?)"
        r"|can\s+i\s+(?:help|assist)\s+you\s+with\s+anything\s+else"
        r"(?:\s+today)?"
        r"|anything\s+else\s+i\s+can\s+(?:help|assist)\s+you\s+with"
        r")",
        re.IGNORECASE,
    )

    for target in targets:
      content = target.get_content()
      if content is None:
        continue

      for line_num, line in enumerate(content.splitlines(), start=1):
        if reflexive_closing_pattern.search(line):
          issues.append({
              "code": "REFLEXIVE_CLOSING_LOOP",
              "file": target.rel_path,
              "line": line_num,
              "message": (
                  "Reflexive closing phrase found in"
                  f" {target.rel_path}:{line_num}. Replace with comprehension"
                  " confirmations or natural pauses."
              ),
          })

    return {
        "passed": len(issues) == 0,
        "issues": issues,
    }

  def audit_anti_looping_and_stability(
      self, app_data: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    """Audits anti-looping constraints, temperature, and <voice_lock> blocks."""
    if app_data is None:
      app_data = self._read_app_json()
    if not isinstance(app_data, dict):
      app_data = {}

    issues: List[Dict[str, Any]] = []

    # Check model temperature
    model_settings = app_data.get("modelSettings")
    if not isinstance(model_settings, dict):
      model_settings = {}
    temp = model_settings.get("temperature")
    if temp is None or "temperature" not in model_settings:
      issues.append({
          "code": "MISSING_SAMPLING_TEMPERATURE",
          "recommended": 1.0,
          "message": (
              "modelSettings.temperature is not configured in app.json. Set to"
              " 1.0 to avoid deterministic acoustic repetition deadlocks."
          ),
      })
    elif isinstance(temp, (int, float)) and temp < 0.9:
      issues.append({
          "code": "LOW_SAMPLING_TEMPERATURE",
          "found": temp,
          "recommended": 1.0,
          "message": (
              f"modelSettings.temperature is {temp}. Set to 1.0 to avoid"
              " deterministic acoustic repetition deadlocks."
          ),
      })

    # Check <voice_lock> in sub-agent instructions
    targets = self._find_instruction_targets()
    for target in targets:
      if (
          "root_agent" in pathlib.PurePath(target.rel_path).parts
          or target.file_path.name == "global_instruction.txt"
      ):
        continue
      content = target.get_content()
      if content is None:
        continue

      if "<voice_lock>" not in content:
        issues.append({
            "code": "MISSING_VOICE_LOCK",
            "file": target.rel_path,
            "message": (
                f"Sub-agent instruction in {target.rel_path} lacks a"
                " '<voice_lock>' directive to prevent user voice mimicking and"
                " voice drift."
            ),
        })

    return {
        "passed": len(issues) == 0,
        "issues": issues,
    }

  def audit(self) -> Dict[str, Any]:
    """Runs all 8 audit passes and returns a consolidated report."""
    app_data = self._read_app_json()

    audio_profile = self.audit_audio_profile(app_data)
    accents = self.audit_accent_specifications(app_data)
    rule_a007 = self.audit_rule_a007_multilang(app_data)
    prohibited_xml = self.audit_prohibited_xml_tags()
    var_antipatterns = self.audit_variable_setting_antipatterns()
    inert_tags = self.audit_inert_tags()
    speech_cues = self.audit_natural_speech_cues()
    anti_looping = self.audit_anti_looping_and_stability(app_data)

    passes = {
        "app_audio_profile": audio_profile,
        "accent_specifications": accents,
        "rule_a007_multilang": rule_a007,
        "prohibited_xml_tags": prohibited_xml,
        "variable_setting_antipatterns": var_antipatterns,
        "inert_tags": inert_tags,
        "natural_speech_cues": speech_cues,
        "anti_looping_and_stability": anti_looping,
    }

    total_issues = sum(len(p.get("issues", [])) for p in passes.values())

    return {
        "workspace": str(self.workspace_path),
        "app_json": (
            str(self.app_json_path) if self.app_json_path.exists() else None
        ),
        "overall_status": "PASSED" if total_issues == 0 else "FAILED",
        "total_issues": total_issues,
        "passes": passes,
    }

  # =========================================================================
  # REMEDIATION METHODS
  # =========================================================================

  def remediate_audio_profile_and_rule_a007(
      self, app_data: Optional[Dict[str, Any]] = None
  ) -> Tuple[Dict[str, Any], List[str]]:
    """Remediates synthesizeSpeechConfigs and Rule A007 in app.json."""
    if app_data is None:
      app_data = self._read_app_json()
    if not isinstance(app_data, dict):
      app_data = {}

    changes: List[str] = []

    # Ensure modelSettings
    if not isinstance(app_data.get("modelSettings"), dict):
      app_data["modelSettings"] = {}
      changes.append("Initialized modelSettings dictionary.")
    model_settings = app_data["modelSettings"]

    if model_settings.get("model") != "gemini-composite-v1":
      model_settings["model"] = "gemini-composite-v1"
      changes.append("Set modelSettings.model to 'gemini-composite-v1'.")
    if model_settings.get("temperature") != 1.0:
      model_settings["temperature"] = 1.0
      changes.append("Set modelSettings.temperature to 1.0.")

    # Determine languages
    if not isinstance(app_data.get("languageSettings"), dict):
      app_data["languageSettings"] = {}
      changes.append("Initialized languageSettings dictionary.")
    lang_settings = app_data["languageSettings"]

    default_lang = lang_settings.get("defaultLanguageCode")
    if not default_lang or not isinstance(default_lang, str):
      default_lang = "en-US"
    lang_settings["defaultLanguageCode"] = default_lang

    supported_langs = lang_settings.get("supportedLanguageCodes")
    if not isinstance(supported_langs, list):
      supported_langs = []
      lang_settings["supportedLanguageCodes"] = supported_langs

    valid_supported = [l for l in supported_langs if isinstance(l, str)]
    declared_langs = sorted(set([default_lang] + valid_supported))

    # Ensure synthesizeSpeechConfigs
    if not isinstance(app_data.get("audioProcessingConfig"), dict):
      app_data["audioProcessingConfig"] = {}
      changes.append("Initialized audioProcessingConfig dictionary.")
    audio_cfg = app_data["audioProcessingConfig"]

    if not isinstance(audio_cfg.get("synthesizeSpeechConfigs"), dict):
      audio_cfg["synthesizeSpeechConfigs"] = {}
      changes.append("Initialized synthesizeSpeechConfigs dictionary.")
    speech_cfgs = audio_cfg["synthesizeSpeechConfigs"]

    # 1. Inject missing configs for any declared languages not currently served
    for lang in declared_langs:
      match_res = _find_speech_config(speech_cfgs, lang)
      if not match_res:
        golden_note = generate_golden_directors_note(lang)
        default_voice = get_default_voice(lang)
        speech_cfgs[lang] = {
            "voice": default_voice,
            "speakingRate": 1.0,
            "instruction": golden_note,
        }
        changes.append(
            f"Injected golden synthesizeSpeechConfigs for language '{lang}'."
        )

    # 2. Repair existing entries in synthesizeSpeechConfigs in place (each entry processed once)
    for cfg_key in list(speech_cfgs.keys()):
      if not isinstance(cfg_key, str):
        continue
      cfg = speech_cfgs[cfg_key]
      if not isinstance(cfg, dict):
        cfg = {}
        speech_cfgs[cfg_key] = cfg

      golden_note = generate_golden_directors_note(cfg_key)
      default_voice = get_default_voice(cfg_key)
      target_accent = get_locale_accent(cfg_key)

      if not cfg.get("voice") or not isinstance(cfg.get("voice"), str):
        cfg["voice"] = default_voice
        changes.append(
            f"Set default voice '{default_voice}' for '{cfg_key}'."
        )
      if "speakingRate" not in cfg or not isinstance(
          cfg.get("speakingRate"), (int, float)
      ):
        cfg["speakingRate"] = 1.0
        changes.append(f"Set speakingRate 1.0 for '{cfg_key}'.")

      current_inst = cfg.get("instruction")
      if not isinstance(current_inst, str):
        current_inst = ""

      # Normalize any existing Accent directive to the correct localized
      # natural language accent derived from this config key.
      if re.search(
          r"\bAccent:\s*[^\n\r]+", current_inst, flags=re.IGNORECASE
      ):
        fixed_accent = re.sub(
            r"\bAccent:\s*[^\n\r]+",
            f"Accent: {target_accent}",
            current_inst,
            flags=re.IGNORECASE,
        )
        if fixed_accent != current_inst:
          cfg["instruction"] = fixed_accent
          changes.append(
              f"Normalized Accent directive to 'Accent: {target_accent}' for"
              f" '{cfg_key}'."
          )
          current_inst = fixed_accent

      # If instruction lacks Director's Note structure, inject golden template
      has_directors_note = bool(
          re.search(
              r"^#+\s*director'?s\s*notes?",
              current_inst,
              re.IGNORECASE | re.MULTILINE,
          )
      )
      has_audio_profile = bool(
          re.search(
              r"^#+\s*audio\s*profile",
              current_inst,
              re.IGNORECASE | re.MULTILINE,
          )
      )
      if not has_directors_note or not has_audio_profile:
        cfg["instruction"] = golden_note
        changes.append(
            f"Updated '{cfg_key}' instruction with complete Golden"
            " Director's Note."
        )
        current_inst = golden_note
      elif (
          "## Transcript:" not in current_inst
          and "### TRANSCRIPT:" not in current_inst
      ):
        # Cleanly repair malformed hook or append missing hook
        if re.search(
            r"(?i)\n*(?:#{1,3}\s*)?transcript\s*:?\s*$", current_inst
        ):
          fixed_inst = re.sub(
              r"(?i)\n*(?:#{1,3}\s*)?transcript\s*:?\s*$",
              "\n\n## Transcript:\n",
              current_inst,
          )
        else:
          fixed_inst = current_inst.rstrip() + "\n\n## Transcript:\n"
        cfg["instruction"] = fixed_inst
        changes.append(
            "Appended missing '## Transcript:' hook to"
            f" '{cfg_key}' instruction."
        )
        current_inst = fixed_inst

    # Ensure user_lang variable declaration
    if not isinstance(app_data.get("variableDeclarations"), list):
      app_data["variableDeclarations"] = []
      changes.append("Initialized variableDeclarations list.")
    var_decls = app_data["variableDeclarations"]

    var_names = [
        v.get("name")
        for v in var_decls
        if isinstance(v, dict) and isinstance(v.get("name"), str)
    ]
    if "user_lang" not in var_names:
      var_decls.append({
          "name": "user_lang",
          "description": "Active session language code (e.g. 'EN', 'ES')",
          "schema": {"type": "STRING", "default": default_lang[:2].upper()},
      })
      changes.append(
          "Added 'user_lang' session variable to app.json.variableDeclarations."
      )

    return app_data, changes

  def remediate_prohibited_xml_tags(self) -> List[str]:
    """Strips prohibited platform XML blocks and standalone tags."""
    changes: List[str] = []
    targets = self._find_instruction_targets()

    tags_joined = "|".join([re.escape(tag) for tag in PROHIBITED_XML_TAGS])
    # Match paired tag blocks (e.g. <state_update>...</state_update>)
    block_pattern = re.compile(
        rf"<\s*({tags_joined})\b[^>]*>.*?<\s*/\s*\1\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    # Match standalone opening or closing tags
    tag_pattern = re.compile(
        rf"<\s*/?\s*(?:{tags_joined})\b[^>]*>",
        re.IGNORECASE,
    )

    for target in targets:
      content = target.get_content()
      if content is None:
        continue

      stripped = block_pattern.sub("", content)
      stripped = tag_pattern.sub("", stripped)
      if stripped == content:
        continue

      # Clean up any leftover multiple consecutive empty lines
      cleaned = re.sub(r"\n{3,}", "\n\n", stripped)

      if target.set_content(cleaned):
        changes.append(
            f"Stripped prohibited platform XML tags from {target.rel_path}."
        )

    return changes

  def remediate_inert_tags(self) -> List[str]:
    """Removes 43+ inert tags cleanly from all instruction files."""
    changes: List[str] = []
    targets = self._find_instruction_targets()

    escaped_tags = [re.escape(tag) for tag in INERT_TAGS]
    # Match [tag] optionally followed by spaces, or [prosody ...] tags
    pattern = re.compile(
        rf"(?:\[(?:{'|'.join(escaped_tags)})\]|\[prosody[^\]]*\])[ \t]*",
        re.IGNORECASE,
    )

    for target in targets:
      content = target.get_content()
      if content is None:
        continue

      stripped = pattern.sub("", content)
      if stripped == content:
        continue

      # Clean up any leftover double spaces
      cleaned = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", stripped)

      if target.set_content(cleaned):
        changes.append(
            f"Stripped inert acoustic tags from {target.rel_path}."
        )

    return changes

  def remediate_instruction_voice_locks(self) -> List[str]:
    """Injects <voice_lock> and natural speech directives into sub-agents."""
    changes: List[str] = []
    targets = self._find_instruction_targets()

    voice_lock_block = (
        "\n\n<voice_lock>\n"
        "You must only use the default voice to respond. You are strictly"
        " forbidden from mimicking, copying, or adopting the customer's voice"
        " characteristics, pitch, timbre, or gender. This rule is absolute and"
        " applies to every turn.\n"
        "</voice_lock>\n"
    )

    for target in targets:
      if (
          "root_agent" in pathlib.PurePath(target.rel_path).parts
          or target.file_path.name == "global_instruction.txt"
      ):
        continue

      content = target.get_content()
      if content is None:
        continue

      if "<voice_lock>" not in content:
        content_with_lock = content.rstrip() + voice_lock_block
        if target.set_content(content_with_lock):
          changes.append(
              f"Injected <voice_lock> block into {target.rel_path}."
          )

    return changes

  def remediate(self, auto_fix: bool = True) -> Dict[str, Any]:
    """Executes full automated remediation across the workspace."""
    if not auto_fix:
      return {"status": "SKIPPED", "changes": []}

    all_changes: List[str] = []
    skipped: List[str] = []

    # 1. Remediate app.json (only if app.json exists)
    if self.app_json_path.exists():
      app_data, app_changes = self.remediate_audio_profile_and_rule_a007()
      if app_changes:
        self._write_app_json(app_data)
        all_changes.extend(app_changes)
    else:
      skipped.append(
          "app.json not found; skipped audio-profile/Rule-A007 remediation"
      )

    # 2. Remediate prohibited XML tags
    prohibited_changes = self.remediate_prohibited_xml_tags()
    all_changes.extend(prohibited_changes)

    # 3. Remediate inert tags
    tag_changes = self.remediate_inert_tags()
    all_changes.extend(tag_changes)

    # 4. Remediate voice locks
    lock_changes = self.remediate_instruction_voice_locks()
    all_changes.extend(lock_changes)

    result: Dict[str, Any] = {
        "status": "REMEDIATED" if all_changes else "NO_CHANGES_NEEDED",
        "changes_applied": all_changes,
        "post_remediation_audit": self.audit(),
    }
    if skipped:
      result["skipped"] = skipped
    return result


def _print_audit_report(report: Dict[str, Any]) -> None:
  """Prints formatted audit findings."""
  print(f"=== CXAS Voice Audit: {report['overall_status']} ===")
  print(f"Total Issues Found: {report['total_issues']}")
  for pass_name, pass_data in report.get("passes", {}).items():
    status = "PASS" if pass_data.get("passed") else "FAIL"
    print(f"\n[{status}] {pass_name}:")
    for issue in pass_data.get("issues", []):
      print(f"  * {issue.get('message')}")


def main() -> None:
  """CLI entry point for voice_auditor."""
  parser = argparse.ArgumentParser(
      description="CXAS Voice Configuration Auditor and Auto-Remediator."
  )
  parser.add_argument(
      "--workspace",
      type=str,
      default=".",
      help="Path to CXAS workspace directory.",
  )
  mode_group = parser.add_mutually_exclusive_group()
  mode_group.add_argument(
      "--audit-only",
      action="store_true",
      help="Perform audit without modifying files.",
  )
  mode_group.add_argument(
      "--remediate",
      action="store_true",
      help="Apply automated in-place remediation to local workspace files.",
  )
  parser.add_argument(
      "--json-output",
      action="store_true",
      help="Print output as JSON.",
  )

  args = parser.parse_args()
  auditor = CXASVoiceAuditor(pathlib.Path(args.workspace))

  if args.remediate:
    result = auditor.remediate(auto_fix=True)
    if result.get("skipped"):
      for msg in result["skipped"]:
        print(f"[WARNING] {msg}", file=sys.stderr)
    if args.json_output:
      print(json.dumps(result, indent=2))
    else:
      print(f"=== Remediation Status: {result['status']} ===")
      for change in result["changes_applied"]:
        print(f" - {change}")
      post_audit = result["post_remediation_audit"]
      print(
          "Post-remediation audit status:"
          f" {post_audit['overall_status']}"
      )
      if post_audit["overall_status"] != "PASSED":
        print("\n--- Remaining Unresolved Audit Issues ---")
        _print_audit_report(post_audit)
        print(
            "\nNote: variable_setting_antipatterns and natural_speech_cues"
            " findings require manual edits; re-run with --audit-only for"
            " details."
        )
    if result["post_remediation_audit"]["overall_status"] != "PASSED":
      sys.exit(1)
  else:
    report = auditor.audit()
    if args.json_output:
      print(json.dumps(report, indent=2))
    else:
      _print_audit_report(report)

    if report["overall_status"] != "PASSED":
      sys.exit(1)


if __name__ == "__main__":
  main()
