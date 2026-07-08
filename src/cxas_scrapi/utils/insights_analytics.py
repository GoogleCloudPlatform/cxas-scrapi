"""Insights Analytics & Dashboard Generator module for CXAS Scrapi."""

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

import datetime
from typing import Any

from cxas_scrapi.core.insights import Insights


def calculate_start_time_iso(time_window: str) -> str | None:
    """Converts a time window string (e.g. '1h', '24h', '7d', '30d') to an ISO 8601 UTC timestamp."""
    if not time_window or time_window.lower() in ("all", "none", ""):
        return None

    unit = time_window[-1].lower()
    try:
        val = float(time_window[:-1])
    except ValueError:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    if unit == "h":
        delta = datetime.timedelta(hours=val)
    elif unit == "d":
        delta = datetime.timedelta(days=val)
    elif unit == "w":
        delta = datetime.timedelta(weeks=val)
    elif unit == "m":
        delta = datetime.timedelta(days=val * 30)
    else:
        return None

    start_time = now - delta
    return start_time.strftime("%Y-%m-%dT%H:%M:%SZ")


class InsightsAnalytics:
    """Aggregates metrics and generates dashboards from CCAI Insights conversation data."""

    def __init__(
        self, project_id: str, location: str = "us-central1", **kwargs
    ):
        self.project_id = project_id
        self.location = location
        self.insights_client = Insights(
            project_id=project_id, location=location, **kwargs
        )

    def aggregate_metrics(
        self,
        time_window: str = "24h",
        app_name: str | None = None,
        custom_filter: str | None = None,
        conversations: list[dict[str, Any]] | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Aggregates conversation metrics across a time window or provided conversation list."""
        if conversations is None:
            parent = parent or self.insights_client.parent
            filters = []
            if app_name:
                filters.append(f'agent_id="{app_name}"')
            if custom_filter:
                filters.append(f"({custom_filter})")
            start_iso = calculate_start_time_iso(time_window)
            if start_iso:
                filters.append(f'create_time >= "{start_iso}"')
            filter_str = " AND ".join(filters) if filters else None

            conversations = self.insights_client.list_conversations(
                filter_str=filter_str, view="FULL"
            )

        total_convs = len(conversations)
        total_turns = 0
        total_duration = 0.0
        user_sentiments = []

        # Scorecard aggregation: revision_id -> stats dict
        scorecards_data: dict[str, dict[str, Any]] = {}
        # Topics/Issues aggregation: issue_name -> count
        issues_data: dict[str, int] = {}
        # Roster entries
        roster = []

        for conv in conversations:
            turns = conv.get("turnCount", 0)
            if isinstance(turns, str) and turns.isdigit():
                turns = int(turns)
            total_turns += turns

            duration = conv.get("duration", "0s")
            if isinstance(duration, str) and duration.endswith("s"):
                try:
                    total_duration += float(duration[:-1])
                except ValueError:
                    pass

            sentiment = conv.get("sentiment", {})
            # check user sentiment
            if isinstance(sentiment, dict):
                score = sentiment.get("score")
                if score is not None:
                    user_sentiments.append(float(score))

            # Issues extraction
            for issue_assignment in conv.get("issues", []):
                issue_name = issue_assignment.get("issue")
                if issue_name:
                    issues_data[issue_name] = issues_data.get(issue_name, 0) + 1

            # Scorecards extraction
            qa_answers = list(conv.get("qaAnswers", []))
            if "latestAnalysis" in conv:
                qa_scorecard_results = (
                    conv.get("latestAnalysis", {})
                    .get("analysisResult", {})
                    .get("callAnalysisMetadata", {})
                    .get("qaScorecardResults", [])
                )
                if isinstance(qa_scorecard_results, list):
                    qa_answers.extend(qa_scorecard_results)

            conv_scorecard_avg = None
            if isinstance(qa_answers, list):
                for ans in qa_answers:
                    rev = ans.get("qaScorecardRevision", "unknown")
                    if rev not in scorecards_data:
                        scorecards_data[rev] = {
                            "revision": rev,
                            "count": 0,
                            "total_score": 0.0,
                            "total_potential": 0.0,
                            "questions": {},
                        }
                    scorecards_data[rev]["count"] += 1
                    rev_score = 0.0
                    rev_pot = 0.0
                    for q_ans in ans.get("qaQuestions", []) or ans.get(
                        "qaAnswers", []
                    ):
                        q_name = q_ans.get("qaQuestion", "unknown")
                        val_dict = q_ans.get("answerValue", {})
                        raw_score = (
                            val_dict.get("score")
                            if isinstance(val_dict, dict)
                            and val_dict.get("score") is not None
                            else q_ans.get("score")
                        )
                        raw_pot = (
                            val_dict.get("potentialScore")
                            if isinstance(val_dict, dict)
                            and val_dict.get("potentialScore") is not None
                            else q_ans.get("potentialScore")
                        )
                        q_score = (
                            float(raw_score) if raw_score is not None else 0.0
                        )
                        q_pot = float(raw_pot) if raw_pot is not None else 1.0
                        rev_score += q_score
                        rev_pot += q_pot

                        if q_name not in scorecards_data[rev]["questions"]:
                            scorecards_data[rev]["questions"][q_name] = {
                                "question": q_name,
                                "count": 0,
                                "total_score": 0.0,
                                "total_potential": 0.0,
                                "answer_distribution": {},
                            }
                        q_stat = scorecards_data[rev]["questions"][q_name]
                        q_stat["count"] += 1
                        q_stat["total_score"] += float(q_score)
                        q_stat["total_potential"] += float(q_pot)
                        ans_val = (
                            q_ans.get("answerValue", {}).get("strValue")
                            or q_ans.get("answerValue", {}).get("numValue")
                            or "N/A"
                        )
                        ans_str = str(ans_val)
                        q_stat["answer_distribution"][ans_str] = (
                            q_stat["answer_distribution"].get(ans_str, 0) + 1
                        )
                    scorecards_data[rev]["total_score"] += rev_score
                    scorecards_data[rev]["total_potential"] += rev_pot
                    if rev_pot > 0:
                        conv_scorecard_avg = (rev_score / rev_pot) * 100.0

            roster.append(
                {
                    "name": conv.get("name", "N/A"),
                    "create_time": conv.get("createTime", "N/A"),
                    "turns": turns,
                    "duration_seconds": round(
                        float(duration[:-1])
                        if isinstance(duration, str) and duration.endswith("s")
                        else 0.0,
                        1,
                    ),
                    "user_sentiment": round(user_sentiments[-1], 2)
                    if user_sentiments
                    else "N/A",
                    "scorecard_percentage": round(conv_scorecard_avg, 1)
                    if conv_scorecard_avg is not None
                    else "N/A",
                    "issues_count": len(conv.get("issues", [])),
                }
            )

        avg_turns = (
            round(total_turns / total_convs, 1) if total_convs > 0 else 0.0
        )
        avg_duration = (
            round(total_duration / total_convs, 1) if total_convs > 0 else 0.0
        )
        avg_user_sentiment = (
            round(sum(user_sentiments) / len(user_sentiments), 2)
            if user_sentiments
            else 0.0
        )

        # Format scorecard summary list
        scorecard_list = []
        overall_scorecard_pct_accum = 0.0
        scorecard_count = 0
        for rev, sdata in scorecards_data.items():
            pct = (
                round(
                    (sdata["total_score"] / sdata["total_potential"]) * 100.0, 1
                )
                if sdata["total_potential"] > 0
                else 0.0
            )
            overall_scorecard_pct_accum += pct
            scorecard_count += 1
            q_list = []
            for q_name, qdata in sdata["questions"].items():
                q_pct = (
                    round(
                        (qdata["total_score"] / qdata["total_potential"])
                        * 100.0,
                        1,
                    )
                    if qdata["total_potential"] > 0
                    else 0.0
                )
                q_list.append(
                    {
                        "question": q_name,
                        "count": qdata["count"],
                        "pass_percentage": q_pct,
                        "answer_distribution": qdata["answer_distribution"],
                    }
                )
            scorecard_list.append(
                {
                    "revision": rev,
                    "count": sdata["count"],
                    "pass_percentage": pct,
                    "questions": q_list,
                }
            )

        overall_scorecard_avg = (
            round(overall_scorecard_pct_accum / scorecard_count, 1)
            if scorecard_count > 0
            else 0.0
        )

        # Sort top issues
        sorted_issues = sorted(
            [
                {
                    "issue": k,
                    "count": v,
                    "percentage": round((v / total_convs) * 100.0, 1)
                    if total_convs > 0
                    else 0.0,
                }
                for k, v in issues_data.items()
            ],
            key=lambda x: x["count"],
            reverse=True,
        )

        return {
            "timestamp": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "project_id": self.project_id,
            "location": self.location,
            "time_window": time_window,
            "app_name": app_name or "ALL",
            "kpis": {
                "total_conversations": total_convs,
                "average_turns": avg_turns,
                "average_duration_seconds": avg_duration,
                "average_user_sentiment": avg_user_sentiment,
                "overall_scorecard_pass_percentage": overall_scorecard_avg,
            },
            "scorecards": scorecard_list,
            "topics": sorted_issues[:15],
            "conversations_roster": roster,
        }

    def generate_html_dashboard(
        self,
        report_data: dict[str, Any],
        title: str = "CXAS Insights Analytics Dashboard",
    ) -> str:
        """Generates a neat, self-contained, highly stylized HTML dashboard from metrics data."""
        kpis = report_data.get("kpis", {})
        scorecards = report_data.get("scorecards", [])
        topics = report_data.get("topics", [])
        roster = report_data.get("conversations_roster", [])
        time_window = report_data.get("time_window", "24h")
        app_name = report_data.get("app_name", "ALL")

        def _score_color(pct: float | str) -> str:
            if isinstance(pct, str) or pct is None:
                return "#6c757d"
            if pct >= 85.0:
                return "#198754"
            if pct >= 70.0:
                return "#ffc107"
            return "#dc3545"

        # Build Scorecards Section HTML
        scorecards_html = ""
        if not scorecards:
            scorecards_html = "<p class='text-muted'>No scorecard evaluations found in the analyzed period.</p>"
        else:
            for sc in scorecards:
                sc_pct = sc["pass_percentage"]
                color = _score_color(sc_pct)
                rows_html = ""
                for q in sc.get("questions", []):
                    q_pct = q["pass_percentage"]
                    q_color = _score_color(q_pct)
                    dist_badges = " ".join(
                        [
                            f"<span class='badge bg-secondary'>{k}: {v}</span>"
                            for k, v in q["answer_distribution"].items()
                        ]
                    )
                    rows_html += f"""
                    <tr>
                      <td><code>{q["question"].split("/")[-1]}</code></td>
                      <td>{q["count"]}</td>
                      <td>
                        <div class="progress" style="height: 20px;">
                          <div class="progress-bar" role="progressbar" style="width: {q_pct}%; background-color: {q_color}; font-weight: bold;">{q_pct}%</div>
                        </div>
                      </td>
                      <td>{dist_badges}</td>
                    </tr>
                    """
                scorecards_html += f"""
                <div class="card mb-4 shadow-sm">
                  <div class="card-header d-flex justify-content-between align-items-center">
                    <h5 class="mb-0">Scorecard Revision: <code>{sc["revision"].split("/")[-1]}</code></h5>
                    <span class="badge" style="background-color: {color}; font-size: 1.1em;">Overall: {sc_pct}% ({sc["count"]} evals)</span>
                  </div>
                  <div class="card-body p-0">
                    <table class="table table-striped table-hover mb-0">
                      <thead class="table-light">
                        <tr>
                          <th>Question</th>
                          <th>Evaluated Count</th>
                          <th style="width: 35%;">Pass Rate (%)</th>
                          <th>Answer Distribution</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows_html}
                      </tbody>
                    </table>
                  </div>
                </div>
                """

        # Build Topics Chart HTML
        topics_html = ""
        if not topics:
            topics_html = "<p class='text-muted'>No topic models or issues found on these conversations.</p>"
        else:
            max_count = max([t["count"] for t in topics]) if topics else 1
            for t in topics:
                bar_width = max(round((t["count"] / max_count) * 100, 1), 5)
                issue_short = t["issue"].split("/")[-1]
                topics_html += f"""
                <div class="mb-3">
                  <div class="d-flex justify-content-between mb-1">
                    <span class="fw-bold">{issue_short}</span>
                    <span>{t["count"]} convs ({t["percentage"]}%)</span>
                  </div>
                  <div class="progress" style="height: 22px;">
                    <div class="progress-bar bg-info text-dark fw-bold" role="progressbar" style="width: {bar_width}%;">{t["count"]}</div>
                  </div>
                </div>
                """

        # Build Roster Table HTML
        roster_rows = ""
        for r in roster[:50]:  # Cap to top 50 in display
            pct_val = r["scorecard_percentage"]
            pct_badge = (
                f"<span class='badge' style='background-color: {_score_color(pct_val)}'>{pct_val}%</span>"
                if isinstance(pct_val, (int, float))
                else "<span class='badge bg-secondary'>N/A</span>"
            )
            roster_rows += f"""
            <tr>
              <td><code>{r["name"].split("/")[-1]}</code></td>
              <td>{r["create_time"]}</td>
              <td>{r["turns"]}</td>
              <td>{r["duration_seconds"]}s</td>
              <td>{r["user_sentiment"]}</td>
              <td>{pct_badge}</td>
              <td><span class="badge bg-dark">{r["issues_count"]}</span></td>
            </tr>
            """

        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {{ background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
    .kpi-card {{ border-radius: 10px; transition: transform 0.2s; }}
    .kpi-card:hover {{ transform: translateY(-3px); }}
    .kpi-title {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; color: #6c757d; }}
    .kpi-value {{ font-size: 2.2rem; font-weight: 700; }}
  </style>
</head>
<body>
  <div class="container-fluid py-4 px-5">
    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-4 pb-3 border-bottom">
      <div>
        <h1 class="h2 fw-bold text-dark mb-1">{title}</h1>
        <p class="text-muted mb-0">Project: <strong>{report_data.get("project_id")}</strong> | Location: <strong>{report_data.get("location")}</strong> | App: <span class="badge bg-primary">{app_name}</span></p>
      </div>
      <div class="text-end">
        <span class="badge bg-secondary fs-6 px-3 py-2">Window: Last {time_window}</span>
        <div class="text-muted small mt-1">Generated: {report_data.get("timestamp", "")[:19].replace("T", " ")} UTC</div>
      </div>
    </div>

    <!-- KPI Cards Grid -->
    <div class="row g-4 mb-5">
      <div class="col-md-4 col-xl-2">
        <div class="card kpi-card shadow-sm p-3 border-0 bg-white">
          <div class="kpi-title">Analyzed Convs</div>
          <div class="kpi-value text-primary">{kpis.get("total_conversations", 0)}</div>
        </div>
      </div>
      <div class="col-md-4 col-xl-2">
        <div class="card kpi-card shadow-sm p-3 border-0 bg-white">
          <div class="kpi-title">Scorecard Pass Rate</div>
          <div class="kpi-value" style="color: {_score_color(kpis.get("overall_scorecard_pass_percentage", 0))}">{kpis.get("overall_scorecard_pass_percentage", 0)}%</div>
        </div>
      </div>
      <div class="col-md-4 col-xl-2">
        <div class="card kpi-card shadow-sm p-3 border-0 bg-white">
          <div class="kpi-title">Avg Turns</div>
          <div class="kpi-value text-dark">{kpis.get("average_turns", 0)}</div>
        </div>
      </div>
      <div class="col-md-4 col-xl-2">
        <div class="card kpi-card shadow-sm p-3 border-0 bg-white">
          <div class="kpi-title">Avg Duration</div>
          <div class="kpi-value text-dark">{kpis.get("average_duration_seconds", 0)}s</div>
        </div>
      </div>
      <div class="col-md-4 col-xl-2">
        <div class="card kpi-card shadow-sm p-3 border-0 bg-white">
          <div class="kpi-title">User Sentiment</div>
          <div class="kpi-value text-info">{kpis.get("average_user_sentiment", 0)}</div>
        </div>
      </div>
      <div class="col-md-4 col-xl-2">
        <div class="card kpi-card shadow-sm p-3 border-0 bg-white">
          <div class="kpi-title">Top Topics Count</div>
          <div class="kpi-value text-secondary">{len(topics)}</div>
        </div>
      </div>
    </div>

    <!-- Scorecards and Topics -->
    <div class="row g-4 mb-5">
      <div class="col-lg-7">
        <h4 class="mb-3 fw-bold">Scorecard Evaluations & Question Breakdown</h4>
        {scorecards_html}
      </div>
      <div class="col-lg-5">
        <div class="card shadow-sm border-0 bg-white p-4">
          <h4 class="mb-3 fw-bold">Detected Topic Modelling Issues</h4>
          {topics_html}
        </div>
      </div>
    </div>

    <!-- Roster -->
    <div class="card shadow-sm border-0 bg-white mb-5">
      <div class="card-header bg-white py-3">
        <h5 class="mb-0 fw-bold">Analyzed Conversations Roster (Recent 50)</h5>
      </div>
      <div class="card-body p-0 table-responsive">
        <table class="table table-hover mb-0">
          <thead class="table-light">
            <tr>
              <th>Conversation ID</th>
              <th>Create Time (UTC)</th>
              <th>Turns</th>
              <th>Duration</th>
              <th>Sentiment</th>
              <th>Scorecard %</th>
              <th>Topics Tagged</th>
            </tr>
          </thead>
          <tbody>
            {roster_rows}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>
"""
        return html_template
