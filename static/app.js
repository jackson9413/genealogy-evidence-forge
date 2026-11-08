(function () {
  "use strict";

  var state = {
    result: null,
    sessionId: null,
    tab: "people",
    severity: "all",
    axis: "all",
    samples: []
  };

  function $(sel) { return document.querySelector(sel); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function clear(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  function scoreBand(score) {
    if (score >= 85) { return "good"; }
    if (score >= 70) { return "good"; }
    if (score >= 55) { return "warn"; }
    if (score >= 40) { return "warn"; }
    return "bad";
  }

  function verdictBand(verdict) {
    if (verdict === "research_ready") { return "good"; }
    if (verdict === "solid_progress") { return "good"; }
    if (verdict === "needs_work") { return "warn"; }
    if (verdict === "thin_evidence") { return "warn"; }
    return "bad";
  }

  function severityPill(sev) {
    var map = { high: "pill-bad", medium: "pill-warn", low: "pill-info" };
    return el("span", "pill " + (map[sev] || "pill-muted"), sev);
  }

  function pill(text, cls) { return el("span", "pill " + cls, text); }

  function bar(score, color) {
    var wrap = el("div", "bar");
    var fill = el("span");
    fill.style.width = Math.max(2, Math.min(100, score)) + "%";
    fill.style.background = color;
    wrap.appendChild(fill);
    return wrap;
  }

  function colorFor(score) {
    if (score >= 75) { return "var(--good)"; }
    if (score >= 50) { return "var(--warn)"; }
    return "var(--bad)";
  }

  function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); });
  }

  function patchJSON(url, payload) {
    return fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); });
  }

  // ---------------------------------------------------------------- boot
  function init() {
    fetch("/api/health").then(function (r) { return r.json(); }).then(function (data) {
      var node = $("#health");
      node.className = "pill " + (data.ok ? "pill-good" : "pill-bad");
      node.textContent = data.ok ? "engine online" : "engine offline";
    }).catch(function () {
      var node = $("#health");
      node.className = "pill pill-bad";
      node.textContent = "engine offline";
    });

    fetch("/api/samples").then(function (r) { return r.json(); }).then(function (samples) {
      state.samples = samples;
      var sel = $("#sample");
      samples.forEach(function (s) {
        var opt = el("option", null, s.title);
        opt.value = s.id;
        sel.appendChild(opt);
      });
    });

    $("#btn-analyze").addEventListener("click", doAnalyze);
    $("#btn-clear").addEventListener("click", function () {
      $("#notes").value = "";
      $("#results").classList.add("hidden");
      $("#empty-state").classList.remove("hidden");
      $("#export-row").classList.add("hidden");
    });
    $("#btn-sample").addEventListener("click", function () {
      var id = $("#sample").value;
      var found = null;
      state.samples.forEach(function (s) { if (s.id === id) { found = s; } });
      if (found) {
        $("#notes").value = found.text;
        doAnalyze();
      }
    });
    $("#btn-theme").addEventListener("click", function () {
      document.body.classList.toggle("light");
      this.textContent = document.body.classList.contains("light") ? "Dark mode" : "Light mode";
    });
    $("#notes").addEventListener("keydown", function (ev) {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") { doAnalyze(); }
    });

    loadHistory();
  }

  // ------------------------------------------------------------- analyze
  function doAnalyze() {
    var text = $("#notes").value;
    if (!text.trim()) {
      $("#results").classList.remove("hidden");
      $("#empty-state").classList.add("hidden");
      clear($("#results"));
      $("#results").appendChild(el("p", "hint", "Paste some notes first - or load a sample."));
      return;
    }
    var btn = $("#btn-analyze");
    btn.disabled = true;
    btn.textContent = "Analysing...";
    postJSON("/api/analyze", { text: text, mode: $("#mode").value, save: true })
      .then(function (data) {
        if (data.error) { throw new Error(data.error); }
        state.result = data.result;
        state.sessionId = data.session_id;
        render(data.result);
        wireExports(data.session_id);
        loadHistory();
      })
      .catch(function (err) {
        $("#results").classList.remove("hidden");
        $("#empty-state").classList.add("hidden");
        clear($("#results"));
        $("#results").appendChild(el("p", "hint", "Could not analyse: " + err.message));
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = "Analyse";
      });
  }

  function wireExports(id) {
    if (!id) { return; }
    $("#export-row").classList.remove("hidden");
    $("#dl-md").href = "/api/export/" + id + "?fmt=md";
    $("#dl-csv").href = "/api/export/" + id + "?fmt=csv";
    $("#dl-json").href = "/api/export/" + id + "?fmt=json";
  }

  // -------------------------------------------------------------- render
  function render(result) {
    $("#empty-state").classList.add("hidden");
    $("#results").classList.remove("hidden");
    var root = $("#results");
    clear(root);

    var head = el("div", "score-head");
    var big = el("div", "score-big", result.overall);
    big.style.color = colorFor(result.overall);
    head.appendChild(big);

    var meta = el("div", "score-meta");
    var title = el("h3", null, result.verdict_label);
    meta.appendChild(title);
    var counts = result.counts;
    meta.appendChild(el("p", null,
      counts.records + (counts.records === 1 ? " person" : " people") + " - " +
      counts.events + " events - " + counts.sources + " citations - " +
      counts.findings + " findings - " + counts.plan_items + " next steps"));
    meta.appendChild(pill(result.mode_label, "pill-muted"));
    head.appendChild(meta);
    head.appendChild(pill(result.verdict.replace(/_/g, " "), "pill-" + verdictBand(result.verdict)));
    root.appendChild(head);

    if (result.caps.length) {
      var caps = el("div", "card");
      caps.appendChild(el("h4", null, "Structural caps applied"));
      var ul = el("ul", "note-list");
      result.caps.forEach(function (c) { ul.appendChild(el("li", null, c)); });
      caps.appendChild(ul);
      root.appendChild(caps);
    }

    if (result.strengths.length) {
      var st = el("div", "card");
      st.appendChild(el("h4", null, "What is already solid"));
      var sul = el("ul", "note-list");
      result.strengths.forEach(function (s) { sul.appendChild(el("li", null, s)); });
      st.appendChild(sul);
      root.appendChild(st);
    }

    root.appendChild(renderAxes(result));

    var tabs = el("div", "tabs");
    var defs = [
      ["people", "People", result.records.length],
      ["findings", "Findings", result.findings.length],
      ["plan", "Research plan", result.plan.length],
      ["identity", "Identity", result.identity.length],
      ["collections", "Collections", result.collections.length]
    ];
    defs.forEach(function (d) {
      var b = el("button", "tab" + (state.tab === d[0] ? " active" : ""), d[1]);
      b.appendChild(el("span", "count", String(d[2])));
      b.addEventListener("click", function () {
        state.tab = d[0];
        render(state.result);
      });
      tabs.appendChild(b);
    });
    root.appendChild(tabs);

    var panel = el("div");
    if (state.tab === "people") { panel = renderPeople(result); }
    else if (state.tab === "findings") { panel = renderFindings(result); }
    else if (state.tab === "plan") { panel = renderPlan(result); }
    else if (state.tab === "identity") { panel = renderIdentity(result); }
    else { panel = renderCollections(result); }
    root.appendChild(panel);
  }

  function renderAxes(result) {
    var grid = el("div", "axis-grid");
    result.axes.forEach(function (ax) {
      var box = el("div", "axis");
      var top = el("div", "axis-top");
      top.appendChild(el("span", "axis-name", ax.label));
      var sc = el("span", "axis-score", ax.score);
      sc.style.color = colorFor(ax.score);
      top.appendChild(sc);
      box.appendChild(top);
      box.appendChild(bar(ax.score, colorFor(ax.score)));
      box.appendChild(el("div", "axis-desc", ax.description + " (" + Math.round(ax.weight * 100) + "% weight)"));
      grid.appendChild(box);
    });
    return grid;
  }

  function renderPeople(result) {
    var wrap = el("div");
    if (!result.records.length) {
      wrap.appendChild(el("p", "hint", "No person claims were found in the notes."));
      return wrap;
    }
    result.records.forEach(function (rec) {
      var card = el("div", "card");
      var head = el("h4", null, rec.name);
      card.appendChild(head);

      if (rec.events.length) {
        var table = el("table");
        var thead = el("thead");
        var hrow = el("tr");
        ["Event", "Date", "Place", "Evidence"].forEach(function (h) {
          hrow.appendChild(el("th", null, h));
        });
        thead.appendChild(hrow);
        table.appendChild(thead);
        var tbody = el("tbody");
        rec.events.forEach(function (ev) {
          var tr = el("tr");
          tr.appendChild(el("td", null, ev.type));
          tr.appendChild(el("td", "mono", ev.date_display));
          tr.appendChild(el("td", "mono", ev.place_display || "unknown"));
          tr.appendChild(el("td", "mono", ev.sources.length ? ev.sources.join("; ") : "-"));
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        card.appendChild(table);
      } else {
        card.appendChild(el("p", "hint", "No dated events recorded."));
      }

      if (rec.relations.length) {
        var rel = el("p", "hint");
        rel.appendChild(el("strong", null, "Relations: "));
        rel.appendChild(document.createTextNode(rec.relations.map(function (r) {
          return r.type + " " + r.raw;
        }).join(" - ")));
        card.appendChild(rel);
      }

      if (rec.sources.length) {
        var srcHead = el("p", "hint");
        srcHead.appendChild(el("strong", null, "Citations:"));
        card.appendChild(srcHead);
        var sul = el("ul", "note-list");
        rec.sources.forEach(function (s) {
          var li = el("li");
          li.appendChild(document.createTextNode(s.raw + " - "));
          li.appendChild(pill(s.label, s.tier === "primary" ? "pill-good" :
            (s.tier === "secondary" ? "pill-info" :
            (s.tier === "derivative" ? "pill-bad" : "pill-warn"))));
          li.appendChild(document.createTextNode(" " + (s.tier || "unclassified") +
            (s.year ? ", " + s.year : "")));
          sul.appendChild(li);
        });
        card.appendChild(sul);
      }

      if (rec.conflicts.length) {
        var cul = el("ul", "note-list");
        rec.conflicts.forEach(function (c) { cul.appendChild(el("li", null, c)); });
        card.appendChild(cul);
      }

      wrap.appendChild(card);
    });
    return wrap;
  }

  function renderFindings(result) {
    var wrap = el("div");
    var filters = el("div", "filters");
    ["all", "high", "medium", "low"].forEach(function (sev) {
      var chip = el("button", "chip" + (state.severity === sev ? " active" : ""), sev);
      chip.addEventListener("click", function () {
        state.severity = sev;
        render(state.result);
      });
      filters.appendChild(chip);
    });
    var axisSel = el("select");
    axisSel.appendChild(new Option("All axes", "all"));
    result.axes.forEach(function (a) {
      axisSel.appendChild(new Option(a.label, a.key));
    });
    axisSel.value = state.axis;
    axisSel.addEventListener("change", function () {
      state.axis = axisSel.value;
      render(state.result);
    });
    filters.appendChild(axisSel);
    wrap.appendChild(filters);

    var shown = result.findings.filter(function (f) {
      if (state.severity !== "all" && f.severity !== state.severity) { return false; }
      if (state.axis !== "all" && f.axis !== state.axis) { return false; }
      return true;
    });

    if (!shown.length) {
      wrap.appendChild(el("p", "hint", "No findings match this filter."));
      return wrap;
    }

    var order = { high: 0, medium: 1, low: 2 };
    shown.sort(function (a, b) {
      return order[a.severity] - order[b.severity] || a.axis.localeCompare(b.axis);
    });

    shown.forEach(function (f) {
      var card = el("div", "card finding sev-" + f.severity);
      var h = el("h4", null, f.title);
      h.appendChild(severityPill(f.severity));
      h.appendChild(pill(f.axis, "pill-muted"));
      if (f.person) { h.appendChild(pill(f.person, "pill-muted")); }
      card.appendChild(h);
      card.appendChild(el("p", null, f.detail));
      if (f.evidence) {
        card.appendChild(el("div", "evidence", f.evidence));
      }
      var fix = el("p", "fix");
      fix.appendChild(el("strong", null, "Fix: "));
      fix.appendChild(document.createTextNode(f.fix));
      card.appendChild(fix);
      wrap.appendChild(card);
    });
    return wrap;
  }

  function renderPlan(result) {
    var wrap = el("div");
    if (!result.plan.length) {
      wrap.appendChild(el("p", "hint", "Nothing outstanding - or nothing to search yet."));
      return wrap;
    }
    result.plan.forEach(function (item) {
      var card = el("div", "card plan-item");
      card.appendChild(el("div", "plan-rank", item.rank));
      var body = el("div");
      body.appendChild(el("div", "plan-title", item.title));
      body.appendChild(el("div", "plan-meta",
        (item.person ? item.person + " - " : "") + "target " +
        (item.target_year || "any year") + " - impact " + item.impact + ", likelihood " +
        Math.round(item.likelihood * 100) + "%"));
      body.appendChild(el("div", "plan-why", item.why));
      body.appendChild(el("div", "plan-col", item.collection_name));
      if (item.look_for) {
        body.appendChild(el("div", "plan-why", item.look_for));
      }
      var score = el("div", "plan-score");
      score.appendChild(document.createTextNode("priority"));
      var mb = el("div", "mini-bar");
      var fill = el("span");
      fill.style.width = Math.min(100, item.priority_score * 12) + "%";
      mb.appendChild(fill);
      score.appendChild(mb);
      score.appendChild(document.createTextNode(String(item.priority_score)));
      body.appendChild(score);
      card.appendChild(body);
      wrap.appendChild(card);
    });
    return wrap;
  }

  function renderIdentity(result) {
    var wrap = el("div");
    if (!result.identity.length) {
      wrap.appendChild(el("p", "hint",
        "Only one name to compare - add a second person with the same surname to check for a name collision."));
      return wrap;
    }
    result.identity.forEach(function (pair) {
      var card = el("div", "card");
      var h = el("h4", null, pair.a_name + "  vs  " + pair.b_name);
      var band = pair.band === "likely_same" ? "pill-warn" :
        (pair.band === "possible_same" ? "pill-info" : "pill-good");
      h.appendChild(pill(pair.band.replace(/_/g, " "), band));
      h.appendChild(pill(pair.score + "/100", "pill-muted"));
      card.appendChild(h);
      if (pair.reasons.length) {
        var ul = el("ul", "note-list");
        pair.reasons.forEach(function (r) { ul.appendChild(el("li", null, r)); });
        card.appendChild(ul);
      }
      if (pair.conflicts.length) {
        var cul = el("ul", "note-list");
        pair.conflicts.forEach(function (c) { cul.appendChild(el("li", null, c)); });
        card.appendChild(cul);
      }
      wrap.appendChild(card);
    });
    return wrap;
  }

  function renderCollections(result) {
    var wrap = el("div");
    result.collections.forEach(function (c) {
      var d = el("details", "acc");
      d.appendChild(el("summary", null, c.name));
      d.appendChild(el("p", null, c.year_note || ""));
      d.appendChild(el("p", null, "Proves: " + (c.proves || "")));
      if (c.how_to) { d.appendChild(el("p", null, "How: " + c.how_to)); }
      if (c.caveats && c.caveats.length) {
        var ul = el("ul", "note-list");
        c.caveats.forEach(function (x) { ul.appendChild(el("li", null, x)); });
        d.appendChild(ul);
      }
      wrap.appendChild(d);
    });
    return wrap;
  }

  // ------------------------------------------------------------- history
  function loadHistory() {
    fetch("/api/sessions").then(function (r) { return r.json(); }).then(function (rows) {
      var host = $("#history");
      clear(host);
      if (!rows.length) {
        host.appendChild(el("p", "hint", "No saved analyses yet."));
        return;
      }
      rows.forEach(function (row) {
        var line = el("div", "history-row");
        var left = el("div");
        left.appendChild(el("div", "history-title", row.title || "Untitled"));
        left.appendChild(el("div", "history-sub",
          new Date(row.created_at * 1000).toLocaleString() + " - " + row.mode +
          " - " + (row.overall !== null ? row.overall + "/100 " : "") +
          (row.verdict ? row.verdict.replace(/_/g, " ") : "")));
        line.appendChild(left);

        var right = el("div");
        right.style.display = "flex";
        right.style.gap = "10px";
        right.style.alignItems = "center";

        var stars = el("div", "stars");
        for (var i = 1; i <= 5; i += 1) {
          (function (value) {
            var s = el("span", "star" + (row.rating && row.rating >= value ? " on" : ""), "\u2605");
            s.addEventListener("click", function () {
              patchJSON("/api/sessions/" + row.id, { rating: value }).then(loadHistory);
            });
            stars.appendChild(s);
          })(i);
        }
        right.appendChild(stars);

        var open = el("button", "btn btn-ghost", "Open");
        open.addEventListener("click", function () {
          fetch("/api/sessions/" + row.id).then(function (r) { return r.json(); }).then(function (data) {
            state.result = data.result;
            state.sessionId = row.id;
            render(data.result);
            wireExports(row.id);
            window.scrollTo({ top: 0, behavior: "smooth" });
          });
        });
        right.appendChild(open);

        var md = el("a", "btn btn-ghost", "MD");
        md.href = "/api/export/" + row.id + "?fmt=md";
        right.appendChild(md);

        var csv = el("a", "btn btn-ghost", "CSV");
        csv.href = "/api/export/" + row.id + "?fmt=csv";
        right.appendChild(csv);

        line.appendChild(right);

        var note = el("input", "history-note");
        note.placeholder = "Add a note to this analysis...";
        note.value = row.notes || "";
        note.addEventListener("change", function () {
          patchJSON("/api/sessions/" + row.id, { notes: note.value });
        });
        line.appendChild(note);

        host.appendChild(line);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
