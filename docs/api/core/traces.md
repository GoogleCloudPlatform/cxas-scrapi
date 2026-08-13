---
title: Traces
---

# Traces

`Traces` is the core Python SDK client for conversation observability, audio analysis, speech-to-text transcription auditing, and BigQuery log reprocessing.

It provides programmatic access to:
- Retrieving full conversation turn details, event timestamps, and tool calls.
- Discovering user and agent turn audio recordings stored in GCS buckets.
- Transcribing user speech turns with Gemini multimodal Flash / Flash-Lite models.
- Evaluating Word Error Rate (WER) metrics against CES reference transcripts.
- Filtering turns for non-English / foreign utterances.
- Reprocessing transcription updates into cloned BigQuery conversation export tables in parallel.

## Quick Example

### 1. Transcribe User Audio & Calculate WER

```python
from cxas_scrapi.core.traces import Traces

app_name = "projects/my-project/locations/us/apps/my-app"
traces = Traces(app_name=app_name)

# 1. Discover user audio recordings from GCS for a conversation
user_audios = traces.get_user_audio_uris(conversation_id="conv-12345")
for item in user_audios:
    print(f"Turn {item['turn_index']}: {item['audio_uri']}")

# 2. Transcribe turns using Gemini Flash and compute WER metrics
results = traces.transcribe_user_turns(
    conversation_id="conv-12345",
    model="gemini-2.5-flash",
    only_non_english=False,
    max_workers=8,
)

for res in results:
    wer = res["wer_metrics"]
    print(
        f"Turn {res['turn_index']}: "
        f"WER={wer['wer']:.1%} "
        f"(Subs={wer['substitutions']}, Dels={wer['deletions']}, Ins={wer['insertions']})"
    )
    print(f"  CES Ref:    {res['reference_transcript']}")
    print(f"  Gemini Hyp: {res['gemini_transcript']}")
```

### 2. Reprocess Conversation in Cloned BigQuery Table

```python
# Reprocess user turn messages into a cloned BigQuery export table
reprocess_summary = traces.reprocess_transcriptions(
    conversation_id="conv-12345",
    model="gemini-2.5-flash",
    only_non_english=True,  # Only reprocess non-English/accented turns
    destination_table="my_dataset.conversation_export_reprocessed",
    clone=True,
    max_workers=16,
)

print(f"Cloned Table: {reprocess_summary['cloned_table']}")
print(f"Updated Turns: {reprocess_summary['updated_turns']}")
```

### 3. Direct Word Error Rate (WER) Utility Usage

```python
from cxas_scrapi.utils.tracing.audio_transcription import (
    AudioTranscriber,
    calculate_wer,
    contains_non_english,
    normalize_text,
)

# Calculate WER between reference and hypothesis
ref = "I would like to check my account balance please"
hyp = "I'd like to check my account balance please"

metrics = calculate_wer(ref, hyp, normalize=True)
print(f"WER: {metrics['wer']:.2%}")
print(f"Substitutions: {metrics['substitutions']}, Hits: {metrics['hits']}")

# Detect non-English characters
is_foreign = contains_non_english("Hola, ¿cómo estás?")
print(f"Contains non-English: {is_foreign}")
```

## Reference

::: cxas_scrapi.core.traces.Traces
    options:
      members:
        - __init__
        - get_trace
        - list_traces
        - get_user_audio_uris
        - transcribe_user_turns
        - reprocess_transcriptions

::: cxas_scrapi.utils.tracing.audio_transcription.AudioTranscriber
    options:
      members:
        - __init__
        - transcribe_gcs_audio

::: cxas_scrapi.utils.tracing.audio_transcription.calculate_wer

::: cxas_scrapi.utils.tracing.audio_transcription.normalize_text

::: cxas_scrapi.utils.tracing.audio_transcription.contains_non_english
