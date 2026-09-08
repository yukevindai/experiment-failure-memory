"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "",
  projects = [],
  labs = [],
  selected = null,
  editing = null;
const arrays = {
  materials: [
    "Materials",
    {
      name: "Name",
      lot: "Lot / batch",
      identifier: "Identifier",
      amount_name: "Amount name",
      amount_value: "Amount:number",
      amount_unit: "Amount unit",
      amount_uncertainty: "Amount uncertainty:number",
    },
  ],
  conditions: [
    "Conditions",
    {
      name: "Name",
      value: "Value:number",
      unit: "Unit",
      uncertainty: "Uncertainty:number",
    },
  ],
  measurements: [
    "Measurements",
    {
      name: "Name",
      value: "Value:number",
      unit: "Unit",
      uncertainty: "Uncertainty:number",
    },
  ],
  equipment: [
    "Equipment",
    {
      name: "Name",
      asset_id: "Asset ID",
      calibration_note: "Calibration note",
    },
  ],
  suspected_causes: [
    "Suspected causes",
    {
      cause: "Suspected cause",
      confidence: ["unknown", "low", "medium", "high"],
      rationale: "Supporting observations",
    },
  ],
  fixes: [
    "Recovery strategies",
    {
      action: "Action",
      outcome: ["unknown", "untried", "succeeded", "failed", "mixed"],
      evidence: "Observed result / evidence",
    },
  ],
};
function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function action(text, fn, cls) {
  const e = node("button", text, cls);
  e.type = "button";
  e.addEventListener("click", () => run(fn));
  return e;
}
function tell(text = "") {
  $("notice").textContent = text;
}
async function run(fn) {
  try {
    tell();
    await fn();
  } catch (e) {
    tell(e.message || String(e));
  }
}
async function api(path, method = "GET", data, raw = false) {
  const headers = { "X-EFM-Request": "1" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  if (data !== undefined && !raw) headers["Content-Type"] = "application/json";
  const response = await fetch("/api" + path, {
    method,
    headers,
    credentials: "same-origin",
    body: data === undefined ? undefined : raw ? data : JSON.stringify(data),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    if (response.status === 401 && path !== "/login") signedOut();
    throw Error(
      typeof detail.detail === "string"
        ? detail.detail
        : JSON.stringify(detail.detail),
    );
  }
  if (response.headers.get("content-type")?.includes("application/zip"))
    return response.blob();
  return response.json();
}
function signedOut() {
  csrf = "";
  selected = null;
  editing = null;
  projects = [];
  labs = [];
  $("workspace").hidden = true;
  $("login-panel").hidden = false;
  $("identity").replaceChildren();
  $("results").replaceChildren();
  $("detail").replaceChildren();
  $("patterns").replaceChildren();
  $("members").textContent = "";
  $("event-list").textContent = "";
  $("editor").close();
  $("record-form").reset();
  $("structured-fields").replaceChildren();
}
async function signedIn(user) {
  csrf = user.csrf;
  $("login-panel").hidden = true;
  $("workspace").hidden = false;
  $("identity").replaceChildren(
    node("span", user.display_name + " "),
    action("Sign out", async () => {
      await api("/logout", "POST");
      signedOut();
    }),
  );
  await refresh();
}
function requireProject() {
  const id = $("project").value;
  if (!id) throw Error("Select a project first.");
  return id;
}
function canEdit(record) {
  return (
    projects.find((p) => p.id === (record?.project_id || $("project").value))
      ?.effective_role &&
    ["admin", "editor"].includes(
      projects.find((p) => p.id === (record?.project_id || $("project").value))
        .effective_role,
    )
  );
}
function choices(select, values, empty) {
  const previous = select.value;
  select.replaceChildren();
  if (empty) select.append(new Option(empty, ""));
  for (const v of values) select.append(new Option(v.name, v.id));
  if ([...select.options].some((o) => o.value === previous))
    select.value = previous;
}
async function refresh() {
  [projects, labs] = await Promise.all([api("/projects"), api("/labs")]);
  choices($("project"), projects, "All accessible projects");
  choices(
    $("lab"),
    labs.filter((l) => ["owner", "admin"].includes(l.role)),
  );
  await search();
}
async function search() {
  const query = new URLSearchParams({
    q: $("query").value,
    archived: $("archived").checked,
    limit: 200,
  });
  if ($("project").value) query.set("project_id", $("project").value);
  if ($("status").value) query.set("status", $("status").value);
  const records = await api("/search?" + query);
  $("result-count").textContent =
    `${records.length} experiments shown${records.length === 200 ? " (limit reached; narrow your search)" : ""}`;
  $("results").replaceChildren(...records.map(card));
  if (!records.length)
    $("results").append(
      node(
        "div",
        "No experiments match this view. Select a project to record an attempt, or adjust your search.",
        "empty",
      ),
    );
  $("new-record").disabled = !$("project").value || !canEdit();
}
function card(r) {
  const e = node("article", undefined, "card");
  e.append(
    node("span", r.record.status, "badge " + r.record.status),
    node("h3", r.record.title),
    node("p", r.record.summary || r.record.outcomes.slice(0, 180)),
    node(
      "p",
      new Date(r.record.performed_at).toLocaleString() +
        " · version " +
        r.version,
      "muted",
    ),
    action("Inspect experiment", () => detail(r.id), "secondary"),
  );
  return e;
}
function disclosure(title, value) {
  const e = node("details");
  e.append(
    node("summary", title),
    node(
      "pre",
      typeof value === "string" ? value : JSON.stringify(value, null, 2),
    ),
  );
  return e;
}
async function detail(id) {
  selected = await api("/records/" + id);
  const r = selected,
    p = r.record,
    e = $("detail");
  e.hidden = false;
  e.replaceChildren(
    node(
      "span",
      p.status + (r.archived ? " · archived" : ""),
      "badge " + p.status,
    ),
    node("h2", p.title),
    node("p", p.summary),
    node("h3", "Observed outcomes"),
    node("p", p.outcomes, "detail-text"),
    node("h3", "Uncertainty"),
    node("p", p.uncertainty_notes, "detail-text"),
    node("p", `Source: ${p.source.kind} · ${p.source.reference}`),
    node("p", `Record ${r.id} · version ${r.version}`, "muted"),
  );
  const buttons = node("div");
  buttons.append(
    action("Find similar attempts", async () => {
      const rows = await api("/records/" + id + "/similar");
      const list = node("div");
      list.append(
        node("h3", "Similar attempts"),
        node(
          "p",
          "Similarity combines shared terms and compatible conditions. It does not establish a common cause.",
        ),
      );
      for (const x of rows) {
        list.append(
          action(
            `${x.record.title} · ${(x.similarity * 100).toFixed(0)}% similarity`,
            () => detail(x.id),
            "secondary",
          ),
          disclosure("Comparison evidence", {
            matched_terms: x.matched_terms,
            conditions: x.condition_comparisons,
          }),
        );
      }
      if (!rows.length)
        list.append(node("p", "No other active attempts in this project."));
      e.append(list);
    }),
  );
  if (canEdit(r))
    buttons.append(
      action("Edit experiment", () => edit(r)),
      action(
        r.archived ? "Restore" : "Archive",
        async () => {
          await api("/records/" + id + "/archive", "POST", {
            expected_version: r.version,
            archived: !r.archived,
          });
          await search();
          await detail(id);
        },
        "secondary",
      ),
    );
  e.append(buttons);
  e.append(disclosure("Complete experimental conditions & provenance", p));
  const attachments = node("section");
  attachments.append(node("h3", "Attachments"));
  for (const a of r.attachments) {
    const link = node("a", `${a.filename} (${a.size} bytes)`);
    link.href = "/api/attachments/" + encodeURIComponent(a.id);
    attachments.append(link, node("p", "SHA-256: " + a.sha256, "muted"));
  }
  if (!r.attachments.length)
    attachments.append(node("p", "No attachments.", "muted"));
  if (canEdit(r)) {
    const file = node("input");
    file.type = "file";
    file.setAttribute("aria-label", "Attach a file");
    attachments.append(
      file,
      action("Upload attachment", async () => {
        if (!file.files.length) throw Error("Choose a file first.");
        const f = file.files[0];
        if (f.size > 16 * 1024 * 1024)
          throw Error("Maximum attachment size is 16 MiB.");
        await api(
          "/records/" +
            id +
            "/attachments?filename=" +
            encodeURIComponent(f.name),
          "POST",
          f,
          true,
        );
        await detail(id);
      }),
    );
  }
  e.append(attachments);
  e.append(node("h3", "Related attempts"));
  for (const l of r.links) {
    const target = l.source_id === id ? l.target_id : l.source_id;
    e.append(
      action(
        `${l.source_id === id ? "This attempt" : "Other attempt"} ${l.relation.replaceAll("_", " ")} ${l.target_id === id ? "this attempt" : l.target_id}`,
        () => detail(target),
        "secondary",
      ),
    );
  }
  if (canEdit(r)) {
    const target = node("input");
    target.placeholder = "Related record ID";
    target.setAttribute("aria-label", "Related record ID");
    const relation = node("select");
    relation.setAttribute("aria-label", "Relationship");
    for (const v of ["repeat_of", "fix_for", "derived_from"])
      relation.append(new Option(v.replaceAll("_", " "), v));
    e.append(
      target,
      relation,
      action("Link this attempt", async () => {
        await api("/records/" + id + "/links", "POST", {
          target_id: target.value,
          relation: relation.value,
        });
        await detail(id);
      }),
    );
  }
  e.append(node("h3", "Discussion"));
  for (const c of r.comments)
    e.append(
      node("p", `${c.author} · ${new Date(c.at).toLocaleString()}`, "muted"),
      node("p", c.body, "detail-text"),
    );
  if (canEdit(r)) {
    const comment = node("textarea");
    comment.placeholder = "Add an observation or follow-up";
    comment.maxLength = 10000;
    comment.setAttribute("aria-label", "Comment");
    e.append(
      comment,
      action("Add comment", async () => {
        await api("/records/" + id + "/comments", "POST", {
          body: comment.value,
        });
        await detail(id);
      }),
    );
  }
  e.append(disclosure("Revision history", r.history));
  e.scrollIntoView({ behavior: "smooth", block: "start" });
}
function addEntry(key, value = {}) {
  const e = node("div", undefined, "entry");
  e.dataset.entry = key;
  const data = { ...value };
  if (value.amount)
    for (const [k, v] of Object.entries(value.amount)) data["amount_" + k] = v;
  for (const [field, label] of Object.entries(arrays[key][1])) {
    const wrap = node(
      "label",
      Array.isArray(label) ? field.replaceAll("_", " ") : label.split(":")[0],
    );
    const input = node(Array.isArray(label) ? "select" : "input");
    input.dataset.field = field;
    if (Array.isArray(label))
      for (const v of label) input.append(new Option(v, v));
    else if (label.endsWith(":number")) {
      input.type = "number";
      input.step = "any";
      if (field.includes("uncertainty")) input.min = "0";
    }
    input.value = data[field] ?? (Array.isArray(label) ? label[0] : "");
    wrap.append(input);
    e.append(wrap);
  }
  e.append(action("Remove", () => e.remove(), "secondary"));
  $("entries-" + key).append(e);
}
function edit(record = null) {
  if (!record) requireProject();
  editing = record;
  $("editor-title").textContent = record
    ? "Edit experiment"
    : "Record an experiment";
  $("record-form").reset();
  $("edit-error").textContent = "";
  const p = record?.record || {};
  const form = $("record-form");
  for (const k of ["title", "summary", "outcomes", "uncertainty_notes"])
    form.elements[k].value = p[k] || "";
  form.elements.status.value = p.status || "failed";
  const d = p.performed_at ? new Date(p.performed_at) : new Date();
  form.elements.performed_at.value = new Date(
    d.getTime() - d.getTimezoneOffset() * 60000,
  )
    .toISOString()
    .slice(0, 16);
  form.elements.tags.value = (p.tags || []).join(", ");
  form.elements.procedure.value = (p.procedure || []).join("\n");
  for (const k of ["kind", "reference", "notes"])
    form.elements["source_" + k].value =
      p.source?.[k] || (k === "kind" ? "notebook" : "");
  $("structured-fields").replaceChildren();
  for (const [key, [label]] of Object.entries(arrays)) {
    const field = node("fieldset");
    field.append(node("legend", label));
    const entries = node("div");
    entries.id = "entries-" + key;
    field.append(
      entries,
      action("Add " + label.toLowerCase(), () => addEntry(key), "secondary"),
    );
    $("structured-fields").append(field);
    for (const v of p[key] || []) addEntry(key, v);
  }
  $("editor").showModal();
}
function serialize() {
  const f = $("record-form"),
    p = {};
  for (const k of [
    "title",
    "summary",
    "status",
    "outcomes",
    "uncertainty_notes",
  ])
    p[k] = f.elements[k].value;
  p.performed_at = new Date(f.elements.performed_at.value).toISOString();
  if (editing) {
    const original = new Date(editing.record.performed_at);
    const local = new Date(
      original.getTime() - original.getTimezoneOffset() * 60000,
    )
      .toISOString()
      .slice(0, 16);
    if (f.elements.performed_at.value === local)
      p.performed_at = editing.record.performed_at;
  }
  p.tags = f.elements.tags.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  p.procedure = f.elements.procedure.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  p.source = {};
  for (const k of ["kind", "reference", "notes"])
    p.source[k] = f.elements["source_" + k].value;
  for (const key of Object.keys(arrays)) {
    p[key] = [...document.querySelectorAll(`[data-entry="${key}"]`)].map(
      (e) => {
        const v = {};
        for (const input of e.querySelectorAll("[data-field]"))
          v[input.dataset.field] =
            input.type === "number"
              ? input.value === ""
                ? null
                : Number(input.value)
              : input.value;
        if (key === "materials") {
          const hasAmount = [
            "amount_name",
            "amount_value",
            "amount_unit",
            "amount_uncertainty",
          ].some((k) => v[k] !== "" && v[k] !== null);
          v.amount = hasAmount
            ? {
                name: v.amount_name,
                value: v.amount_value,
                unit: v.amount_unit,
                uncertainty: v.amount_uncertainty,
              }
            : null;
          for (const k of Object.keys(v))
            if (k.startsWith("amount_")) delete v[k];
        }
        return v;
      },
    );
  }
  return p;
}
async function patterns() {
  const p = await api("/projects/" + requireProject() + "/patterns");
  const e = $("patterns");
  e.replaceChildren(
    node("p", p.interpretation),
    node("p", `${p.record_count} active records`),
    disclosure("Observed outcomes", p.outcomes),
  );
  for (const c of p.causes)
    e.append(disclosure(`${c.label} · ${c.record_count} records`, c.evidence));
  e.append(node("h3", "Recovery strategies"));
  for (const f of p.recovery_strategies)
    e.append(
      disclosure(f.action, {
        reported_outcomes: f.reported_outcomes,
        evidence: f.evidence,
      }),
    );
}
function submit(id, fn) {
  $(id).addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const button = e.submitter;
      if (button) button.disabled = true;
      try {
        await fn(new FormData(e.target));
      } finally {
        if (button) button.disabled = false;
      }
    });
  });
}
submit("login-form", async (data) => {
  const result = await api("/login", "POST", Object.fromEntries(data));
  $("login-form").reset();
  await signedIn({ ...result.user, csrf: result.csrf });
});
submit("lab-form", async (data) => {
  await api("/labs", "POST", Object.fromEntries(data));
  $("lab-form").reset();
  await refresh();
  tell("Laboratory created.");
});
submit("project-form", async (data) => {
  const lab = data.get("lab");
  if (!lab) throw Error("Select a laboratory first.");
  data.delete("lab");
  const p = await api(
    "/labs/" + lab + "/projects",
    "POST",
    Object.fromEntries(data),
  );
  $("project-form").reset();
  await refresh();
  $("project").value = p.id;
  await search();
  tell("Project created.");
});
submit("lab-member-form", async (data) => {
  if (!$("lab").value) throw Error("Select a laboratory first.");
  await api(
    "/labs/" + $("lab").value + "/members",
    "PUT",
    Object.fromEntries(data),
  );
  tell("Laboratory membership updated.");
  await refresh();
});
submit("project-member-form", async (data) => {
  await api(
    "/projects/" + requireProject() + "/members",
    "PUT",
    Object.fromEntries(data),
  );
  tell("Project membership updated.");
  await refresh();
});
submit("record-form", async () => {
  try {
    const p = serialize();
    const saved = editing
      ? await api("/records/" + editing.id, "PUT", {
          expected_version: editing.version,
          record: p,
        })
      : await api("/projects/" + requireProject() + "/records", "POST", p);
    $("editor").close();
    await search();
    await detail(saved.id);
  } catch (e) {
    $("edit-error").textContent = e.message;
    throw e;
  }
});
submit("import-form", async (data) => {
  const file = data.get("file");
  if (file.size > 1024 * 1024) throw Error("Import exceeds 1 MiB.");
  const result = await api(
    "/projects/" + requireProject() + "/import",
    "POST",
    JSON.parse(await file.text()),
  );
  await search();
  await detail(result.record.id);
  tell(
    result.replayed
      ? "This external record was already imported."
      : "Record imported.",
  );
});
$("new-record").onclick = () => run(() => edit());
$("cancel-edit").onclick = () => $("editor").close();
$("search").onclick = () => run(search);
$("query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") run(search);
});
$("project").onchange = () =>
  run(async () => {
    $("detail").hidden = true;
    await search();
    if (!$("patterns-tab").hidden) await patterns();
  });
for (const button of document.querySelectorAll("[data-tab]"))
  button.onclick = () =>
    run(async () => {
      for (const tab of ["records", "patterns", "manage"])
        $(tab + "-tab").hidden = tab !== button.dataset.tab;
      for (const b of document.querySelectorAll("[data-tab]"))
        b.classList.toggle("active", b === button);
      if (button.dataset.tab === "patterns") await patterns();
    });
$("lab-members").onclick = () =>
  run(async () => {
    if (!$("lab").value) throw Error("Select a laboratory first.");
    $("members").textContent = JSON.stringify(
      await api("/labs/" + $("lab").value + "/members"),
      null,
      2,
    );
  });
$("project-members").onclick = () =>
  run(async () => {
    $("members").textContent = JSON.stringify(
      await api("/projects/" + requireProject() + "/members"),
      null,
      2,
    );
  });
$("events").onclick = () =>
  run(async () => {
    $("event-list").textContent = JSON.stringify(
      await api("/projects/" + requireProject() + "/events"),
      null,
      2,
    );
  });
$("export").onclick = () =>
  run(async () => {
    const blob = await api(
      "/projects/" +
        requireProject() +
        "/export?attachments=" +
        $("include-attachments").checked,
      "POST",
    );
    const url = URL.createObjectURL(blob),
      a = node("a");
    a.href = url;
    a.download = "experiment-memory.zip";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    tell("Export downloaded. Keep the copy in an authorized location.");
  });
api("/me")
  .then(signedIn)
  .catch(() => signedOut());
