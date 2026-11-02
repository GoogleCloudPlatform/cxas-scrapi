var failuresOnly = false;
var latencyExpanded = false;

function toggleTheme() {
  var doc = document.documentElement;
  var isDark = doc.dataset.theme === "dark" ||
      document.body.classList.contains("dark-mode") ||
      (!doc.dataset.theme && !document.body.classList.contains("light-mode") &&
       window.matchMedia &&
       window.matchMedia("(prefers-color-scheme: dark)").matches);
  if (isDark) {
    doc.dataset.theme = "light";
    document.body.classList.remove("dark-mode");
    document.body.classList.add("light-mode");
  } else {
    doc.dataset.theme = "dark";
    document.body.classList.remove("light-mode");
    document.body.classList.add("dark-mode");
  }
}

function switchTab(tabName) {
  if (!tabName) return;
  if (tabName.startsWith("tab-")) {
    tabName = tabName.substring(4);
  }
  if (tabName === "audio") {
    tabName = "quality";
  }
  var panes = document.querySelectorAll(".tab-pane");
  var buttons = document.querySelectorAll("#report-nav button");

  var targetPane = document.getElementById("tab-" + tabName);
  if (!targetPane) return;

  panes.forEach(function(p) {
    p.classList.remove("active", "tab-on");
  });
  targetPane.classList.add("active", "tab-on");

  buttons.forEach(function(b) {
    var isSel = (b.dataset.tab === tabName);
    b.classList.toggle("active", isSel);
    b.setAttribute("aria-selected", isSel ? "true" : "false");
  });

  if (window.history && window.history.replaceState) {
    history.replaceState(null, "", "#t=" + tabName);
  } else {
    location.hash = "t=" + tabName;
  }
}

function openSuiteLatency() {
  switchTab("latency");
  var el = document.getElementById("section-latency");
  if (el) {
    el.setAttribute("open", "");
    setTimeout(function() {
      el.scrollIntoView({behavior: "smooth", block: "start"});
    }, 50);
  }
}

function toggleAllLatency() {
  latencyExpanded = !latencyExpanded;
  var btn = document.getElementById("btn-latency");
  if (btn) {
    btn.classList.toggle("active", latencyExpanded);
    btn.textContent = latencyExpanded
        ? "⏱ Collapse All Latency"
        : "⏱ Expand All Latency";
  }
  document.querySelectorAll("details.latency-drawer").forEach(function(d) {
    if (latencyExpanded) {
      d.setAttribute("open", "");
    } else {
      d.removeAttribute("open");
    }
  });
}

function switchPlSlice(key) {
  document.querySelectorAll(".pl-slice-panel").forEach(function(panel) {
    panel.style.display = (panel.id === "pl-slice-" + key) ? "block" : "none";
  });
  document.querySelectorAll(".pl-slice-btn").forEach(function(btn) {
    btn.classList.toggle("active", btn.id === "pl-btn-" + key);
  });
}

function jumpTo(type, evalName) {
  if (evalName === undefined) {
    evalName = type;
    type = "sim";
  }
  if (type === "golden") {
    switchTab("goldens");
  } else if (type === "sim") {
    switchTab("sims");
  } else if (type === "tool" || type === "callback") {
    switchTab("unit");
  } else {
    switchTab("overview");
  }

  var safeEval = evalName.replace(/[^a-zA-Z0-9_-]/g, "_");
  var card = document.getElementById("eval-" + type + "-" + evalName) ||
      document.getElementById("eval-" + evalName) ||
      document.getElementById("eval-" + type + "-" + safeEval);
  if (!card) return;

  var details = card.querySelectorAll("details.run-detail");
  var opened = false;
  details.forEach(function(d) {
    if (!opened && d.dataset.failed === "true") {
      d.setAttribute("open", "");
      opened = true;
    } else {
      d.removeAttribute("open");
    }
  });
  if (!opened && details.length > 0) details[0].setAttribute("open", "");

  setTimeout(function() {
    card.scrollIntoView({behavior: "smooth", block: "start"});
    card.classList.add("highlight-target");
    setTimeout(function() { card.classList.remove("highlight-target"); }, 2000);
  }, 50);
}

function jumpToRun(type, evalName, runIdx) {
  if (runIdx === undefined) {
    runIdx = evalName;
    evalName = type;
    type = "sim";
  }
  if (type === "golden") {
    switchTab("goldens");
  } else if (type === "sim") {
    switchTab("sims");
  } else if (type === "tool" || type === "callback") {
    switchTab("unit");
  } else {
    switchTab("overview");
  }

  var safeEval = evalName.replace(/[^a-zA-Z0-9_-]/g, "_");
  var card = document.getElementById("eval-" + type + "-" + evalName) ||
      document.getElementById("eval-" + evalName) ||
      document.getElementById("eval-" + type + "-" + safeEval);
  if (!card) return;

  var details = card.querySelectorAll("details.run-detail");
  details.forEach(function(d) {
    d.removeAttribute("open");
  });
  if (details[runIdx]) {
    details[runIdx].setAttribute("open", "");
  }
  setTimeout(function() {
    card.scrollIntoView({behavior: "smooth", block: "start"});
    card.classList.add("highlight-target");
    setTimeout(function() { card.classList.remove("highlight-target"); }, 2000);
  }, 50);
}

function toggleFailures() {
  failuresOnly = !failuresOnly;
  var btn = document.getElementById("btn-failures");
  if (btn) {
    btn.classList.toggle("active", failuresOnly);
  }
  document.querySelectorAll("tr[data-passed]").forEach(function(row) {
    if (failuresOnly && row.dataset.passed === "true") {
      row.classList.add("hidden-row");
    } else {
      row.classList.remove("hidden-row");
    }
  });
  document.querySelectorAll(".eval-card[data-passed], .card[data-passed]").forEach(function(card) {
    if (failuresOnly && card.dataset.passed === "true") {
      card.classList.add("hidden-card");
      card.classList.add("hidden-row");
    } else {
      card.classList.remove("hidden-card");
      card.classList.remove("hidden-row");
    }
  });
}

function expandAll() {
  document.querySelectorAll("details:not(.latency-drawer)").forEach(function(d) {
    d.setAttribute("open", "");
  });
}

function collapseAll() {
  document.querySelectorAll("details").forEach(function(d) {
    d.removeAttribute("open");
  });
  latencyExpanded = false;
  var btn = document.getElementById("btn-latency");
  if (btn) {
    btn.classList.remove("active");
    btn.textContent = "⏱ Expand All Latency";
  }
}

function switchReportTab(tabName, el) {
  switchTab(tabName);
}

var AUD_S = {
  g: {
    outcome: [],
    grade: [],
    type: [],
    lang: [],
    checks: [],
    intent: [],
    issue: []
  },
  q: "",
  sort: { k: "run", dir: 1 },
  page: 0,
  open: null,
  intents: null
};

var ISSUE_KEY_ALIASES = {
  agent_voice_consistency: "voice_drift",
  personaConsistency: "voice_drift",
  no_long_pauses: "long_pauses",
  perceivedLatency: "long_pauses",
  agent_having_trouble: "having_trouble",
  agent_cutoff: "audio_cutoff",
  repetitionAvoidance: "agent_looping",
  scriptedness: "robotic_delivery",
  emotion: "flat_emotion",
  empathyCalibration: "flat_emotion",
  emotionalArcTracking: "flat_emotion",
  pacing: "unnatural_pacing",
  grammarStyle: "written_grammar",
  spokenNumbers: "written_grammar",
  concision: "overlong_turns",
  turnTaking: "turn_taking",
  disfluency: "turn_taking",
  conversationalFlow: "turn_taking"
};

var AUD_GROUPS = [
  {
    k: "outcome",
    label: "Outcome",
    single: true,
    test: function(r, v) { return (r.outcome || (r.passed ? "passed" : "failed")) === v; },
    opts: function() {
      return [
        { v: "passed", label: "Passed", sw: "--good" },
        { v: "failed", label: "Failed", sw: "--critical" }
      ];
    }
  },
  {
    k: "grade",
    label: "Grade",
    single: false,
    test: function(r, v) { return r.grade_key === v; },
    opts: function() {
      return [
        { v: "human_like", label: "Human-like", sw: "--good" },
        { v: "transitional", label: "Transitional", sw: "--warn" },
        { v: "bot_like", label: "Bot-like", sw: "--critical" }
      ];
    }
  },
  {
    k: "type",
    label: "Eval Type",
    single: true,
    test: function(r, v) { return r.eval_type === v; },
    opts: function() {
      return [
        { v: "sim", label: "Simulation (sim)" },
        { v: "golden", label: "Golden (golden)" }
      ];
    }
  },
  {
    k: "lang",
    label: "Language",
    single: true,
    test: function(r, v) {
      if (v === "switch") return Boolean(r.language_switch);
      if (v === "mismatch") return Boolean(r.language_mismatch);
      return (r.language || "").toLowerCase() === v.toLowerCase();
    },
    opts: function() {
      var rows = (window.__audioConvData && window.__audioConvData.run_rows) || [];
      var langs = [];
      var seen = {};
      var hasSwitch = false;
      var hasMismatch = false;
      rows.forEach(function(r) {
        var l = r.language || "English";
        if (!seen[l]) {
          seen[l] = true;
          langs.push({ v: l.toLowerCase(), label: l });
        }
        if (r.language_switch) hasSwitch = true;
        if (r.language_mismatch) hasMismatch = true;
      });
      if (hasSwitch) langs.push({ v: "switch", label: "Language Switch" });
      if (hasMismatch) langs.push({ v: "mismatch", label: "Language Mismatch", sw: "--critical" });
      return langs;
    }
  },
  {
    k: "checks",
    label: "Audio Checks",
    single: true,
    test: function(r, v) {
      if (v === "pass") return r.audio_checks_status === "PASS";
      if (v === "fail") return r.audio_checks_status === "FAIL";
      return true;
    },
    opts: function() {
      return [
        { v: "pass", label: "All Passed", sw: "--good" },
        { v: "fail", label: "Any Failed", sw: "--critical" }
      ];
    }
  },
  {
    k: "intent",
    label: "Intent",
    single: true,
    test: function(r, v) {
      if (v === "__none__") return false;
      return (r.intent || "") === v;
    },
    opts: function() {
      var rows = (window.__audioConvData && window.__audioConvData.run_rows) || [];
      var intents = [];
      var seen = {};
      rows.forEach(function(r) {
        var it = r.intent || "unknown";
        if (!seen[it]) {
          seen[it] = true;
          intents.push({ v: it, label: humanizeIntent(it) });
        }
      });
      return intents;
    }
  },
  {
    k: "issue",
    label: "Issues / Flags",
    hint: "any of",
    single: false,
    test: function(r, v) {
      var issues = r.issue_keys || [];
      var targetKey = ISSUE_KEY_ALIASES[v] || v;
      return issues.indexOf(v) !== -1 || issues.indexOf(targetKey) !== -1;
    },
    opts: function() {
      var bars = (window.__audioConvData && window.__audioConvData.issue_bars) || [];
      return bars.map(function(b) {
        return { v: b.key, label: b.label };
      });
    }
  }
];

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeJsAttr(s) {
  return escapeHtml(String(s == null ? "" : s).replace(/\\/g, "\\\\").replace(/'/g, "\\'"));
}

function humanizeIntent(k) {
  if (!k || k === "unknown") return "Unknown";
  var s = String(k).replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function hlText(text, q) {
  var s = String(text != null ? text : "");
  if (!q) return escapeHtml(s);
  var ql = q.toLowerCase();
  var sl = s.toLowerCase();
  var idx = sl.indexOf(ql);
  if (idx < 0) return escapeHtml(s);
  return escapeHtml(s.slice(0, idx)) +
    "<mark>" + escapeHtml(s.slice(idx, idx + q.length)) + "</mark>" +
    hlText(s.slice(idx + q.length), q);
}

var ISSUE_SHORT_LABELS = {
  language_mismatch: "Lang mismatch",
  voice_drift: "Voice drift",
  long_pauses: "Dead air",
  agent_looping: "Looping",
  audio_cutoff: "Cutoff",
  having_trouble: "Trouble",
  flat_emotion: "Flat emotion",
  unnatural_pacing: "Pacing",
  written_grammar: "Grammar",
  overlong_turns: "Verbose",
  turn_taking: "Barge-in",
  high_wer: "High WER",
  audio_exp_failed: "Exp failed",
  verdict_barged_missed: "Barged verdict",
  wall_of_text: "Wall of text",
  markdown_link_tts_leakage: "TTS link leak",
  prompt_or_directive_leakage: "Prompt leak",
  early_ack_redundancy: "Early ack",
  subagent_transfer_verbalization: "Router leak",
  subagent_ping_pong_loop: "Ping-pong",
  ignored_user: "Ignored user",
  caller_requested_human: "Asked human",
  tts_cache_seam: "TTS seam",
  loudness_jump: "Loudness jump",
  midturn_voice_seam: "Voice seam",
  audio_truncation: "Truncation"
};

var BAD_ISSUES = [
  "language_mismatch",
  "voice_drift",
  "long_pauses",
  "agent_looping",
  "audio_cutoff",
  "having_trouble",
  "audio_exp_failed",
  "verdict_barged_missed",
  "subagent_ping_pong_loop",
  "ignored_user",
  "markdown_link_tts_leakage",
  "prompt_or_directive_leakage",
  "midturn_voice_seam",
  "audio_truncation"
];

function matchAudioQ(r, q) {
  if (!q) return true;
  var ql = q.toLowerCase();
  if ((r.eval_name || "").toLowerCase().indexOf(ql) !== -1) return true;
  if ((r.session_id || "").toLowerCase().indexOf(ql) !== -1) return true;
  if ((r.summary_text || "").toLowerCase().indexOf(ql) !== -1) return true;
  if ((r.failure_reasons || []).some(function(fr) { return fr.toLowerCase().indexOf(ql) !== -1; })) return true;
  if ((r.issue_labels || []).some(function(il) { return il.toLowerCase().indexOf(ql) !== -1; })) return true;
  if ((r.transcript_messages || []).some(function(tm) { return (tm[1] || "").toLowerCase().indexOf(ql) !== -1; })) return true;
  return false;
}

function getSearchSnippet(r, q) {
  if (!q) return "";
  var ql = q.toLowerCase();
  if (r.summary_text && r.summary_text.toLowerCase().indexOf(ql) !== -1) {
    return "Summary: " + hlText(r.summary_text, q);
  }
  for (var i = 0; i < (r.failure_reasons || []).length; i++) {
    var fr = r.failure_reasons[i];
    if (fr.toLowerCase().indexOf(ql) !== -1) {
      return "Failure: " + hlText(fr, q);
    }
  }
  for (var j = 0; j < (r.transcript_messages || []).length; j++) {
    var tm = r.transcript_messages[j];
    if ((tm[1] || "").toLowerCase().indexOf(ql) !== -1) {
      var whoLabel = tm[0] === "caller" ? "Caller" : (tm[0] === "agent" ? "Agent" : "Event");
      return whoLabel + ": " + hlText(tm[1], q);
    }
  }
  return "";
}

function audioPassing(skipGroup) {
  var rows = (window.__audioConvData && window.__audioConvData.run_rows) || [];
  var q = (AUD_S.q || "").trim();
  return rows.filter(function(r) {
    if (!matchAudioQ(r, q)) return false;
    for (var i = 0; i < AUD_GROUPS.length; i++) {
      var g = AUD_GROUPS[i];
      if (g.k === skipGroup) continue;
      var sel = AUD_S.g[g.k] || [];
      if (sel.length > 0) {
        var anyMatch = false;
        for (var j = 0; j < sel.length; j++) {
          if (g.test(r, sel[j])) { anyMatch = true; break; }
        }
        if (!anyMatch) return false;
      }
    }
    return true;
  });
}

var AUD_SORTS = {
  run: function(r) { return r.run_num || 0; },
  eval: function(r) { return (r.eval_name || "").toLowerCase(); },
  type: function(r) { return (r.eval_type || "").toLowerCase(); },
  outcome: function(r) { return r.passed ? 1 : 0; },
  grade: function(r) {
    var pts = { human_like: 4, transitional: 2, bot_like: 0, ungraded: -1 };
    return pts[r.grade_key] !== undefined ? pts[r.grade_key] : -1;
  },
  score: function(r) { return r.score != null ? r.score : -1; },
  lang: function(r) { return (r.language || "").toLowerCase(); },
  checks: function(r) { return r.audio_checks_status === "PASS" ? 2 : (r.audio_checks_status === "FAIL" ? 0 : 1); },
  turns: function(r) { return r.turns_count || 0; },
  latency: function(r) { return r.pl_p50_ms != null ? r.pl_p50_ms : 999999; },
  wer: function(r) { return r.wer != null ? r.wer : -1; }
};

function renderAudioConversations() {
  if (!window.__audioConvData) return;
  var allRows = window.__audioConvData.run_rows || [];
  var rs = audioPassing(null);

  // Update counts
  var convNEl = document.getElementById("conv-n");
  if (convNEl) convNEl.textContent = "Showing " + rs.length + " of " + allRows.length + " conversations";
  var tabConvN = document.getElementById("tab-conv-n");
  if (tabConvN) tabConvN.textContent = rs.length;
  updateIntentBtn();

  // Render Facets
  var facetsEl = document.getElementById("audio-facets");
  if (facetsEl) {
    var facetsHtml = "";
    AUD_GROUPS.forEach(function(g) {
      var pool = audioPassing(g.k);
      var sel = AUD_S.g[g.k] || [];
      var rowBtns = [];
      if (g.single) {
        var isAll = (sel.length === 0);
        rowBtns.push(
          '<button type="button" class="facet" data-g="' + g.k + '" data-gv="" aria-pressed="' +
          isAll + '" onclick="toggleAudioFacet(\'' + g.k + '\', \'\', true)">All <span class="c">' +
          pool.length + '</span></button>'
        );
      } else {
        var isAllMulti = (sel.length === 0);
        rowBtns.push(
          '<button type="button" class="facet" data-g="' + g.k + '" data-gv="" aria-pressed="' +
          isAllMulti + '" onclick="toggleAudioFacet(\'' + g.k + '\', \'\', false)">All <span class="c">' +
          pool.length + '</span></button>'
        );
      }
      var opts = g.opts();
      opts.forEach(function(o) {
        var cnt = pool.filter(function(r) { return g.test(r, o.v); }).length;
        var pressed = (sel.indexOf(o.v) !== -1);
        var zeroCls = (cnt === 0 && !pressed) ? " zero" : "";
        var swHtml = o.sw ? '<span class="sw" style="background:var(' + o.sw + ')"></span>' : '';
        rowBtns.push(
          '<button type="button" class="facet' + zeroCls + '" data-g="' + g.k + '" data-gv="' +
          escapeHtml(o.v) + '" aria-pressed="' + pressed + '" onclick="toggleAudioFacet(\'' +
          g.k + '\', \'' + escapeHtml(o.v) + '\', ' + Boolean(g.single) + ')">' +
          swHtml + escapeHtml(o.label) + ' <span class="c">' + cnt + '</span></button>'
        );
      });
      facetsHtml += '<div class="fg"><span class="fg-l">' + escapeHtml(g.label) +
        (g.hint ? ' <span class="fg-h">' + escapeHtml(g.hint) + '</span>' : '') +
        '</span><div class="fg-o">' + rowBtns.join(" ") + '</div></div>\n';
    });
    facetsEl.innerHTML = facetsHtml;
  }

  // Render Active Filters
  var activeEl = document.getElementById("audio-active-filters");
  if (activeEl) {
    var chips = [];
    AUD_GROUPS.forEach(function(g) {
      var sel = AUD_S.g[g.k] || [];
      var optsMap = {};
      g.opts().forEach(function(o) { optsMap[o.v] = o.label; });
      sel.forEach(function(v) {
        var lbl = optsMap[v] || v;
        chips.push(
          '<span class="af">' + escapeHtml(g.label) + ': ' + escapeHtml(lbl) +
          '<button type="button" onclick="removeAudioFilter(\'' + g.k + '\', \'' +
          escapeHtml(v) + '\')" aria-label="Remove filter">×</button></span>'
        );
      });
    });
    if (AUD_S.q) {
      chips.push(
        '<span class="af">Search: &quot;' + escapeHtml(AUD_S.q) +
        '&quot;<button type="button" onclick="clearAudioConvSearch()" aria-label="Clear search">×</button></span>'
      );
    }
    if (chips.length > 0) {
      chips.push('<button type="button" class="af-clear" onclick="clearAudioRunFilter()">Clear all filters</button>');
    }
    activeEl.innerHTML = chips.join(" ");
  }

  // Sort
  var sk = AUD_SORTS[AUD_S.sort.k] || AUD_SORTS.run;
  rs.sort(function(a, b) {
    var x = sk(a);
    var y = sk(b);
    return (x < y ? -1 : (x > y ? 1 : 0)) * AUD_S.sort.dir;
  });
  window.__audioFilteredRows = rs;

  // Update Sort Header Arrows
  document.querySelectorAll("#audio-runs-table th .sort-arr").forEach(function(el) {
    el.textContent = "";
  });
  var activeArr = document.getElementById("sort-arr-" + AUD_S.sort.k);
  if (activeArr) {
    activeArr.textContent = AUD_S.sort.dir > 0 ? " ↑" : " ↓";
  }

  // Paging
  var PAGE_SIZE = 50;
  var pages = Math.max(1, Math.ceil(rs.length / PAGE_SIZE));
  AUD_S.page = Math.max(0, Math.min(AUD_S.page, pages - 1));
  var pg = rs.slice(AUD_S.page * PAGE_SIZE, (AUD_S.page + 1) * PAGE_SIZE);

  // Render Table
  var tbody = document.querySelector("#audio-runs-table tbody");
  if (tbody) {
    if (pg.length === 0) {
      tbody.innerHTML = '<tr><td colspan="12" style="text-align:center;color:var(--text-muted);padding:32px;">No conversations match these filters.</td></tr>';
    } else {
      var rowsHtml = "";
      var q = AUD_S.q.trim();
      pg.forEach(function(r) {
        var evalName = r.eval_name;
        var runNum = r.run_num;
        var evalType = r.eval_type;
        var scoreStr = r.score != null ? r.score.toFixed(1) + "/5" : "—";
        var gk = r.grade_key;
        var gl = r.grade_label;
        var gradeBadge = '<span class="badge ' + escapeHtml(gk) + '">' + escapeHtml(gl) + '</span>';
        var typeBadge = '<span class="badge ' + escapeHtml(evalType) + '">' + escapeHtml(evalType) + '</span>';
        var issuesStr = (r.issue_keys || []).join(" ");
        var passStr = r.passed ? "PASS" : "FAIL";
        var passCls = r.passed ? "pass" : "fail";
        var plStr = r.pl_p50_ms != null ? r.pl_p50_ms.toFixed(0) + " ms" : "—";
        var werStr = r.wer != null ? (r.wer * 100).toFixed(1) + "%" : "—";
        var chkStr = r.audio_checks_status;
        var chkCls = (chkStr === "PASS") ? "pass" : ((chkStr === "FAIL") ? "fail" : "meta");
        var langStatus = r.language_status_label;
        var langCls = r.language_mismatch ? "fail" : "";
        var langKey = (r.language || "").toLowerCase();
        var mismatchStr = String(r.language_mismatch).toLowerCase();
        var outcomeStr = r.outcome || (r.passed ? "passed" : "failed");
        var rowId = r.id || r.row_id;
        var isSel = (AUD_S.open === rowId);

        var flagsHtml = "";
        for (var i = 0; i < (r.issue_keys || []).length; i++) {
          var ik = r.issue_keys[i];
          var fullLbl = r.issue_labels && r.issue_labels[i] ? r.issue_labels[i] : ik;
          var fl = (r.flag_labels && r.flag_labels[i]) ? r.flag_labels[i] : (ISSUE_SHORT_LABELS[ik] || fullLbl);
          var clsName = (BAD_ISSUES.indexOf(ik) !== -1) ? "bad" : "warn";
          var tipTxt = (window.__audioConvData && window.__audioConvData.tooltips && window.__audioConvData.tooltips.issue_tips) ? (window.__audioConvData.tooltips.issue_tips[ik] || "") : "";
          flagsHtml += '<span class="flag ' + clsName + '" title="' + escapeHtml(fullLbl) + '"' + (tipTxt ? ' data-tip="' + escapeHtml(tipTxt) + '"' : '') + '>' + escapeHtml(fl) + '</span>';
        }
        if (!flagsHtml) flagsHtml = '<span style="color:var(--text-muted);font-size:11px;">None</span>';

        var sidHtml = r.session_id ? '<div class="mono ell" style="font-size:11px;color:var(--text-muted);max-width:180px;" title="' + escapeHtml(r.session_id) + '">' + hlText(r.session_id, q) + '</div>' : '';

        rowsHtml += '<tr class="audio-run-row clickable' + (isSel ? ' sel' : '') + '" ' +
          'data-grade="' + escapeHtml(gk) + '" ' +
          'data-lang="' + escapeHtml(langKey) + '" ' +
          'data-intent="' + escapeHtml(r.intent || '') + '" ' +
          'data-issues="' + escapeHtml(issuesStr) + '" ' +
          'data-mismatch="' + escapeHtml(mismatchStr) + '" ' +
          'data-outcome="' + escapeHtml(outcomeStr) + '" ' +
          'data-type="' + escapeHtml(evalType) + '" ' +
          'data-checks="' + escapeHtml(chkStr) + '" ' +
          'data-row-id="' + escapeHtml(rowId) + '" ' +
          'onclick="openAudioConv(\'' + escapeJsAttr(rowId) + '\');">\n' +
          '  <td class="num">#' + runNum + '</td>\n' +
          '  <td><b class="ell" style="display:block;max-width:200px;" title="' + escapeHtml(evalName) + '">' + hlText(evalName, q) + '</b>' + sidHtml + '</td>\n' +
          '  <td>' + typeBadge + '</td>\n' +
          '  <td><span class="badge ' + escapeHtml(passCls) + '">' + escapeHtml(passStr) + '</span></td>\n' +
          '  <td>' + gradeBadge + '</td>\n' +
          '  <td class="num">' + scoreStr + '</td>\n' +
          '  <td><span class="' + langCls + '">' + escapeHtml(langStatus) + '</span></td>\n' +
          '  <td><span class="badge ' + escapeHtml(chkCls) + '">' + escapeHtml(chkStr) + '</span></td>\n' +
          '  <td class="num">' + (r.turns_count || 1) + '</td>\n' +
          '  <td class="num">' + plStr + '</td>\n' +
          '  <td class="num">' + werStr + '</td>\n' +
          '  <td>' + flagsHtml + '</td>\n' +
          '</tr>\n';

        if (q) {
          var snip = getSearchSnippet(r, q);
          if (snip) {
            rowsHtml += '<tr class="audio-run-snippet-row" onclick="openAudioConv(\'' + escapeJsAttr(rowId) + '\');">' +
              '<td></td><td colspan="11" style="font-size:12px;color:var(--text-secondary);padding-top:2px;padding-bottom:10px;">' +
              snip + '</td></tr>\n';
          }
        }
      });
      tbody.innerHTML = rowsHtml;
    }
  }

  // Update Pager
  var pagerInfo = document.getElementById("audio-conv-pager-info");
  if (pagerInfo) {
    var startIdx = rs.length ? (AUD_S.page * PAGE_SIZE + 1) : 0;
    var endIdx = Math.min(rs.length, (AUD_S.page + 1) * PAGE_SIZE);
    pagerInfo.textContent = "Showing " + startIdx + "–" + endIdx + " of " + rs.length;
  }
  var pagerPrev = document.getElementById("audio-pager-prev");
  if (pagerPrev) pagerPrev.disabled = (AUD_S.page <= 0);
  var pagerCurr = document.getElementById("audio-pager-curr");
  if (pagerCurr) pagerCurr.textContent = (AUD_S.page + 1) + " / " + pages;
  var pagerNext = document.getElementById("audio-pager-next");
  if (pagerNext) pagerNext.disabled = (AUD_S.page >= pages - 1);
}

function handleAudioConvSearch(val) {
  AUD_S.q = val || "";
  AUD_S.page = 0;
  renderAudioConversations();
}

function clearAudioConvSearch() {
  AUD_S.q = "";
  AUD_S.page = 0;
  var inp = document.getElementById("audio-conv-search");
  if (inp) inp.value = "";
  renderAudioConversations();
}

function toggleAudioFacet(gKey, optVal, isSingle) {
  if (!optVal) {
    AUD_S.g[gKey] = [];
  } else if (isSingle) {
    if (AUD_S.g[gKey] && AUD_S.g[gKey].length === 1 && AUD_S.g[gKey][0] === optVal) {
      AUD_S.g[gKey] = [];
    } else {
      AUD_S.g[gKey] = [optVal];
    }
  } else {
    var cur = AUD_S.g[gKey] || [];
    var idx = cur.indexOf(optVal);
    if (idx !== -1) {
      cur.splice(idx, 1);
    } else {
      cur.push(optVal);
    }
    AUD_S.g[gKey] = cur;
  }
  if (gKey === "intent") {
    if (!AUD_S.g.intent || AUD_S.g.intent.length === 0) {
      AUD_S.intents = null;
    } else {
      AUD_S.intents = AUD_S.g.intent.slice();
    }
    updateIntentBtn();
    renderIntentSel();
    updateAudioQualitySummary(AUD_S.intents);
  }
  AUD_S.page = 0;
  renderAudioConversations();
}

function removeAudioFilter(gKey, optVal) {
  var cur = AUD_S.g[gKey] || [];
  var idx = cur.indexOf(optVal);
  if (idx !== -1) cur.splice(idx, 1);
  AUD_S.g[gKey] = cur;
  if (gKey === "intent") {
    if (!AUD_S.g.intent || AUD_S.g.intent.length === 0) {
      AUD_S.intents = null;
    } else {
      AUD_S.intents = AUD_S.g.intent.slice();
    }
    updateIntentBtn();
    renderIntentSel();
    updateAudioQualitySummary(AUD_S.intents);
  }
  AUD_S.page = 0;
  renderAudioConversations();
}

function sortAudioConversations(colKey) {
  if (AUD_S.sort.k === colKey) {
    AUD_S.sort.dir = -AUD_S.sort.dir;
  } else {
    AUD_S.sort.k = colKey;
    AUD_S.sort.dir = 1;
  }
  renderAudioConversations();
}

function setAudioConvPage(delta) {
  AUD_S.page += delta;
  renderAudioConversations();
}

function filterAudioRunsByGrade(gradeKey) {
  switchTab("conversations");
  AUD_S.g.grade = gradeKey ? [gradeKey] : [];
  AUD_S.page = 0;
  renderAudioConversations();
  var convSec = document.getElementById("conversations");
  if (convSec) convSec.scrollIntoView({ behavior: "smooth" });
}

function filterAudioRunsByLang(langKey) {
  switchTab("conversations");
  var target = (langKey === "language_switch") ? "switch" : langKey;
  AUD_S.g.lang = target ? [target] : [];
  AUD_S.page = 0;
  renderAudioConversations();
  var convSec = document.getElementById("conversations");
  if (convSec) convSec.scrollIntoView({ behavior: "smooth" });
}

function filterAudioRunsByIssue(issueKey) {
  switchTab("conversations");
  AUD_S.g.issue = issueKey ? [issueKey] : [];
  AUD_S.page = 0;
  renderAudioConversations();
  var convSec = document.getElementById("conversations");
  if (convSec) convSec.scrollIntoView({ behavior: "smooth" });
}

function clearAudioRunFilter() {
  AUD_S.g = { outcome: [], grade: [], type: [], lang: [], checks: [], intent: [], issue: [] };
  AUD_S.q = "";
  AUD_S.page = 0;
  AUD_S.intents = null;
  var inp = document.getElementById("audio-conv-search");
  if (inp) inp.value = "";
  updateIntentBtn();
  renderIntentSel();
  renderAudioConversations();
  updateAudioQualitySummary(null);
}

function openAudioConv(id) {
  if (!window.__audioConvData) return;
  var rows = window.__audioConvData.run_rows || [];
  var r = rows.find(function(x) { return x.id === id || x.row_id === id; });
  if (!r) return;
  AUD_S.open = id;

  var q = AUD_S.q.trim();
  var drH = document.getElementById("dr-h");
  if (drH) {
    var passStr = r.passed ? "PASSED" : "FAILED";
    var passCls = r.passed ? "pass" : "fail";
    var safeName = escapeJsAttr(r.eval_name);
    drH.innerHTML =
      '<span class="badge ' + escapeHtml(r.grade_key) + '" style="font-size:12px;padding:3px 8px;">' + escapeHtml(r.grade_label) + '</span>' +
      '<div style="min-width:0;flex:1;">' +
      '  <div style="font-weight:600;font-size:14px;color:var(--text-primary);">' + escapeHtml(r.eval_name) + ' <span style="font-weight:400;color:var(--text-muted);font-size:12px;">#' + r.run_num + '</span></div>' +
      '  <div class="id" style="color:var(--text-muted);font-size:11px;" title="' + escapeHtml(r.session_id) + '">' + escapeHtml(r.session_id || "No session ID") + '</div>' +
      '</div>' +
      '<span class="badge ' + escapeHtml(passCls) + '">' + escapeHtml(passStr) + '</span>' +
      '<button type="button" class="icon-btn" onclick="switchReportTab(\'overview\'); jumpToRun(\'' + escapeJsAttr(r.eval_type) + '\', \'' + safeName + '\', ' + r.run_idx + '); closeAudioConv();" title="Jump to full trace in Overview tab">Open in Overview ↗</button>' +
      '<button type="button" class="icon-btn" onclick="if (navigator.clipboard) navigator.clipboard.writeText(\'' + escapeJsAttr(r.session_id) + '\')" title="Copy session ID">⧉</button>' +
      '<button type="button" class="icon-btn" onclick="closeAudioConv()" title="Close (esc)">✕</button>';
  }

  var drB = document.getElementById("dr-b");
  if (drB) {
    var prevAudios = document.querySelectorAll("#audio-conv-drawer audio");
    prevAudios.forEach(function(a) {
      try { a.pause(); } catch (e) {}
    });

    var bHtml = "";

    // Resolve Full Conversation audio player src
    var audioSrc = "";
    var safeEval = (r.eval_name || "").replace(/[^a-zA-Z0-9_-]/g, "_");
    var card = document.getElementById("eval-" + r.eval_type + "-" + r.eval_name) ||
        document.getElementById("eval-" + r.eval_name) ||
        document.getElementById("eval-" + r.eval_type + "-" + safeEval);
    if (card) {
      var details = card.querySelectorAll("details.run-detail");
      var targetDetail = (r.run_idx != null && details[r.run_idx]) ? details[r.run_idx] : (details[0] || card);
      var audEl = targetDetail.querySelector(".audio-player audio") || card.querySelector(".audio-player audio");
      if (audEl && audEl.getAttribute("src")) {
        audioSrc = audEl.getAttribute("src");
      }
    }
    if (!audioSrc && r.audio_src) {
      audioSrc = r.audio_src;
    }
    if (!audioSrc && r.session_id) {
      audioSrc = "audio/" + r.session_id + "/" + (r.eval_type === "golden" ? "full-session.wav" : "full-scenario.wav");
    }

    if (audioSrc) {
      bHtml += '<div class="audio-player" style="margin-bottom:12px;">\n' +
        '  <span class="audio-label">Full Conversation</span>\n' +
        '  <audio controls preload="none" src="' + escapeHtml(audioSrc) + '"></audio>\n' +
        '</div>\n';
    }

    // Summary
    bHtml += '<h4>Summary</h4><div class="conv-summary">' + hlText(r.summary_text || "No summary available.", q) + '</div>\n';
    if (r.failure_reasons && r.failure_reasons.length > 0) {
      bHtml += '<div class="failure-reasons" style="margin-top:8px;">' +
        r.failure_reasons.map(function(fr) {
          return '<div style="color:var(--critical);font-size:12px;margin:2px 0;">⚠ ' + hlText(fr, q) + '</div>';
        }).join("") + '</div>\n';
    }

    // Flagged Issues
    if (r.issue_keys && r.issue_keys.length > 0) {
      bHtml += '<h4>Flagged Issues</h4><div style="display:flex;flex-wrap:wrap;gap:4px;">';
      for (var ikIdx = 0; ikIdx < r.issue_keys.length; ikIdx++) {
        var k = r.issue_keys[ikIdx];
        var lbl = (r.issue_labels && r.issue_labels[ikIdx]) ? r.issue_labels[ikIdx] : k;
        bHtml += '<button type="button" class="chip" onclick="filterAudioRunsByIssue(\'' + escapeHtml(k) + '\'); closeAudioConv();">' + escapeHtml(lbl) + '</button>';
      }
      bHtml += '</div>\n';
    }

    // Signals & Audio Metrics
    bHtml += '<h4>Signals &amp; Audio Metrics</h4><dl class="kv">\n' +
      '<dt>Session ID</dt><dd class="mono">' + escapeHtml(r.session_id || "—") + '</dd>\n' +
      '<dt>Eval Type &amp; Status</dt><dd>' + escapeHtml(r.eval_type) + ' &middot; ' + (r.passed ? 'PASSED' : 'FAILED') + '</dd>\n' +
      '<dt>Intent (File)</dt><dd class="mono">' + escapeHtml(r.intent || "—") + '</dd>\n' +
      '<dt>Naturalness Grade</dt><dd>' + escapeHtml(r.grade_label) + ' (' + (r.score != null ? r.score.toFixed(1) + '/5.0' : '—') + ')</dd>\n' +
      '<dt>Language &amp; Adherence</dt><dd>' + escapeHtml(r.language_status_label) + (r.language_mismatch ? ' (Mismatch)' : '') + '</dd>\n' +
      '<dt>Perceived Latency</dt><dd>Median: ' + (r.pl_p50_ms != null ? r.pl_p50_ms.toFixed(0) + ' ms' : '—') +
      (r.pl_max_ms != null ? ' &middot; Max: ' + r.pl_max_ms.toFixed(0) + ' ms' : '') + '</dd>\n' +
      ((r.barge_turns != null || r.max_tts_ms != null) ? '<dt>Barge-In &amp; Max TTS</dt><dd>' + (r.barge_turns || 0) + ' barge-in turns' + (r.max_tts_ms ? ' &middot; Max TTS: ' + (r.max_tts_ms / 1000).toFixed(1) + 's' : '') + '</dd>\n' : '') +
      '<dt>Transcription WER</dt><dd>' + (r.wer != null ? (r.wer * 100).toFixed(1) + '%' : '—') + '</dd>\n' +
      '<dt>Duration &amp; Turns</dt><dd>' + (r.turns_count || 1) + ' turns' + (r.duration_s ? ' &middot; ' + r.duration_s.toFixed(1) + 's' : '') + '</dd>\n' +
      '</dl>\n';

    // Signal & Acoustic Voice Metrics (LUFS / Pitch / Drift)
    var ad = r.acoustic_detail;
    var hasAcousticData = ad && (
      ad.lufs_mean != null ||
      ad.rate_mean != null ||
      ad.f0_median_sd_st != null ||
      ad.max_seam_st != null ||
      (ad.flags && ad.flags.length > 0) ||
      (ad.drift_reasons && ad.drift_reasons.length > 0) ||
      (ad.turns && ad.turns.length > 0) ||
      (ad.barge_in_details && ad.barge_in_details.length > 0)
    );
    if (hasAcousticData) {
      bHtml += '<h4>Signal &amp; Acoustic Voice Metrics (LUFS / Pitch / Drift)</h4>\n';
      var hasKv = (ad.lufs_mean != null || ad.rate_mean != null || ad.f0_median_sd_st != null || ad.max_seam_st != null);
      if (hasKv) {
        bHtml += '<dl class="kv">\n';
        if (ad.lufs_mean != null) {
          bHtml += '<dt>Loudness (LUFS)</dt><dd>Mean: ' + ad.lufs_mean.toFixed(1) + ' dB' +
            (ad.lufs_sd != null ? ' &middot; SD: &plusmn;' + ad.lufs_sd.toFixed(2) + ' dB' : '') +
            (ad.max_adj_dlufs != null ? ' &middot; Max \u0394: ' + ad.max_adj_dlufs.toFixed(1) + ' dB' : '') + '</dd>\n';
        }
        if (ad.rate_mean != null) {
          bHtml += '<dt>Articulation Rate</dt><dd>' + ad.rate_mean.toFixed(1) + ' syl/s' +
            (ad.rate_cov_pct != null ? ' (CoV: ' + ad.rate_cov_pct.toFixed(1) + '%)' : '') + '</dd>\n';
        }
        if (ad.f0_median_sd_st != null || ad.max_seam_st != null) {
          bHtml += '<dt>Pitch &amp; Voice Seam</dt><dd>' +
            (ad.f0_median_sd_st != null ? 'Spread: ' + ad.f0_median_sd_st.toFixed(1) + ' st' : '') +
            (ad.max_seam_st != null ? ' &middot; Max Intra-Turn Seam: ' + ad.max_seam_st.toFixed(1) + ' st' : '') + '</dd>\n';
        }
        bHtml += '</dl>\n';
      }

      if (ad.flags && ad.flags.length > 0) {
        bHtml += '<div style="margin:6px 0;"><b>Acoustic Flags: </b>';
        ad.flags.forEach(function(fl) {
          var isBad = (fl === "MIDTURN" || fl === "TRUNC" || fl === "INTRA_TURN_SPEAKER_CHANGE");
          bHtml += '<span class="flag ' + (isBad ? "bad" : "warn") + '" style="margin-right:4px;">' + escapeHtml(fl) + '</span>';
        });
        bHtml += '</div>\n';
      }

      if (ad.drift_reasons && ad.drift_reasons.length > 0) {
        bHtml += '<div style="margin:6px 0;font-size:12px;color:var(--text-muted);">';
        ad.drift_reasons.forEach(function(dr) {
          bHtml += '<div>\u2022 ' + escapeHtml(dr) + '</div>';
        });
        bHtml += '</div>\n';
      }

      if (ad.turns && ad.turns.length > 0) {
        bHtml += '<table class="audio-runs-table" style="margin:8px 0;font-size:11px;width:100%;">\n' +
          '<thead><tr><th>Turn</th><th>Dur</th><th>LUFS</th><th>Rate</th><th>Pitch</th><th>TTS</th><th>Flags</th></tr></thead>\n<tbody>\n';
        ad.turns.forEach(function(tr) {
          var fBadges = (tr.flags || []).map(function(f) {
            var bCls = (f === "MIDTURN" || f === "TRUNC") ? "bad" : "warn";
            return '<span class="flag ' + bCls + '" style="font-size:10px;">' + escapeHtml(f) + '</span>';
          }).join(" ");
          bHtml += '<tr>' +
            '<td class="mono">#' + tr.turn + '</td>' +
            '<td>' + (tr.dur_s != null ? tr.dur_s.toFixed(1) + 's' : '\u2014') + '</td>' +
            '<td>' + (tr.lufs != null ? tr.lufs.toFixed(1) : '\u2014') + '</td>' +
            '<td>' + (tr.artic_rate != null ? tr.artic_rate.toFixed(1) : '\u2014') + '</td>' +
            '<td>' + (tr.pitch_hz != null ? tr.pitch_hz.toFixed(0) + ' Hz' : '\u2014') + '</td>' +
            '<td>' + (tr.tts ? '<span class="chip" style="font-size:10px;">' + escapeHtml(tr.tts) + '</span>' : '\u2014') + '</td>' +
            '<td>' + (fBadges || '<span style="color:var(--text-muted);">&mdash;</span>') + '</td>' +
            '</tr>\n';
        });
        bHtml += '</tbody></table>\n';
      }

      if (ad.barge_in_details && ad.barge_in_details.length > 0) {
        bHtml += '<h5>Barge-In Assertions &amp; VAD Signals</h5><div style="font-size:12px;margin:4px 0;">\n';
        ad.barge_in_details.forEach(function(bid) {
          bHtml += '<div style="padding:4px 0;border-bottom:1px solid var(--border-subtle);">' +
            '<b>' + escapeHtml(bid.step) + '</b>: ' +
            (bid.barge_in_after_ms != null ? 'barge_in_after_ms=' + bid.barge_in_after_ms + 'ms &middot; ' : '') +
            'interrupted=' + bid.agent_interrupted +
            (bid.vad_barge_in ? ' &middot; vad_barge_in=true' : '') +
            (bid.vad_skip_reason ? ' &middot; <span style="color:var(--warn);">skip=' + escapeHtml(bid.vad_skip_reason) + '</span>' : '') +
            (bid.played_audio_duration_ms != null ? ' &middot; played=' + bid.played_audio_duration_ms.toFixed(0) + 'ms/' + (bid.total_audio_duration_ms != null ? bid.total_audio_duration_ms.toFixed(0) + 'ms' : '?') : '') +
            '</div>\n';
        });
        bHtml += '</div>\n';
      }
    }

    // Multimodal Audio Checks
    if (r.audio_checks_detail && r.audio_checks_detail.length > 0) {
      bHtml += '<h4>Multimodal Audio Checks</h4><div style="display:flex;flex-direction:column;gap:6px;margin:8px 0;">\n';
      r.audio_checks_detail.forEach(function(chk) {
        var cls = (chk.result === "PASS") ? "pass" : ((chk.result === "FAIL") ? "fail" : "meta");
        bHtml += '<div style="display:flex;align-items:baseline;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border-subtle);font-size:12px;">' +
          '<div><b>' + escapeHtml(chk.label) + '</b>' +
          (chk.justification ? '<br><span style="color:var(--text-muted);font-size:11px;">' + escapeHtml(chk.justification) + '</span>' : '') +
          '</div><span class="badge ' + cls + '">' + escapeHtml(chk.result) + '</span></div>\n';
      });
      bHtml += '</div>\n';
    }

    // Conversation Factors
    if (r.conv_factors_detail && r.conv_factors_detail.length > 0) {
      bHtml += '<h4>Conversation Naturalness Factors</h4><div style="display:flex;flex-direction:column;gap:6px;margin:8px 0;">\n';
      r.conv_factors_detail.forEach(function(cf) {
        bHtml += '<div style="display:flex;align-items:baseline;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border-subtle);font-size:12px;">' +
          '<div><b>' + escapeHtml(cf.label) + '</b>' +
          (cf.justification ? '<br><span style="color:var(--text-muted);font-size:11px;">' + escapeHtml(cf.justification) + '</span>' : '') +
          '</div><span class="num"><b>' + (cf.score != null ? cf.score.toFixed(1) : '—') + ' / 5.0</b></span></div>\n';
      });
      bHtml += '</div>\n';
    }

    // Turn-by-Turn Naturalness
    if (r.turn_naturalness_detail && r.turn_naturalness_detail.length > 0) {
      bHtml += '<h4>Turn-by-Turn Naturalness</h4><div style="margin:8px 0;">\n';
      r.turn_naturalness_detail.forEach(function(t) {
        var badgeCls = (t.label || "").toLowerCase().replace(/[^a-z0-9]+/g, "_");
        bHtml += '<div class="turn-diag-card">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;">' +
          '<b>Turn #' + t.turn_index + '</b>' +
          '<div><span class="num" style="margin-right:6px;">' + (t.score != null ? t.score.toFixed(1) + '/5.0' : '—') + '</span><span class="badge ' + badgeCls + '">' + escapeHtml(t.label) + '</span></div>' +
          '</div>' +
          (t.justification ? '<p style="margin:4px 0 0;font-size:12px;color:var(--text-muted);">' + escapeHtml(t.justification) + '</p>' : '') +
          (t.low_factors && t.low_factors.length > 0 ? '<div style="margin-top:4px;">' + t.low_factors.map(function(lf) { return '<span class="flag bad">low: ' + escapeHtml(lf) + '</span>'; }).join(" ") + '</div>' : '') +
          '</div>\n';
      });
      bHtml += '</div>\n';
    }

    // Transcript
    bHtml += '<h4>Transcript</h4><div class="tx">\n';
    if (r.transcript_messages && r.transcript_messages.length > 0) {
      r.transcript_messages.forEach(function(tm) {
        var who = tm[0];
        var text = tm[1];
        if (who === "caller" || who === "user") {
          bHtml += '<div class="msg caller"><span class="who">Caller</span>' + hlText(text, q) + '</div>\n';
        } else if (who === "agent") {
          bHtml += '<div class="msg agent"><span class="who">Agent</span>' + hlText(text, q) + '</div>\n';
        } else {
          bHtml += '<div class="msg event">' + hlText(text, q) + '</div>\n';
        }
      });
    } else {
      bHtml += '<span style="color:var(--text-muted);font-size:12px;">No transcript available.</span>\n';
    }
    bHtml += '</div>\n';

    drB.innerHTML = bHtml;
    drB.scrollTop = 0;
  }

  var drawer = document.getElementById("audio-conv-drawer");
  var scrim = document.getElementById("audio-conv-scrim");
  if (drawer) {
    drawer.classList.add("on");
    drawer.setAttribute("aria-hidden", "false");
  }
  if (scrim) scrim.classList.add("on");

  document.querySelectorAll("#audio-runs-table tr.sel").forEach(function(el) {
    el.classList.remove("sel");
  });
  var curTr = document.querySelector("#audio-runs-table tr[data-row-id='" + id + "']");
  if (curTr) curTr.classList.add("sel");
}

function closeAudioConv() {
  AUD_S.open = null;
  var drawer = document.getElementById("audio-conv-drawer");
  var scrim = document.getElementById("audio-conv-scrim");
  if (drawer) {
    var audios = drawer.querySelectorAll("audio");
    audios.forEach(function(a) {
      try { a.pause(); } catch (e) {}
    });
    drawer.classList.remove("on");
    drawer.setAttribute("aria-hidden", "true");
  }
  if (scrim) scrim.classList.remove("on");
  document.querySelectorAll("#audio-runs-table tr.sel").forEach(function(el) {
    el.classList.remove("sel");
  });
}

function stepAudioConv(dir) {
  var rows = window.__audioFilteredRows || ((window.__audioConvData && window.__audioConvData.run_rows) || []);
  if (!rows || rows.length === 0) return;
  var idx = rows.findIndex(function(r) { return (r.id === AUD_S.open || r.row_id === AUD_S.open); });
  if (idx < 0) idx = 0;
  var nextIdx = idx + dir;
  if (nextIdx >= 0 && nextIdx < rows.length) {
    var nextRow = rows[nextIdx];
    openAudioConv(nextRow.id || nextRow.row_id);
  }
}

function checkHashTab() {
  var hash = window.location.hash || "";
  var tMatch = hash.match(/[#&]t=([a-z0-9_-]+)/i);
  if (tMatch && tMatch[1]) {
    switchTab(tMatch[1]);
    return;
  }
  if (hash === "#section-latency" || hash === "#latency") {
    switchTab("latency");
    openSuiteLatency();
  } else if (hash === "#section-quality" || hash === "#quality" || hash === "#audio") {
    switchTab("quality");
  } else if (hash === "#section-conversations" || hash === "#conversations") {
    switchTab("conversations");
  } else if (hash === "#section-goldens" || hash === "#goldens") {
    switchTab("goldens");
  } else if (hash === "#section-sims" || hash === "#sims") {
    switchTab("sims");
  } else if (hash === "#section-tools" || hash === "#section-callbacks" || hash === "#unit") {
    switchTab("unit");
  } else if (hash === "#overview") {
    switchTab("overview");
  } else if (hash.startsWith("#eval-")) {
    var el = document.getElementById(hash.substring(1));
    if (el) {
      var pane = el.closest(".tab-pane") || el.closest(".report-tab-panel");
      if (pane) {
        switchTab(pane.id.replace("tab-panel-", "").replace("tab-", ""));
      }
      setTimeout(function() {
        el.scrollIntoView({behavior: "smooth", block: "start"});
        el.classList.add("highlight-target");
      }, 50);
    }
  }
}

function initAudioConversationsApp() {
  var dataEl = document.getElementById("audio-conv-data");
  if (dataEl) {
    try {
      window.__audioConvData = JSON.parse(dataEl.textContent);
    } catch (e) {
      console.error("Failed to parse audio conv data", e);
    }
  }

  saveInitialAudioQualityState();
  renderIntentSel();

  // Keyboard navigation
  if (typeof document !== "undefined") {
    document.addEventListener("keydown", function(e) {
      var tag = (document.activeElement && document.activeElement.tagName) ? document.activeElement.tagName.toLowerCase() : "";
      var isInput = (tag === "input" || tag === "textarea" || tag === "select");

      if (e.key === "Escape") {
        var drawer = document.getElementById("audio-conv-drawer");
        if (drawer && drawer.classList.contains("on")) {
          closeAudioConv();
        }
      } else if (!isInput && (e.key === "j" || e.key === "J")) {
        var drawerJ = document.getElementById("audio-conv-drawer");
        if (drawerJ && drawerJ.classList.contains("on")) {
          stepAudioConv(1);
        }
      } else if (!isInput && (e.key === "k" || e.key === "K")) {
        var drawerK = document.getElementById("audio-conv-drawer");
        if (drawerK && drawerK.classList.contains("on")) {
          stepAudioConv(-1);
        }
      }
    });
  }

  checkHashTab();
}

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", initAudioConversationsApp);
}

// Floating tooltip popover for data-tip
(function() {
  var tipEl = null;

  function getTooltipEl() {
    if (!tipEl) {
      tipEl = document.getElementById("audio-tooltip-pop");
      if (!tipEl) {
        tipEl = document.createElement("div");
        tipEl.id = "audio-tooltip-pop";
        tipEl.className = "audio-tooltip-pop";
        tipEl.setAttribute("role", "tooltip");
        document.body.appendChild(tipEl);
      }
    }
    return tipEl;
  }

  function showTooltip(target) {
    var tipText = target.getAttribute("data-tip");
    if (!tipText) return;
    var el = getTooltipEl();
    el.textContent = tipText;
    el.classList.add("visible");
    positionTooltip(target, el);
  }

  function hideTooltip() {
    if (tipEl) {
      tipEl.classList.remove("visible");
    }
  }

  function positionTooltip(target, el) {
    var rect = target.getBoundingClientRect();
    var popRect = el.getBoundingClientRect();
    var top = rect.top - popRect.height - 8;
    var left = rect.left + (rect.width - popRect.width) / 2;
    if (top < 10) {
      top = rect.bottom + 8;
    }
    if (left < 10) left = 10;
    if (left + popRect.width > window.innerWidth - 10) {
      left = window.innerWidth - popRect.width - 10;
    }
    el.style.top = Math.round(top) + "px";
    el.style.left = Math.round(left) + "px";
  }

  if (typeof document !== "undefined") {
    document.addEventListener("mouseover", function(e) {
      var target = e.target.closest("[data-tip]");
      if (target) {
        showTooltip(target);
      }
    });

    document.addEventListener("mouseout", function(e) {
      var target = e.target.closest("[data-tip]");
      if (target) {
        hideTooltip();
      }
    });

    document.addEventListener("focusin", function(e) {
      var target = e.target.closest("[data-tip]");
      if (target) {
        showTooltip(target);
      }
    });

    document.addEventListener("focusout", function(e) {
      var target = e.target.closest("[data-tip]");
      if (target) {
        hideTooltip();
      }
    });
  }
})();

function filterEvals(query) {
  var q = (query || "").toLowerCase().trim();
  document.querySelectorAll("tr[data-passed]").forEach(function(row) {
    var text = (row.textContent || "").toLowerCase();
    var match = !q || text.indexOf(q) !== -1;
    row.style.display = match ? "" : "none";
  });
  document.querySelectorAll(".eval-card").forEach(function(card) {
    var text = (card.textContent || "").toLowerCase();
    var match = !q || text.indexOf(q) !== -1;
    card.style.display = match ? "" : "none";
  });
}

function toggleIntentPop(e) {
  if (e) {
    e.stopPropagation();
    e.preventDefault();
  }
  var pop = document.getElementById("intent-pop");
  var btn = document.getElementById("intent-btn");
  if (!pop || !btn) return;
  var isOpen = pop.classList.contains("open") || pop.classList.contains("on");
  if (isOpen) {
    pop.classList.remove("open", "on");
    btn.setAttribute("aria-expanded", "false");
  } else {
    renderIntentSel();
    pop.classList.add("open", "on");
    btn.setAttribute("aria-expanded", "true");
  }
}

function updateIntentBtn() {
  var btn = document.getElementById("intent-btn");
  if (!btn || !window.__audioConvData || !window.__audioConvData.run_rows) return;
  var allRuns = window.__audioConvData.run_rows;
  var allKeys = [];
  var seen = {};
  allRuns.forEach(function(r) {
    var it = r.intent || "unknown";
    if (!seen[it]) {
      seen[it] = true;
      allKeys.push(it);
    }
  });

  if (AUD_S.intents === null) {
    AUD_S.intents = allKeys.slice();
  }

  var selCount = AUD_S.intents.length;
  var isAll = (allKeys.length > 0 && selCount === allKeys.length);

  if (isAll) {
    btn.classList.remove("on");
    btn.innerHTML = '<span>All intents · ' + allRuns.length + '</span><span class="arr">▾</span>';
  } else if (selCount === 0) {
    btn.classList.add("on");
    btn.innerHTML = '<span>0 intents · 0</span><span class="arr">▾</span>';
  } else if (selCount === 1) {
    var itKey = AUD_S.intents[0];
    var matchedCount = allRuns.filter(function(r) { return (r.intent || "unknown") === itKey; }).length;
    btn.classList.add("on");
    btn.innerHTML = '<span>' + escapeHtml(humanizeIntent(itKey)) + ' · ' + matchedCount + '</span><span class="arr">▾</span>';
  } else {
    var matchedCount = allRuns.filter(function(r) { return AUD_S.intents.indexOf(r.intent || "unknown") !== -1; }).length;
    btn.classList.add("on");
    btn.innerHTML = '<span>' + selCount + ' intents · ' + matchedCount + '</span><span class="arr">▾</span>';
  }
}

var _initialAudioQualityState = null;

function saveInitialAudioQualityState() {
  if (_initialAudioQualityState) return;
  var gpaEl = document.getElementById("gpa");
  var stackEl = document.getElementById("grade-stack");
  var legendEl = document.getElementById("grade-legend");
  var navBadge = document.getElementById("tab-audio-n") || document.querySelector('#report-nav button[data-tab="quality"] .c, .report-tabs-nav button[data-tab="quality"] .c');
  var ovTilesEl = document.getElementById("overview-tiles");
  if (!gpaEl && !stackEl && !legendEl && !ovTilesEl) return;

  var gradeTips = {};
  if (legendEl) {
    var btns = legendEl.querySelectorAll("button[data-audio-filter-grade]");
    btns.forEach(function(b) {
      var gk = b.getAttribute("data-audio-filter-grade");
      if (gk) gradeTips[gk] = b.getAttribute("data-tip") || "";
    });
  }

  _initialAudioQualityState = {
    gpaHtml: gpaEl ? gpaEl.innerHTML : "",
    gpaTip: gpaEl ? (gpaEl.getAttribute("data-tip") || "") : "",
    stackHtml: stackEl ? stackEl.innerHTML : "",
    legendHtml: legendEl ? legendEl.innerHTML : "",
    navBadgeText: navBadge ? navBadge.textContent : "",
    ovTilesHtml: ovTilesEl ? ovTilesEl.innerHTML : "",
    gradeTips: gradeTips
  };
}

function getGpaScore(score, label) {
  if (score === null || score === undefined || isNaN(score)) return null;
  if (score >= 4.0 || label === "Human-like") return 4.0;
  if (score >= 3.5) return 3.0;
  if (score >= 2.5 || label === "Transitional") return 2.0;
  if (score >= 1.5) return 1.0;
  return 0.0;
}

function updateAudioQualitySummary(selectedIntents) {
  saveInitialAudioQualityState();
  if (!_initialAudioQualityState) return;

  var gpaEl = document.getElementById("gpa");
  var stackEl = document.getElementById("grade-stack");
  var legendEl = document.getElementById("grade-legend");
  var navBadge = document.getElementById("tab-audio-n") || document.querySelector('#report-nav button[data-tab="quality"] .c, .report-tabs-nav button[data-tab="quality"] .c');
  var ovTilesEl = document.getElementById("overview-tiles");

  var allRuns = (window.__audioConvData && window.__audioConvData.run_rows) || [];
  var allKeys = [];
  var seen = {};
  allRuns.forEach(function(r) {
    var it = r.intent || "unknown";
    if (!seen[it]) {
      seen[it] = true;
      allKeys.push(it);
    }
  });

  // Check if all intents are selected or unfiltered
  var isAll = false;
  if (selectedIntents === null || selectedIntents === undefined) {
    isAll = true;
  } else if (allKeys.length > 0 && selectedIntents.length === allKeys.length) {
    isAll = allKeys.every(function(k) { return selectedIntents.indexOf(k) !== -1; });
  }

  if (isAll) {
    if (gpaEl) gpaEl.innerHTML = _initialAudioQualityState.gpaHtml;
    if (stackEl) stackEl.innerHTML = _initialAudioQualityState.stackHtml;
    if (legendEl) legendEl.innerHTML = _initialAudioQualityState.legendHtml;
    if (navBadge) navBadge.textContent = _initialAudioQualityState.navBadgeText;
    if (ovTilesEl) ovTilesEl.innerHTML = _initialAudioQualityState.ovTilesHtml;
    return;
  }

  // Filter runs for the active subset of intents
  var filteredRuns = allRuns.filter(function(r) {
    var it = r.intent || "unknown";
    return selectedIntents.indexOf(it) !== -1;
  });

  var totalRuns = filteredRuns.length;
  var gradedCount = 0;
  var natScores = [];
  var gpaScores = [];
  var gradeCounts = { human_like: 0, transitional: 0, bot_like: 0 };

  filteredRuns.forEach(function(r) {
    var isGraded = (r.is_graded !== undefined) ? r.is_graded : (r.score !== null && r.score !== undefined);
    if (isGraded) {
      gradedCount++;
    }
    if (r.score !== null && r.score !== undefined) {
      var s = Number(r.score);
      natScores.push(s);
      var gpa = getGpaScore(s, r.grade_label);
      if (gpa !== null) gpaScores.push(gpa);
      var gk = r.grade_key;
      if (gk && gradeCounts.hasOwnProperty(gk)) {
        gradeCounts[gk]++;
      }
    }
  });

  var meanNat = natScores.length > 0 ? (natScores.reduce(function(a, b) { return a + b; }, 0) / natScores.length) : null;
  var meanGpa = gpaScores.length > 0 ? (gpaScores.reduce(function(a, b) { return a + b; }, 0) / gpaScores.length) : null;
  var meanScoreStr = meanNat !== null ? meanNat.toFixed(2) : "—";
  var gpaStr = meanGpa !== null ? meanGpa.toFixed(2) : "—";

  var gpaSubTip = _initialAudioQualityState.gpaTip || (gpaEl && gpaEl.getAttribute("data-tip")) || "";
  var gpaMeta = gradedCount + " of " + totalRuns + " runs graded \u00B7 Mean Naturalness " + meanScoreStr + "/5.0 \u00B7 GPA " + gpaStr + " / 4";
  if (gpaEl) {
    gpaEl.innerHTML = escapeHtml(gpaMeta) + (gpaSubTip ? ' <span class="tip-badge" data-tip="' + escapeHtml(gpaSubTip) + '">?</span>' : '');
  }
  if (navBadge) {
    navBadge.textContent = String(gradedCount);
  }

  var gradeDenom = natScores.length || gradedCount || 0;
  var bands = [
    { key: "human_like", label: "Human-like (4.0–5.0)", color: "var(--good)", count: gradeCounts.human_like },
    { key: "transitional", label: "Transitional (2.5–3.9)", color: "var(--warn)", count: gradeCounts.transitional },
    { key: "bot_like", label: "Bot-like (<2.5)", color: "var(--critical)", count: gradeCounts.bot_like }
  ];
  bands.forEach(function(b) {
    b.pct = gradeDenom ? Math.round(b.count / gradeDenom * 100) : 0;
    b.tip = (_initialAudioQualityState.gradeTips && _initialAudioQualityState.gradeTips[b.key]) || "";
  });

  if (stackEl) {
    var stackHtml = "";
    var anyGraded = false;
    bands.forEach(function(b) {
      if (b.count > 0) {
        anyGraded = true;
        stackHtml += '<div class="stack-seg" style="flex:' + b.count + ';background:' + b.color + '" title="' + escapeHtml(b.label) + ': ' + b.count + ' runs (' + b.pct + '%)" data-tip="' + escapeHtml(b.tip) + '" onclick="filterAudioRunsByGrade(\'' + b.key + '\')"></div>';
      }
    });
    if (!anyGraded) {
      stackHtml = '<div class="stack-seg" style="flex:1;background:var(--bg-subtle)" title="No runs graded"></div>';
    }
    stackEl.innerHTML = stackHtml;
  }

  if (legendEl) {
    var legendHtml = "";
    bands.forEach(function(b) {
      legendHtml += '<button type="button" data-audio-filter-grade="' + b.key + '" data-tip="' + escapeHtml(b.tip) + '" onclick="filterAudioRunsByGrade(\'' + b.key + '\')">' +
                    '<span class="sw" style="background:' + b.color + '"></span>' +
                    '<div><b class="num">' + b.pct + '%</b><span>' + escapeHtml(b.label) + ' \u00B7 ' + b.count + '</span></div>' +
                    '</button>';
    });
    legendEl.innerHTML = legendHtml;
  }

  // Update Overview top-line KPI metric tiles
  if (ovTilesEl) {
    function setTile(id, valText, statusCls, subText) {
      var el = document.getElementById(id);
      if (!el) return;
      var vEl = el.querySelector(".t-val");
      if (vEl) {
        vEl.textContent = valText;
        vEl.classList.remove("pass", "warn", "fail");
        if (statusCls) vEl.classList.add(statusCls);
      }
      var sEl = el.querySelector(".t-sub");
      if (sEl) {
        sEl.textContent = subText;
      }
    }

    // 1. Audio Naturalness
    var natCls = meanNat !== null ? (meanNat >= 4.0 ? "pass" : (meanNat >= 2.5 ? "warn" : "fail")) : "";
    setTile("ov-tile-nat", meanScoreStr + "/5", natCls, "GPA " + gpaStr + "/4.0 \u00B7 " + gradedCount + "/" + totalRuns + " graded");

    // 2. Human-like Runs
    var hbCount = gradeCounts.human_like;
    var hbPct = gradeDenom ? Math.round(hbCount / gradeDenom * 100) : 0;
    var hbCls = hbPct >= 75 ? "pass" : (hbPct >= 50 ? "warn" : "fail");
    setTile("ov-tile-human", hbPct + "%", hbCls, hbCount + " / " + (gradedCount || 0) + " graded runs (\u22654.0)");

    // 3. Audio checks
    var chkPass = filteredRuns.filter(function(r) { return r.audio_checks_status === "PASS"; }).length;
    var chkTotal = filteredRuns.filter(function(r) { return r.audio_checks_status === "PASS" || r.audio_checks_status === "FAIL"; }).length;
    var chkPct = chkTotal ? Math.round(100 * chkPass / chkTotal) : 0;
    var chkCls = chkPct >= 90 ? "pass" : (chkPct >= 70 ? "warn" : "fail");
    setTile("ov-tile-checks", chkPct + "%", chkCls, chkPass + " / " + chkTotal + " runs passed");

    // 4. Acoustic
    var acRuns = filteredRuns.filter(function(r) {
      return r.acoustic_measured !== undefined ? r.acoustic_measured === true : true;
    });
    var acClean = acRuns.filter(function(r) {
      var ik = r.issue_keys || [];
      return ik.indexOf("tts_cache_seam") === -1 && ik.indexOf("loudness_jump") === -1 && ik.indexOf("midturn_voice_seam") === -1 && ik.indexOf("audio_truncation") === -1;
    }).length;
    var acTotal = acRuns.length;
    var acPct = acTotal ? Math.round(100 * acClean / acTotal) : 100;
    var acCls = acPct >= 90 ? "pass" : (acPct >= 70 ? "warn" : "fail");
    setTile("ov-tile-acoustic", acPct + "%", acCls, acClean + " / " + acTotal + " drift-free");

    // 5. WER
    var werRuns = filteredRuns.filter(function(r) { return r.wer !== null && r.wer !== undefined; });
    var meanWer = werRuns.length ? (werRuns.reduce(function(a, b) { return a + Number(b.wer); }, 0) / werRuns.length) : null;
    var highWer = werRuns.filter(function(r) { return Number(r.wer) > 0.25; }).length;
    var werVal = meanWer !== null ? (meanWer * 100).toFixed(1) + "%" : "—";
    var werCls = meanWer !== null ? (meanWer <= 0.10 ? "pass" : (meanWer <= 0.25 ? "warn" : "fail")) : "";
    setTile("ov-tile-wer", werVal, werCls, highWer + " high (>25%) \u00B7 " + werRuns.length + " runs");

    // 6. Language
    var misRuns = filteredRuns.filter(function(r) { return r.language_mismatch === true; }).length;
    var matchRuns = totalRuns - misRuns;
    var langPct = totalRuns ? Math.round(100 * matchRuns / totalRuns) : 0;
    var langCls = misRuns === 0 ? "pass" : (langPct >= 85 ? "warn" : "fail");
    setTile("ov-tile-lang", langPct + "%", langCls, matchRuns + " / " + totalRuns + " matched (" + misRuns + " mismatch)");

    // 7. Barge-in
    var bargeTurns = filteredRuns.reduce(function(a, b) { return a + (Number(b.barge_turns) || 0); }, 0);
    var bargeSess = filteredRuns.filter(function(r) { return (Number(r.barge_turns) || 0) > 0; }).length;
    var totTurns = filteredRuns.reduce(function(a, b) { return a + (Number(b.turns_count) || 0); }, 0);
    var bRate = totTurns ? (bargeTurns / totTurns) : 0;
    var bCls = bRate > 0.30 ? "warn" : "pass";
    setTile("ov-tile-barge", (bRate * 100).toFixed(1) + "%", bCls, bargeTurns + " turns \u00B7 " + bargeSess + " sessions");

    // 8. Avg # LLM Turns
    var totLlmTurns = filteredRuns.reduce(function(a, b) {
      var lt = (b.llm_turns_count !== undefined && b.llm_turns_count !== null) ? Number(b.llm_turns_count) : (Number(b.turns_count) || 1);
      return a + lt;
    }, 0);
    var avgLlmTurns = totalRuns ? (totLlmTurns / totalRuns) : 0;
    var avgConvTurns = totalRuns ? (totTurns / totalRuns) : 0;
    setTile("ov-tile-llm-turns", totalRuns ? avgLlmTurns.toFixed(1) : "—", "", totLlmTurns + " total \u00B7 " + avgConvTurns.toFixed(1) + " user turns/run");

    // 9. Perceived Latency
    var plRuns = filteredRuns.map(function(r) { return r.pl_p50_ms; }).filter(function(v) { return v !== null && v !== undefined; });
    if (plRuns.length > 0) {
      var sortedPl = plRuns.slice().sort(function(a, b) { return a - b; });
      var medPl = sortedPl[Math.floor(sortedPl.length / 2)];
      var p90Pl = sortedPl[Math.floor(sortedPl.length * 0.9)];
      var avgPl = sortedPl.reduce(function(a, b) { return a + b; }, 0) / sortedPl.length;
      var plCls = medPl <= 1500 ? "pass" : (medPl <= 2500 ? "warn" : "fail");
      setTile("ov-tile-pl", Math.round(medPl) + " ms", plCls, "p90: " + Math.round(p90Pl) + " ms \u00B7 Mean: " + Math.round(avgPl) + " ms");
    }
  }
}

function renderIntentSel() {
  var pop = document.getElementById("intent-pop");
  if (!pop || !window.__audioConvData || !window.__audioConvData.run_rows) return;
  var allRuns = window.__audioConvData.run_rows;
  var countsByIntent = {};
  allRuns.forEach(function(r) {
    var it = r.intent || "unknown";
    countsByIntent[it] = (countsByIntent[it] || 0) + 1;
  });
  var sortedIntents = Object.keys(countsByIntent).sort(function(a, b) {
    return countsByIntent[b] - countsByIntent[a];
  });

  if (AUD_S.intents === null) {
    AUD_S.intents = sortedIntents.slice();
  }

  var allChecked = sortedIntents.length > 0 && sortedIntents.every(function(k) {
    return AUD_S.intents.indexOf(k) !== -1;
  });
  var someChecked = !allChecked && AUD_S.intents.length > 0;

  var html = '<label class="intent-item all-opt' + (allChecked ? ' all-selected' : (someChecked ? ' some-selected' : '')) + '" onclick="event.stopPropagation();" title="' + (allChecked ? 'Deselect all' : 'Select all') + '">' +
             '<input type="checkbox" id="intent-master-cb" ' + (allChecked ? 'checked' : '') + ' onchange="toggleIntent(\'all\')"> ' +
             '<span>All intents</span><span class="c">' + allRuns.length + '</span></label>';

  sortedIntents.forEach(function(it) {
    var isChecked = AUD_S.intents.indexOf(it) !== -1;
    var safeKey = it.replace(/'/g, "\\'");
    html += '<label class="intent-item" onclick="event.stopPropagation();">' +
            '<input type="checkbox" ' + (isChecked ? 'checked' : '') + ' onchange="toggleIntent(\'' + safeKey + '\')"> ' +
            '<span>' + escapeHtml(humanizeIntent(it)) + '</span><span class="c">' + countsByIntent[it] + '</span></label>';
  });
  pop.innerHTML = html;
  var masterCb = document.getElementById("intent-master-cb");
  if (masterCb) {
    masterCb.indeterminate = someChecked;
  }
  updateIntentBtn();
}

function selectAllIntents(selectAll) {
  var allRuns = (window.__audioConvData && window.__audioConvData.run_rows) || [];
  var allKeys = [];
  var seen = {};
  allRuns.forEach(function(r) {
    var it = r.intent || "unknown";
    if (!seen[it]) {
      seen[it] = true;
      allKeys.push(it);
    }
  });

  if (selectAll) {
    AUD_S.intents = allKeys.slice();
    AUD_S.g.intent = [];
  } else {
    AUD_S.intents = [];
    AUD_S.g.intent = ["__none__"];
  }

  AUD_S.page = 0;
  updateIntentBtn();
  renderIntentSel();
  renderAudioConversations();
  updateAudioQualitySummary(AUD_S.intents);
}
window.selectAllIntents = selectAllIntents;

function toggleIntent(key) {
  var allRuns = (window.__audioConvData && window.__audioConvData.run_rows) || [];
  var allKeys = [];
  var seen = {};
  allRuns.forEach(function(r) {
    var it = r.intent || "unknown";
    if (!seen[it]) {
      seen[it] = true;
      allKeys.push(it);
    }
  });

  if (AUD_S.intents === null) {
    AUD_S.intents = allKeys.slice();
  }

  if (key === 'all') {
    var isAll = allKeys.length > 0 && allKeys.every(function(k) {
      return AUD_S.intents.indexOf(k) !== -1;
    });
    selectAllIntents(!isAll);
    return;
  } else {
    var idx = AUD_S.intents.indexOf(key);
    if (idx !== -1) {
      AUD_S.intents.splice(idx, 1);
    } else {
      AUD_S.intents.push(key);
    }
  }

  if (AUD_S.intents.length === allKeys.length) {
    AUD_S.g.intent = [];
  } else if (AUD_S.intents.length === 0) {
    AUD_S.g.intent = ["__none__"];
  } else {
    AUD_S.g.intent = AUD_S.intents.slice();
  }

  AUD_S.page = 0;
  updateIntentBtn();
  renderIntentSel();
  renderAudioConversations();
  updateAudioQualitySummary(AUD_S.intents);
}

// Global initialization
window.addEventListener("DOMContentLoaded", function() {
  saveInitialAudioQualityState();
  function handleHash() {
    checkHashTab();
  }

  handleHash();
  window.addEventListener("hashchange", handleHash);

  if (typeof initAudioData === "function") {
    initAudioData();
  }
  updateIntentBtn();

  var searchInput = document.getElementById("q");
  if (searchInput) {
    searchInput.addEventListener("input", function(e) {
      filterEvals(e.target.value);
    });
  }

  var audioSearchInput = document.getElementById("audio-conv-search");

  document.addEventListener("click", function(e) {
    var pop = document.getElementById("intent-pop");
    var btn = document.getElementById("intent-btn");
    if (pop && (pop.classList.contains("open") || pop.classList.contains("on")) && !pop.contains(e.target) && (!btn || !btn.contains(e.target))) {
      pop.classList.remove("open", "on");
      if (btn) btn.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", function(e) {
    if (e.key === "/" && document.activeElement !== searchInput && document.activeElement !== audioSearchInput) {
      var tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag !== "INPUT" && tag !== "TEXTAREA") {
        e.preventDefault();
        var activeTabBtn = document.querySelector("#report-nav button.active, .report-tabs-nav button.active");
        var activeTab = activeTabBtn ? activeTabBtn.dataset.tab : "overview";
        if (activeTab === "conversations" && audioSearchInput) {
          audioSearchInput.focus();
          audioSearchInput.select();
        } else if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
      }
    }
  });

  function syncAudioTableTop() {
    var topEl = document.querySelector(".top");
    if (topEl) {
      document.documentElement.style.setProperty("--top-h", topEl.offsetHeight + "px");
    }
  }
  window.addEventListener("resize", syncAudioTableTop);
  syncAudioTableTop();
});

// CES stereo audio player (lazy Blob URL decoding, channel solo, chapter seek)
var _cesAudioCtx = null;
var _cesAudioGraphs = {};

function audioEl(id) {
  var el = document.getElementById("aud_" + id);
  if (el && !el.getAttribute("src")) {
    var s = document.getElementById("blob_" + id);
    if (s) {
      var bin = atob(s.textContent.trim());
      var a = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) {
        a[i] = bin.charCodeAt(i);
      }
      el.src = URL.createObjectURL(new Blob([a], { type: s.dataset.mime }));
    }
  }
  return el;
}

function seekAudio(id, sec) {
  var el = audioEl(id);
  if (!el) return;
  var go = function() {
    el.currentTime = sec;
    el.play();
  };
  if (el.readyState >= 1) {
    go();
  } else {
    el.addEventListener("loadedmetadata", go, { once: true });
    el.load();
  }
}

function setChannel(id, mode) {
  var el = audioEl(id);
  if (!el) return;
  if (!_cesAudioCtx) {
    _cesAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  var g = _cesAudioGraphs[id];
  if (!g) {
    var src = _cesAudioCtx.createMediaElementSource(el);
    var sp = _cesAudioCtx.createChannelSplitter(2);
    var m = _cesAudioCtx.createChannelMerger(2);
    var l = _cesAudioCtx.createGain();
    var r = _cesAudioCtx.createGain();
    src.connect(sp);
    sp.connect(l, 0);
    sp.connect(r, 1);
    [l, r].forEach(function(n) {
      n.connect(m, 0, 0);
      n.connect(m, 0, 1);
    });
    m.connect(_cesAudioCtx.destination);
    g = _cesAudioGraphs[id] = { l: l, r: r };
  }
  _cesAudioCtx.resume();
  g.l.gain.value = (mode === "agent") ? 0 : 1;
  g.r.gain.value = (mode === "caller") ? 0 : 1;
  document.querySelectorAll('[data-chan-for="' + id + '"]').forEach(function(b) {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
}

function initCesAudioObservers() {
  var cards = document.querySelectorAll("[data-audio-id]");
  if (!cards.length) return;
  if (!("IntersectionObserver" in window)) {
    cards.forEach(function(c) {
      audioEl(c.dataset.audioId);
    });
    return;
  }
  var io = new IntersectionObserver(function(es) {
    es.forEach(function(e) {
      if (e.isIntersecting) {
        audioEl(e.target.dataset.audioId);
        io.unobserve(e.target);
      }
    });
  }, { rootMargin: "400px" });
  cards.forEach(function(c) {
    io.observe(c);
  });
}

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", initCesAudioObservers);
}
