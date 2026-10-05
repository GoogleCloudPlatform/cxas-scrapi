var failuresOnly = false;
var latencyExpanded = false;

function toggleTheme() {
  var isDark = document.body.classList.contains("dark-mode") ||
      (!document.body.classList.contains("light-mode") &&
       window.matchMedia &&
       window.matchMedia("(prefers-color-scheme: dark)").matches);
  if (isDark) {
    document.body.classList.remove("dark-mode");
    document.body.classList.add("light-mode");
  } else {
    document.body.classList.remove("light-mode");
    document.body.classList.add("dark-mode");
  }
}

function openSuiteLatency() {
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
  var card = document.getElementById("eval-" + type + "-" + evalName) ||
      document.getElementById("eval-" + evalName);
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
  }, 50);
}

function jumpToRun(type, evalName, runIdx) {
  if (runIdx === undefined) {
    runIdx = evalName;
    evalName = type;
    type = "sim";
  }
  var card = document.getElementById("eval-" + type + "-" + evalName) ||
      document.getElementById("eval-" + evalName);
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
  document.querySelectorAll(".eval-card[data-passed]").forEach(function(card) {
    if (failuresOnly && card.dataset.passed === "true") {
      card.classList.add("hidden-card");
    } else {
      card.classList.remove("hidden-card");
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
