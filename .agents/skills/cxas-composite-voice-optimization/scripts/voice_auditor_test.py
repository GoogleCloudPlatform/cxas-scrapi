"""Unit tests for CXAS Voice Auditor and Remediator."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

# Ensure script dir is in sys.path for direct execution
_scripts_dir = pathlib.Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
  sys.path.insert(0, str(_scripts_dir))

import voice_auditor  # noqa: E402

CXASVoiceAuditor = voice_auditor.CXASVoiceAuditor
generate_golden_directors_note = voice_auditor.generate_golden_directors_note
get_locale_accent = voice_auditor.get_locale_accent
get_default_voice = voice_auditor.get_default_voice
LOCALE_TO_ACCENT = voice_auditor.LOCALE_TO_ACCENT
DEFAULT_VOICES = voice_auditor.DEFAULT_VOICES
PROHIBITED_XML_TAGS = voice_auditor.PROHIBITED_XML_TAGS


class CXASVoiceAuditorTest(unittest.TestCase):

  def setUp(self):
    super().setUp()
    self.test_dir = tempfile.TemporaryDirectory()
    self.workspace = pathlib.Path(self.test_dir.name)

  def tearDown(self):
    self.test_dir.cleanup()
    super().tearDown()

  def test_generate_golden_directors_note(self):
    en_note = generate_golden_directors_note("en-US")
    self.assertIn("# Audio Profile", en_note)
    self.assertIn("# Director's note", en_note)
    self.assertIn("Accent: American English", en_note)
    self.assertIn("## Transcript:\n", en_note)

    es_note = generate_golden_directors_note("es-US")
    self.assertIn("Accent: Spanish accent", es_note)
    self.assertIn('bridge words (like "eh," "ah," or "veamos")', es_note)

  def test_locale_to_accent_regional_dialects(self):
    """Verifies accent mapping and golden notes for regional dialects."""
    self.assertEqual(LOCALE_TO_ACCENT["en-IE"], "Contemporary Irish English")
    self.assertEqual(LOCALE_TO_ACCENT["es-419"], "Latin American Spanish")
    self.assertEqual(LOCALE_TO_ACCENT["es-MX"], "Latin American Spanish")
    self.assertEqual(LOCALE_TO_ACCENT["en-AU"], "Australian English")
    self.assertEqual(LOCALE_TO_ACCENT["en-GB"], "British English")
    self.assertEqual(LOCALE_TO_ACCENT["en-NZ"], "New Zealand English")
    self.assertEqual(LOCALE_TO_ACCENT["pt-PT"], "European Portuguese")
    self.assertEqual(LOCALE_TO_ACCENT["zh-CN"], "Mandarin Chinese")

    self.assertEqual(DEFAULT_VOICES["en-IE"], "en-IE-Chirp3-HD-Aoede")
    self.assertEqual(DEFAULT_VOICES["es-419"], "es-US-Chirp3-HD-Aoede")

    ie_note = generate_golden_directors_note("en-IE")
    self.assertIn("Accent: Contemporary Irish English", ie_note)
    self.assertIn('bridge words (like "um," "ah," or "well")', ie_note)

    latam_note = generate_golden_directors_note("es-419")
    self.assertIn("Accent: Latin American Spanish", latam_note)
    self.assertIn('bridge words (like "eh," "ah," or "veamos")', latam_note)

    au_note = generate_golden_directors_note("en-AU")
    self.assertIn("Accent: Australian English", au_note)
    self.assertIn('bridge words (like "um," "ah," or "yeah")', au_note)

    gb_note = generate_golden_directors_note("en-GB")
    self.assertIn("Accent: British English", gb_note)
    self.assertIn('bridge words (like "um," "er," or "ah")', gb_note)

  def test_audit_missing_app_json(self):
    auditor = CXASVoiceAuditor(self.workspace)
    report = auditor.audit()
    self.assertEqual(report["overall_status"], "FAILED")
    self.assertGreater(report["total_issues"], 0)

  def test_audit_audio_profile_detection(self):
    app_data = {
        "displayName": "Test App",
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": "Just read this transcript.",
                }
            }
        },
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_audio_profile(app_data)
    self.assertFalse(result["passed"])
    issue_codes = [issue["code"] for issue in result["issues"]]
    self.assertIn("MISSING_AUDIO_PROFILE_HEADER", issue_codes)
    self.assertIn("MISSING_DIRECTORS_NOTE_HEADER", issue_codes)
    self.assertIn("MISSING_TRANSCRIPT_HOOK", issue_codes)

  def test_audit_accent_specifications_locale_code_flagged(self):
    app_data = {
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": (
                        "# Audio Profile\nTest\n# Director's note\n* Accent:"
                        " en-US\n## Transcript:\n"
                    ),
                }
            }
        }
    }
    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_accent_specifications(app_data)
    self.assertFalse(result["passed"])
    self.assertEqual(result["issues"][0]["code"], "LOCALE_CODE_IN_ACCENT")
    self.assertEqual(result["issues"][0]["invalid_code"], "en-US")
    self.assertEqual(
        result["issues"][0]["recommended"], "Accent: American English"
    )

  def test_audit_rule_a007_multilang(self):
    app_data = {
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": ["es-US", "fr-FR"],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": generate_golden_directors_note("en-US"),
                }
            }
        },
        "variableDeclarations": [],
    }
    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_rule_a007_multilang(app_data)
    self.assertFalse(result["passed"])
    issue_codes = [issue["code"] for issue in result["issues"]]
    self.assertIn("A007_FAIL_MISSING_LANG", issue_codes)
    self.assertIn("A007_FAIL_MISSING_VAR", issue_codes)

  def test_audit_rule_a007_case_insensitive_and_root_fallback(self):
    """Verifies that case-insensitive keys and root language fallback pass Rule A007."""
    app_data = {
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": ["es-419"],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-us": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": generate_golden_directors_note("en-US"),
                },
                "es": {
                    "voice": "es-US-Chirp3-HD-Aoede",
                    "instruction": generate_golden_directors_note("es-419"),
                },
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_rule_a007_multilang(app_data)
    self.assertTrue(result["passed"], f"Unexpected issues: {result['issues']}")
    self.assertEqual(len(result["issues"]), 0)

  def test_audit_prohibited_xml_tags_detection(self):
    """Verifies prohibited XML tags are detected."""
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    instruction_file = agents_dir / "instruction.txt"
    instruction_file.write_text(
        "You are billing support.\n"
        "<state_update>user_status = active</state_update>\n"
        "<context>user has overdue bill</context>\n"
        "<reasoning>check balance first</reasoning>\n"
        "<thought>verify account</thought>\n"
        "<internal>system log</internal>\n",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_prohibited_xml_tags()
    self.assertFalse(result["passed"])
    self.assertEqual(result["files_scanned"], 1)
    tags_found = [issue["tag"] for issue in result["issues"]]
    self.assertTrue(any("state_update" in t for t in tags_found))
    self.assertTrue(any("context" in t for t in tags_found))
    self.assertTrue(any("reasoning" in t for t in tags_found))
    self.assertTrue(any("thought" in t for t in tags_found))
    self.assertTrue(any("internal" in t for t in tags_found))

  def test_audit_variable_setting_antipatterns_detection(self):
    """Verifies that text-based variable setting directives are flagged."""
    agents_dir = self.workspace / "agents" / "root_agent"
    agents_dir.mkdir(parents=True)
    instruction_file = agents_dir / "instruction.txt"
    instruction_file.write_text(
        "If Spanish detected, Set user_lang = ES and set language = es.\n"
        "Set is_vip = true when authenticated.\n",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_variable_setting_antipatterns()
    self.assertFalse(result["passed"])
    self.assertEqual(result["files_scanned"], 1)
    issue_codes = [issue["code"] for issue in result["issues"]]
    self.assertIn("VARIABLE_SETTING_ANTIPATTERN", issue_codes)
    self.assertGreaterEqual(len(result["issues"]), 2)

  def test_remediate_prohibited_xml_tags(self):
    """Verifies stripping of prohibited XML tag blocks."""
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    instruction_file = agents_dir / "instruction.txt"
    original_text = (
        "<role>Billing Agent</role>\n<state_update>\nuser_state ="
        " active\nbalance = 50\n</state_update>\nPlease confirm your card"
        " ending in <voice_output>4 8 2 1</voice_output>.\n<context>Account in"
        " good standing</context>\n<thought>Lookup finished</thought>\n"
    )
    instruction_file.write_text(original_text, encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    changes = auditor.remediate_prohibited_xml_tags()
    self.assertTrue(len(changes) > 0)

    cleaned_text = instruction_file.read_text(encoding="utf-8")
    self.assertNotIn("state_update", cleaned_text)
    self.assertNotIn("user_state = active", cleaned_text)
    self.assertNotIn("Account in good standing", cleaned_text)
    self.assertNotIn("Lookup finished", cleaned_text)
    self.assertIn("<role>Billing Agent</role>", cleaned_text)
    self.assertIn("<voice_output>4 8 2 1</voice_output>", cleaned_text)

  def test_audit_inert_tags_detection(self):
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    instruction_file = agents_dir / "instruction.txt"
    instruction_file.write_text(
        "You are a helpful agent. [empathetic] I understand your problem."
        " [warm] Let me check [short pause] your bill.",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_inert_tags()
    self.assertFalse(result["passed"])
    self.assertEqual(result["files_scanned"], 1)
    tags_found = [issue["tag"] for issue in result["issues"]]
    self.assertIn("[empathetic]", tags_found)
    self.assertIn("[warm]", tags_found)
    self.assertIn("[short pause]", tags_found)

  def test_audit_natural_speech_cues(self):
    agents_dir = self.workspace / "agents" / "root_agent"
    agents_dir.mkdir(parents=True)
    instruction_file = agents_dir / "instruction.txt"
    instruction_file.write_text(
        "At the end of every response ask: Is there anything else I can help"
        " you with today?",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_natural_speech_cues()
    self.assertFalse(result["passed"])
    self.assertEqual(result["issues"][0]["code"], "REFLEXIVE_CLOSING_LOOP")

  def test_audit_anti_looping_and_voice_lock(self):
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    instruction_file = agents_dir / "instruction.txt"
    instruction_file.write_text(
        "You are the billing agent.",
        encoding="utf-8",
    )
    app_data = {
        "modelSettings": {"temperature": 0.5},
    }

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_anti_looping_and_stability(app_data)
    self.assertFalse(result["passed"])
    issue_codes = [issue["code"] for issue in result["issues"]]
    self.assertIn("LOW_SAMPLING_TEMPERATURE", issue_codes)
    self.assertIn("MISSING_VOICE_LOCK", issue_codes)

  def test_standard_scrapi_workspace_metadata_agent_json_and_instruction_txt(
      self,
  ):
    """Verifies that SCRAPI agent.json metadata alongside instruction.txt does not produce false positives."""
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    # Metadata-only agent.json (no instruction field)
    agent_json = agents_dir / "agent.json"
    agent_json.write_text(
        json.dumps({
            "name": "billing_agent",
            "displayName": "Billing Support",
            "description": "Handles invoices",
        }),
        encoding="utf-8",
    )
    # Separate instruction.txt
    instruction_txt = agents_dir / "instruction.txt"
    instruction_txt.write_text(
        "You are billing support. Help the customer.",
        encoding="utf-8",
    )

    app_data = {
        "displayName": "SCRAPI Workspace App",
        "modelSettings": {"model": "gemini-composite-v1", "temperature": 1.0},
        "languageSettings": {"defaultLanguageCode": "en-US"},
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": generate_golden_directors_note("en-US"),
                }
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    app_json = self.workspace / "app.json"
    app_json.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    rem_res = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_res["status"], "REMEDIATED")

    post_audit = auditor.audit()
    self.assertEqual(post_audit["overall_status"], "PASSED")
    self.assertEqual(post_audit["total_issues"], 0)

    # Verify instruction.txt got <voice_lock>, while agent.json remained intact
    self.assertIn("<voice_lock>", instruction_txt.read_text(encoding="utf-8"))
    agent_meta = json.loads(agent_json.read_text(encoding="utf-8"))
    self.assertNotIn("instruction", agent_meta)

  def test_end_to_end_remediation(self):
    # Setup imperfect CXAS workspace
    app_data = {
        "displayName": "Imperfect Agent",
        "modelSettings": {"model": "gemini-1.5-flash", "temperature": 0.4},
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": ["es-US"],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": "* Accent: en-US\nUnstructured style",
                }
            }
        },
        "variableDeclarations": [],
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    subagent_dir = self.workspace / "agents" / "claims_agent"
    subagent_dir.mkdir(parents=True)
    subagent_inst = subagent_dir / "instruction.txt"
    subagent_inst.write_text(
        "<role>Claims Agent</role>\n"
        "<state_update>status = pending</state_update>\n"
        "[empathetic] I am sorry. [calm] Let me assist.",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    initial_audit = auditor.audit()
    self.assertEqual(initial_audit["overall_status"], "FAILED")

    # Run remediation
    rem_result = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_result["status"], "REMEDIATED")
    self.assertTrue(len(rem_result["changes_applied"]) > 0)

    # Post-remediation audit must pass
    post_audit = auditor.audit()
    self.assertEqual(post_audit["overall_status"], "PASSED")
    self.assertEqual(post_audit["total_issues"], 0)

    # Verify remediated app.json content
    updated_app_data = json.loads(app_json_path.read_text(encoding="utf-8"))
    self.assertEqual(
        updated_app_data["modelSettings"]["model"], "gemini-composite-v1"
    )
    self.assertEqual(updated_app_data["modelSettings"]["temperature"], 1.0)
    speech_cfgs = updated_app_data["audioProcessingConfig"][
        "synthesizeSpeechConfigs"
    ]
    self.assertIn("en-US", speech_cfgs)
    self.assertIn("es-US", speech_cfgs)
    self.assertIn(
        "Accent: American English", speech_cfgs["en-US"]["instruction"]
    )
    self.assertIn("Accent: Spanish accent", speech_cfgs["es-US"]["instruction"])
    var_names = [v["name"] for v in updated_app_data["variableDeclarations"]]
    self.assertIn("user_lang", var_names)

    # Verify instruction file cleaned, prohibited XML stripped, and has
    # <voice_lock>.
    cleaned_inst = subagent_inst.read_text(encoding="utf-8")
    self.assertNotIn("[empathetic]", cleaned_inst)
    self.assertNotIn("[calm]", cleaned_inst)
    self.assertNotIn("state_update", cleaned_inst)
    self.assertIn("<voice_lock>", cleaned_inst)

  def test_audit_rule_a007_multilang_all_locales_accent_mismatch(self):
    """Verifies non-English locales with mismatched accents are detected."""
    test_locales = [
        ("es-US", "American English", "Spanish accent"),
        ("es-419", "American English", "Latin American Spanish"),
        ("es-ES", "American English", "Castilian Spanish"),
        ("es-MX", "American English", "Latin American Spanish"),
        ("en-IE", "American English", "Contemporary Irish English"),
        ("en-AU", "American English", "Australian English"),
        ("en-NZ", "American English", "New Zealand English"),
        ("fr-FR", "American English", "Metropolitan French"),
        ("fr-CA", "American English", "French Canadian"),
        ("de-DE", "American English", "German"),
        ("ja-JP", "American English", "Japanese"),
        ("it-IT", "American English", "Italian"),
        ("pt-BR", "American English", "Brazilian Portuguese"),
    ]
    for locale, wrong_accent, expected_accent in test_locales:
      app_data = {
          "languageSettings": {
              "defaultLanguageCode": "en-US",
              "supportedLanguageCodes": [locale],
          },
          "audioProcessingConfig": {
              "synthesizeSpeechConfigs": {
                  "en-US": {
                      "voice": "en-US-Chirp3-HD-Aoede",
                      "instruction": generate_golden_directors_note("en-US"),
                  },
                  locale: {
                      "voice": f"{locale}-Chirp3-HD-Aoede",
                      "instruction": (
                          "# Audio Profile\nTest\n# Director's note\n*"
                          f" Accent: {wrong_accent}\n## Transcript:\n"
                      ),
                  },
              }
          },
          "variableDeclarations": [{"name": "user_lang"}],
      }
      auditor = CXASVoiceAuditor(self.workspace)
      result = auditor.audit_rule_a007_multilang(app_data)
      self.assertFalse(
          result["passed"], f"Failed to flag mismatch for {locale}"
      )
      mismatch_issues = [
          i
          for i in result["issues"]
          if i["code"] == "A007_FAIL_ACCENT_MISMATCH"
      ]
      self.assertEqual(len(mismatch_issues), 1)
      self.assertEqual(mismatch_issues[0]["locale"], locale)
      self.assertEqual(mismatch_issues[0]["found"], wrong_accent)
      self.assertEqual(mismatch_issues[0]["expected"], expected_accent)

  def test_audit_and_remediate_multilang_accent_contradictions(self):
    """Tests multi-language coverage across multiple locales."""
    app_data = {
        "displayName": "Global Multilingual App",
        "modelSettings": {"model": "gemini-composite-v1", "temperature": 1.0},
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": [
                "fr-FR",
                "de-DE",
                "es-US",
                "ja-JP",
                "es-MX",
            ],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": generate_golden_directors_note("en-US"),
                },
                "fr-FR": {
                    "voice": "fr-FR-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": generate_golden_directors_note("en-US"),
                },
                "de-DE": {
                    "voice": "de-DE-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": generate_golden_directors_note("en-US"),
                },
                "es-US": {
                    "voice": "es-US-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": generate_golden_directors_note("en-US"),
                },
                "ja-JP": {
                    "voice": "ja-JP-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": (
                        "# Audio Profile\nCustom\n# Director's note\n* Accent:"
                        " ja-JP\n## Transcript:\n"
                    ),
                },
                "es-MX": {
                    "voice": "es-US-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": generate_golden_directors_note("en-US"),
                },
            }
        },
        "variableDeclarations": [
            {"name": "user_lang", "schema": {"type": "STRING"}}
        ],
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    report = auditor.audit()
    self.assertEqual(report["overall_status"], "FAILED")

    a007_issues = report["passes"]["rule_a007_multilang"]["issues"]
    mismatch_locales = [
        i["locale"]
        for i in a007_issues
        if i["code"] == "A007_FAIL_ACCENT_MISMATCH"
    ]
    self.assertIn("fr-FR", mismatch_locales)
    self.assertIn("de-DE", mismatch_locales)
    self.assertIn("es-US", mismatch_locales)
    self.assertIn("es-MX", mismatch_locales)

    accent_issues = report["passes"]["accent_specifications"]["issues"]
    self.assertTrue(any(i["invalid_code"] == "ja-JP" for i in accent_issues))

    rem_result = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_result["status"], "REMEDIATED")

    post_audit = auditor.audit()
    self.assertEqual(
        post_audit["overall_status"],
        "PASSED",
        f"Issues remain: {post_audit['passes']}",
    )

    updated_json = json.loads(app_json_path.read_text(encoding="utf-8"))
    speech_cfgs = updated_json["audioProcessingConfig"][
        "synthesizeSpeechConfigs"
    ]
    self.assertIn(
        "Accent: Metropolitan French", speech_cfgs["fr-FR"]["instruction"]
    )
    self.assertIn("Accent: German", speech_cfgs["de-DE"]["instruction"])
    self.assertIn("Accent: Spanish accent", speech_cfgs["es-US"]["instruction"])
    self.assertIn(
        "Accent: Latin American Spanish", speech_cfgs["es-MX"]["instruction"]
    )
    self.assertIn("Accent: Japanese", speech_cfgs["ja-JP"]["instruction"])

  def test_remediate_missing_transcript_hook(self):
    """Tests missing '## Transcript:' hook is appended."""
    app_data = {
        "displayName": "Missing Hook App",
        "modelSettings": {"model": "gemini-composite-v1", "temperature": 1.0},
        "languageSettings": {"defaultLanguageCode": "en-US"},
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": (
                        "# Audio Profile\nYou are warm and patient.\n#"
                        " Director's note\n* Persona: Friendly\n* Accent:"
                        " American English"
                    ),
                }
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    report = auditor.audit_audio_profile(app_data)
    self.assertFalse(report["passed"])
    self.assertIn(
        "MISSING_TRANSCRIPT_HOOK", [i["code"] for i in report["issues"]]
    )

    rem_result = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_result["status"], "REMEDIATED")

    post_report = auditor.audit_audio_profile()
    self.assertTrue(post_report["passed"])
    self.assertEqual(len(post_report["issues"]), 0)

    updated_json = json.loads(app_json_path.read_text(encoding="utf-8"))
    inst = updated_json["audioProcessingConfig"]["synthesizeSpeechConfigs"][
        "en-US"
    ]["instruction"]
    self.assertIn("You are warm and patient.", inst)
    self.assertTrue(inst.endswith("## Transcript:\n"))

  def test_remediate_malformed_transcript_hook_variants(self):
    """Verifies remediation of malformed hook variants."""
    malformed_variants = [
        (
            "en-US",
            (
                "# Audio Profile\nProfile\n# Director's note\n* Accent:"
                " American English\n\n## Transcript"
            ),
        ),
        (
            "es-US",
            (
                "# Audio Profile\nProfile\n# Director's note\n* Accent: Spanish"
                " accent\n\nTranscript:"
            ),
        ),
        (
            "fr-FR",
            (
                "# Audio Profile\nProfile\n# Director's note\n* Accent:"
                " Metropolitan French\n\n## TRANSCRIPT:"
            ),
        ),
    ]
    app_data = {
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": ["es-US", "fr-FR"],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                locale: {
                    "voice": f"{locale}-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": inst,
                }
                for locale, inst in malformed_variants
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    rem_result = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_result["status"], "REMEDIATED")
    self.assertEqual(
        rem_result["post_remediation_audit"]["overall_status"], "PASSED"
    )

    remediated_app = json.loads(app_json_path.read_text(encoding="utf-8"))
    speech_cfgs = remediated_app["audioProcessingConfig"][
        "synthesizeSpeechConfigs"
    ]
    for locale in ["en-US", "es-US", "fr-FR"]:
      inst = speech_cfgs[locale]["instruction"]
      self.assertTrue(
          inst.endswith("## Transcript:\n"),
          f"Locale '{locale}' did not conclude with '## Transcript:\\n'."
          f" Found: {repr(inst[-25:])}",
      )

  def test_audit_and_remediate_null_fields_in_app_json(self):
    """Tests that explicit null JSON fields do not cause crashes."""
    app_data = {
        "displayName": "Null Fields Agent",
        "audioProcessingConfig": None,
        "modelSettings": None,
        "languageSettings": None,
        "variableDeclarations": None,
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    report = auditor.audit()
    self.assertEqual(report["overall_status"], "FAILED")
    self.assertGreater(report["total_issues"], 0)

    audio_issues = [
        i["code"] for i in report["passes"]["app_audio_profile"]["issues"]
    ]
    self.assertIn("MISSING_SYNTHESIZE_SPEECH_CONFIGS", audio_issues)
    a007_issues = [
        i["code"] for i in report["passes"]["rule_a007_multilang"]["issues"]
    ]
    self.assertIn("A007_FAIL_MISSING_VAR", a007_issues)

    rem_result = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_result["status"], "REMEDIATED")

    post_audit = auditor.audit()
    self.assertEqual(post_audit["overall_status"], "PASSED")
    self.assertEqual(post_audit["total_issues"], 0)

    remediated_json = json.loads(app_json_path.read_text(encoding="utf-8"))
    self.assertIsInstance(remediated_json["modelSettings"], dict)
    self.assertEqual(
        remediated_json["modelSettings"]["model"], "gemini-composite-v1"
    )
    self.assertEqual(remediated_json["modelSettings"]["temperature"], 1.0)
    self.assertIsInstance(remediated_json["languageSettings"], dict)
    self.assertEqual(
        remediated_json["languageSettings"]["defaultLanguageCode"], "en-US"
    )
    self.assertIsInstance(
        remediated_json["audioProcessingConfig"]["synthesizeSpeechConfigs"],
        dict,
    )
    self.assertIn(
        "en-US",
        remediated_json["audioProcessingConfig"]["synthesizeSpeechConfigs"],
    )
    self.assertIsInstance(remediated_json["variableDeclarations"], list)
    self.assertIn(
        "user_lang",
        [v["name"] for v in remediated_json["variableDeclarations"]],
    )

  def test_remediation_strict_idempotency(self):
    """Tests that running remediation multiple times is idempotent."""
    app_data = {
        "displayName": "Idempotent App",
        "modelSettings": {"model": "gemini-1.5-flash", "temperature": 0.5},
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": ["es-US"],
        },
        "audioProcessingConfig": None,
        "variableDeclarations": None,
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    sub_dir = self.workspace / "agents" / "sales_agent"
    sub_dir.mkdir(parents=True)
    sub_file = sub_dir / "instruction.txt"
    sub_file.write_text(
        "<state_update>state = init</state_update>\n"
        "[empathetic] Help the customer with sales. [warm]",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)

    rem1 = auditor.remediate(auto_fix=True)
    self.assertEqual(rem1["status"], "REMEDIATED")
    self.assertTrue(len(rem1["changes_applied"]) > 0)

    snapshot_app_json = app_json_path.read_text(encoding="utf-8")
    snapshot_sub_inst = sub_file.read_text(encoding="utf-8")

    rem2 = auditor.remediate(auto_fix=True)
    self.assertEqual(rem2["status"], "NO_CHANGES_NEEDED")
    self.assertEqual(len(rem2["changes_applied"]), 0)

    self.assertEqual(
        app_json_path.read_text(encoding="utf-8"), snapshot_app_json
    )
    self.assertEqual(sub_file.read_text(encoding="utf-8"), snapshot_sub_inst)
    self.assertEqual(snapshot_sub_inst.count("<voice_lock>"), 1)
    self.assertNotIn("state_update", snapshot_sub_inst)

    final_audit = auditor.audit()
    self.assertEqual(final_audit["overall_status"], "PASSED")
    self.assertEqual(final_audit["total_issues"], 0)

  def test_audit_audio_profile_case_insensitive_headers(self):
    """Verifies that casing variations in headers pass audit."""
    app_data = {
        "displayName": "Casing Test App",
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": (
                        "# Audio profile\nCustom warm human profile.\n\n"
                        "# Director's Note\n* Persona & Tone: Friendly\n"
                        "* Accent: American English\n\n## Transcript:\n"
                    ),
                }
            }
        },
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_audio_profile(app_data)
    self.assertTrue(result["passed"])
    self.assertEqual(len(result["issues"]), 0)

  def test_locale_normalization_helpers(self):
    """Verifies that helper functions handle variants and base codes."""
    self.assertEqual(get_locale_accent("de"), "German")
    self.assertEqual(get_locale_accent("ja"), "Japanese")
    self.assertEqual(get_locale_accent("es"), "Spanish accent")
    self.assertEqual(get_locale_accent("fr"), "Metropolitan French")
    self.assertEqual(get_locale_accent("pt"), "Brazilian Portuguese")
    self.assertEqual(get_locale_accent("it"), "Italian")
    self.assertEqual(get_locale_accent("en"), "American English")

    self.assertEqual(get_locale_accent("de-AT"), "German")
    self.assertEqual(get_locale_accent("fr-BE"), "Metropolitan French")
    self.assertEqual(get_locale_accent("es-MX"), "Latin American Spanish")
    self.assertEqual(get_locale_accent("en-NZ"), "New Zealand English")
    self.assertEqual(get_locale_accent("pt-PT"), "European Portuguese")
    self.assertEqual(get_locale_accent("zh-CN"), "Mandarin Chinese")

    self.assertEqual(get_locale_accent("en-us"), "American English")
    self.assertEqual(get_locale_accent("EN_US"), "American English")
    self.assertEqual(get_locale_accent("pt-br"), "Brazilian Portuguese")
    self.assertEqual(get_locale_accent("PT_BR"), "Brazilian Portuguese")
    self.assertEqual(get_locale_accent("it-it"), "Italian")
    self.assertEqual(get_locale_accent("IT_IT"), "Italian")
    self.assertEqual(get_locale_accent("es_419"), "Latin American Spanish")

    self.assertEqual(get_default_voice("de"), "de-DE-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("ja"), "ja-JP-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("en_us"), "en-US-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("pt_BR"), "pt-BR-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("it-it"), "it-IT-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("es-MX"), "es-US-Chirp3-HD-Aoede")

  def test_broadened_reflexive_closing_phrases(self):
    """Verifies that broadened reflexive closing variations are detected."""
    sub_dir = self.workspace / "agents" / "support_agent"
    sub_dir.mkdir(parents=True)
    inst_file = sub_dir / "instruction.txt"
    inst_file.write_text(
        "Always say: Can I help you with anything else today?\n"
        "And also: Is there anything else I can assist you with?",
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_natural_speech_cues()
    self.assertFalse(result["passed"])
    self.assertEqual(len(result["issues"]), 2)
    self.assertEqual(result["issues"][0]["code"], "REFLEXIVE_CLOSING_LOOP")
    self.assertEqual(result["issues"][1]["code"], "REFLEXIVE_CLOSING_LOOP")

  def test_remediation_preserves_custom_directors_note_case_insensitive(self):
    """Verifies remediation preserves custom Director's Notes."""
    custom_instruction = (
        "# Audio Profile\nCustom persona instructions.\n\n"
        "# Director's Note\n* Persona & Tone: Specialized concierge\n* Accent:"
        " American English\n\n## Transcript:\n"
    )
    app_data = {
        "displayName": "Custom Note App",
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": [],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": custom_instruction,
                }
            }
        },
        "variableDeclarations": [],
    }
    app_json_path = self.workspace / "app.json"
    app_json_path.write_text(json.dumps(app_data, indent=2), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    _ = auditor.remediate(auto_fix=True)
    updated_app = json.loads(app_json_path.read_text(encoding="utf-8"))
    speech_cfgs = updated_app["audioProcessingConfig"][
        "synthesizeSpeechConfigs"
    ]
    updated_inst = speech_cfgs["en-US"]["instruction"]

    self.assertIn("Specialized concierge", updated_inst)
    self.assertEqual(updated_inst, custom_instruction)

  def test_audit_and_remediation_with_embedded_agent_json(self):
    """Verifies that agent.json embedded instructions are audited and remediated."""
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    agent_json_file = agents_dir / "agent.json"
    agent_data = {
        "name": "billing_agent",
        "displayName": "Billing Support",
        "instruction": (
            "You are billing support. [empathetic] I understand your problem."
            " <thought>Look up invoice</thought> Set user_lang = ES."
        ),
    }
    agent_json_file.write_text(
        json.dumps(agent_data, indent=2), encoding="utf-8"
    )

    auditor = CXASVoiceAuditor(self.workspace)
    prohibited_audit = auditor.audit_prohibited_xml_tags()
    self.assertFalse(prohibited_audit["passed"])
    self.assertGreaterEqual(len(prohibited_audit["issues"]), 1)

    inert_audit = auditor.audit_inert_tags()
    self.assertFalse(inert_audit["passed"])
    self.assertGreaterEqual(len(inert_audit["issues"]), 1)

    remediation_res = auditor.remediate(auto_fix=True)
    self.assertEqual(remediation_res["status"], "REMEDIATED")

    updated_agent = json.loads(agent_json_file.read_text(encoding="utf-8"))
    updated_instruction = updated_agent["instruction"]
    self.assertNotIn("<thought>", updated_instruction)
    self.assertNotIn("[empathetic]", updated_instruction)
    self.assertIn("<voice_lock>", updated_instruction)
    self.assertFalse((self.workspace / "app.json").exists())

  def test_remediation_does_not_rewrite_violation_free_files_or_destroy_indentation(
      self,
  ):
    """Verifies that violation-free files retain formatting and indentation."""
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    inst_file = agents_dir / "instruction.txt"
    original = (
        "Steps:\n- Greet\n    - Verify identity\n\n\n\nDone.\n<voice_lock>x</voice_lock>\n"
    )
    inst_file.write_text(original, encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    inert_changes = auditor.remediate_inert_tags()
    xml_changes = auditor.remediate_prohibited_xml_tags()
    self.assertEqual(inert_changes, [])
    self.assertEqual(xml_changes, [])
    self.assertEqual(inst_file.read_text(encoding="utf-8"), original)

    # Full remediate() also leaves it untouched
    res = auditor.remediate(auto_fix=True)
    self.assertNotIn("billing_agent", str(res["changes_applied"]))
    self.assertEqual(inst_file.read_text(encoding="utf-8"), original)

    # File with [empathetic] still gets stripped
    inst_file.write_text("Hello [empathetic] world", encoding="utf-8")
    stripped_changes = auditor.remediate_inert_tags()
    self.assertGreater(len(stripped_changes), 0)
    self.assertEqual(inst_file.read_text(encoding="utf-8"), "Hello world")

  def test_hidden_directory_workspace_scanning(self):
    """Verifies that workspaces inside dot-directories are properly scanned."""
    dot_base = self.workspace / ".workspaces" / "nested_app"
    agent_dir = dot_base / "agents" / "billing_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.txt").write_text(
        "<state_update>x=1</state_update> [empathetic] hi", encoding="utf-8"
    )

    # Subdirectory with .git inside workspace is skipped
    git_dir = dot_base / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (git_dir / "instruction.txt").write_text(
        "<state_update>bad</state_update>", encoding="utf-8"
    )

    auditor = CXASVoiceAuditor(dot_base)
    r = auditor.audit_prohibited_xml_tags()
    self.assertEqual(r["files_scanned"], 1)
    self.assertFalse(r["passed"])
    self.assertEqual(len(r["issues"]), 2)

  def test_language_detection_doc_block_and_antipattern_negative_examples(
      self,
  ):
    """Verifies variable setting audit handles doc blocks, valid exclusions, and real antipatterns."""
    agents_dir = self.workspace / "agents" / "root_agent"
    agents_dir.mkdir(parents=True)
    inst_file = agents_dir / "instruction.txt"

    # Updated doc block verbatim passes
    updated_doc_block = (
        "<language_detection>\n"
        "- You must respond to the customer in the language specified by"
        " {{user_lang}}.\n"
        "- You may ONLY trigger a language switch if the customer explicitly"
        ' requests it (e.g., "Can we speak in Spanish?", "Habla en espanol por'
        " favor\").\n"
        "- If the customer speaks an isolated phrase or sentence in another"
        " language without an explicit request to change languages, continue"
        " responding in {{user_lang}}.\n"
        "- When an explicit switch is detected, invoke the update_language tool"
        ' with the new target language code (e.g., "ES") and confirm the'
        " switch in the new language.\n"
        "- DO NOT emit text-based variable-setting directives that assign"
        " user_lang in plain text; state updates must occur solely via tool"
        " execution.\n"
        "</language_detection>\n"
    )
    inst_file.write_text(updated_doc_block, encoding="utf-8")
    app_data = {
        "languageSettings": {"defaultLanguageCode": "en-US"},
        "modelSettings": {"temperature": 1.0, "model": "gemini-composite-v1"},
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": generate_golden_directors_note("en-US"),
                }
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    (self.workspace / "app.json").write_text(
        json.dumps(app_data), encoding="utf-8"
    )

    auditor = CXASVoiceAuditor(self.workspace)
    var_audit = auditor.audit_variable_setting_antipatterns()
    self.assertTrue(var_audit["passed"])
    rem_res = auditor.remediate(auto_fix=True)
    self.assertEqual(
        rem_res["post_remediation_audit"]["overall_status"], "PASSED"
    )

    # Old-style doc block with negative example in quotes is also not flagged
    old_style_block = (
        "- DO NOT emit text-based variable setting directives (e.g. \"Set"
        ' user_lang = ES"); state updates must occur solely via tool'
        " execution.\n"
    )
    inst_file.write_text(old_style_block, encoding="utf-8")
    self.assertTrue(auditor.audit_variable_setting_antipatterns()["passed"])

    # Real anti-pattern is still flagged
    bad_line = "If Spanish detected, Set user_lang = ES.\n"
    inst_file.write_text(bad_line, encoding="utf-8")
    bad_audit = auditor.audit_variable_setting_antipatterns()
    self.assertFalse(bad_audit["passed"])
    self.assertEqual(len(bad_audit["issues"]), 1)

  def test_shared_root_language_config_idempotency_and_no_leak(self):
    """Verifies shared root voice configs converge without language leak or oscillation."""
    app = {
        "languageSettings": {
            "defaultLanguageCode": "es-419",
            "supportedLanguageCodes": ["es-ES"],
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "es": {
                    "voice": "es-US-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": generate_golden_directors_note("es-419"),
                }
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    (self.workspace / "app.json").write_text(json.dumps(app), encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    r1 = auditor.remediate(auto_fix=True)
    self.assertEqual(r1["post_remediation_audit"]["overall_status"], "PASSED")

    r2 = auditor.remediate(auto_fix=True)
    self.assertEqual(r2["status"], "NO_CHANGES_NEEDED")
    self.assertEqual(r2["changes_applied"], [])

    saved_app = json.loads(
        (self.workspace / "app.json").read_text(encoding="utf-8")
    )
    langs = saved_app["languageSettings"]["supportedLanguageCodes"]
    self.assertNotIn("es", langs)
    self.assertEqual(langs, ["es-ES"])

  def test_accent_code_anchoring_and_descriptive_accents(self):
    """Verifies that descriptive accents are not falsely flagged as locale codes."""
    app_data = {
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": (
                        "# Audio profile\nCustom profile.\n\n"
                        "# Director's Note\n* Persona & Tone: Professional\n"
                        "* Accent: no strong regional accent\n\n##"
                        " Transcript:\n"
                    ),
                },
                "en-IE": {
                    "voice": "en-IE-Chirp3-HD-Aoede",
                    "instruction": (
                        "# Audio profile\nCustom profile.\n\n"
                        "# Director's Note\n* Persona & Tone: Professional\n"
                        "* Accent: an authentic Dublin accent\n\n##"
                        " Transcript:\n"
                    ),
                },
                "it-IT": {
                    "voice": "it-IT-Chirp3-HD-Aoede",
                    "instruction": (
                        "# Audio profile\nCustom profile.\n\n"
                        "# Director's Note\n* Persona & Tone: Professional\n"
                        "* Accent: it depends on caller\n\n## Transcript:\n"
                    ),
                },
            }
        }
    }
    auditor = CXASVoiceAuditor(self.workspace)
    result = auditor.audit_accent_specifications(app_data)
    self.assertTrue(result["passed"])
    self.assertEqual(len(result["issues"]), 0)

    # Bare codes are flagged, and multiple occurrences in one instruction are all reported
    bad_app_data = {
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "multi-bad": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": (
                        "# Audio profile\nProfile.\n\n"
                        "# Director's Note\n"
                        "Accent: en-US\n"
                        "Accent: es-419\n"
                        "Accent: EN_us\n"
                        "## Transcript:\n"
                    ),
                }
            }
        }
    }
    bad_result = auditor.audit_accent_specifications(bad_app_data)
    self.assertFalse(bad_result["passed"])
    self.assertEqual(len(bad_result["issues"]), 3)

  def test_no_app_json_fabrication_on_empty_workspace(self):
    """Verifies that remediate() does not create app.json when absent."""
    auditor = CXASVoiceAuditor(self.workspace)
    res = auditor.remediate(auto_fix=True)
    self.assertEqual(res["status"], "NO_CHANGES_NEEDED")
    self.assertFalse((self.workspace / "app.json").exists())
    self.assertIn("skipped", res)
    self.assertIn(
        "app.json not found; skipped audio-profile/Rule-A007 remediation",
        res["skipped"],
    )

  def test_missing_temperature_audit_and_remediation(self):
    """Verifies that missing modelSettings.temperature is flagged and repaired."""
    app_data = {
        "languageSettings": {"defaultLanguageCode": "en-US"},
        "modelSettings": {},
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": {
                "en-US": {
                    "voice": "en-US-Chirp3-HD-Aoede",
                    "instruction": generate_golden_directors_note("en-US"),
                }
            }
        },
        "variableDeclarations": [{"name": "user_lang"}],
    }
    (self.workspace / "app.json").write_text(
        json.dumps(app_data), encoding="utf-8"
    )

    auditor = CXASVoiceAuditor(self.workspace)
    anti_loop = auditor.audit_anti_looping_and_stability(app_data)
    self.assertFalse(anti_loop["passed"])
    codes = [i["code"] for i in anti_loop["issues"]]
    self.assertIn("MISSING_SAMPLING_TEMPERATURE", codes)

    rem_res = auditor.remediate(auto_fix=True)
    self.assertEqual(rem_res["status"], "REMEDIATED")
    updated_app = json.loads(
        (self.workspace / "app.json").read_text(encoding="utf-8")
    )
    self.assertEqual(updated_app["modelSettings"]["temperature"], 1.0)
    post_audit = auditor.audit_anti_looping_and_stability(updated_app)
    self.assertTrue(post_audit["passed"])

  def test_root_agent_path_segment_matching(self):
    """Verifies only exact root_agent path segments are exempt from voice lock."""
    # root_agent/instruction.txt is exempt
    root_dir = self.workspace / "agents" / "root_agent"
    root_dir.mkdir(parents=True)
    (root_dir / "instruction.txt").write_text(
        "Root agent prompt", encoding="utf-8"
    )

    # root_agent_backup/instruction.txt is NOT exempt
    backup_dir = self.workspace / "agents" / "root_agent_backup"
    backup_dir.mkdir(parents=True)
    backup_inst = backup_dir / "instruction.txt"
    backup_inst.write_text("Backup agent prompt", encoding="utf-8")

    auditor = CXASVoiceAuditor(self.workspace)
    audit_res = auditor.audit_anti_looping_and_stability(
        {"modelSettings": {"temperature": 1.0}}
    )
    self.assertFalse(audit_res["passed"])
    flagged_files = [i["file"] for i in audit_res["issues"]]
    self.assertIn("agents/root_agent_backup/instruction.txt", flagged_files)
    self.assertNotIn("agents/root_agent/instruction.txt", flagged_files)

    rem_changes = auditor.remediate_instruction_voice_locks()
    self.assertEqual(len(rem_changes), 1)
    self.assertIn("<voice_lock>", backup_inst.read_text(encoding="utf-8"))
    self.assertNotIn(
        "<voice_lock>", (root_dir / "instruction.txt").read_text(encoding="utf-8")
    )

  def test_pause_variants_and_prosody_detection_and_stripping(self):
    """Verifies long_pause, medium pause, and arbitrary prosody tags are handled."""
    agents_dir = self.workspace / "agents" / "billing_agent"
    agents_dir.mkdir(parents=True)
    inst_file = agents_dir / "instruction.txt"
    inst_file.write_text(
        'Wait [long_pause] and [medium pause] then [prosody rate="90%"] speak.',
        encoding="utf-8",
    )

    auditor = CXASVoiceAuditor(self.workspace)
    audit_res = auditor.audit_inert_tags()
    self.assertFalse(audit_res["passed"])
    tags = [i["tag"] for i in audit_res["issues"]]
    self.assertIn("[long_pause]", tags)
    self.assertIn("[medium pause]", tags)
    self.assertIn('[prosody rate="90%"]', tags)

    auditor.remediate_inert_tags()
    self.assertEqual(
        inst_file.read_text(encoding="utf-8"), "Wait and then speak."
    )

  def test_default_voice_fallbacks(self):
    """Verifies get_default_voice handles bare and regional languages cleanly."""
    self.assertEqual(get_default_voice("no"), "nb-NO-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("nb-NO"), "nb-NO-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("da"), "da-DK-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("da-DK"), "da-DK-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("sv"), "sv-SE-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("sv-SE"), "sv-SE-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("fi"), "fi-FI-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("fi-FI"), "fi-FI-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("pl"), "pl-PL-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("pl-PL"), "pl-PL-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("tr"), "tr-TR-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("tr-TR"), "tr-TR-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("ar"), "ar-XA-Chirp3-HD-Aoede")
    self.assertEqual(get_default_voice("ar-XA"), "ar-XA-Chirp3-HD-Aoede")


if __name__ == "__main__":
  unittest.main()
