var failuresOnly = false;

function jumpTo(type, evalName) {
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
  btn.classList.toggle("active", failuresOnly);
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
  document.querySelectorAll("details").forEach(function(d) {
    d.setAttribute("open", "");
  });
}

function collapseAll() {
  document.querySelectorAll("details").forEach(function(d) {
    d.removeAttribute("open");
  });
}
