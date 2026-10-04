"use strict";

// Deliberately dependency-free. API keys live only in this closure's memory.
(() => {
  const $ = (selector) => document.querySelector(selector);
  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const date = (value) =>
    value
      ? new Date(value * 1000).toLocaleString([], {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";
  const bytes = (value) =>
    value < 1024
      ? `${value} B`
      : value < 1048576
        ? `${(value / 1024).toFixed(1)} KB`
        : `${(value / 1048576).toFixed(1)} MB`;
  const badge = (state) =>
    `<span class="badge ${escape(state)}">${escape(state)}</span>`;
  const icons = {
    overview: "◈",
    compute: "▤",
    bucket: "▱",
    database: "▥",
    loadbalancer: "⇄",
    secrets: "◇",
    keys: "♙",
    monitoring: "⌁",
    billing: "▧",
    audit: "≡",
    operations: "↻",
  };
  const pages = {
    overview: [
      "Overview",
      "A clear view of your resources and recent activity.",
    ],
    compute: [
      "Compute",
      "Isolated containers from an operator-approved image catalog.",
    ],
    bucket: [
      "Object storage",
      "Private buckets, integrity-checked objects, and predictable quotas.",
    ],
    database: [
      "Databases",
      "Single-node PostgreSQL with persistent volumes and managed credentials.",
    ],
    loadbalancer: [
      "Load balancers",
      "Distribute HTTP requests across your ready compute resources.",
    ],
    secrets: [
      "Secrets",
      "Encrypted values, version counters, and audited access.",
    ],
    keys: [
      "Identity & access",
      "Expiring workspace keys with owner, operator, and viewer roles.",
    ],
    monitoring: [
      "Monitoring",
      "Control-plane metrics and queue health, scoped to your workspace.",
    ],
    billing: [
      "Billing simulator",
      "Understand synthetic resource costs. No payments or real charges.",
    ],
    audit: [
      "Audit trail",
      "A history of accepted changes and secret access in your workspace.",
    ],
    operations: [
      "Operations",
      "Durable provisioning jobs, retries, and actionable failure messages.",
    ],
  };
  let token = "",
    identity = null,
    page = "overview",
    offset = 0,
    timer = null,
    loadVersion = 0;
  let resourceCache = [],
    selectedBucket = "",
    dialogAction = null,
    submitting = false;
  const pageSize = 20;

  async function api(path, options = {}) {
    const headers = {
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    };
    if (options.body && typeof options.body === "string")
      headers["Content-Type"] = "application/json";
    const response = await fetch(`/v1${path}`, { ...options, headers });
    if (response.status === 401) {
      logout();
      throw new Error("Your key is invalid or expired. Connect again.");
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(
        Array.isArray(error.detail)
          ? error.detail.map((item) => item.msg).join("; ")
          : error.detail || `Request failed (${response.status})`,
      );
    }
    if (response.status === 204) return null;
    if (options.raw) return response;
    return response.json();
  }

  function notice(message, error = false) {
    const node = $("#notice");
    node.textContent = message;
    node.className = error ? "error" : "";
    node.hidden = false;
  }
  function empty(title, subtitle = "") {
    return `<div class="empty"><strong>${escape(title)}</strong>${escape(subtitle)}</div>`;
  }
  function table(headers, rows) {
    return `<div class="table-scroll"><table><thead><tr>${headers.map((h) => `<th>${escape(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }
  function panel(title, body, note = "") {
    return `<section class="panel"><div class="panel-header"><h2>${escape(title)}</h2><small>${escape(note)}</small></div>${body}</section>`;
  }
  function paginate(length) {
    return `<div class="pagination"><button class="secondary" data-action="previous" ${offset === 0 ? "disabled" : ""}>← Previous</button><span>Page ${offset / pageSize + 1}</span><button class="secondary" data-action="next" ${length < pageSize ? "disabled" : ""}>Next →</button></div>`;
  }
  function canWrite() {
    return identity && identity.role !== "viewer";
  }

  function logout() {
    token = "";
    identity = null;
    resourceCache = [];
    selectedBucket = "";
    loadVersion++;
    clearInterval(timer);
    closeDialog();
    $("#api-key").value = "";
    $("#content").replaceChildren();
    $("#shell").hidden = true;
    $("#login").hidden = false;
  }

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    $("#login-error").textContent = "";
    token = $("#api-key").value.trim();
    try {
      identity = await api("/me");
      $("#api-key").value = "";
      $("#workspace-name").textContent = identity.tenant_name;
      $("#role-label").textContent = `${identity.role} workspace`;
      $("#provider-label").textContent =
        `${identity.provider === "docker" ? "Docker runtime" : "Simulation mode"}`;
      $("#mode-banner").innerHTML =
        identity.provider === "simulated"
          ? "<strong>Simulation mode</strong> &nbsp; Compute, databases, and load balancers simulate lifecycle. Object storage and secrets store real data."
          : "<strong>Local Docker provider</strong> &nbsp; Workloads run on your host. Single-node preview; billing is always simulated.";
      $("#login").hidden = true;
      $("#shell").hidden = false;
      navigate();
      timer = setInterval(() => {
        if (!$("#modal").open && !document.hidden) load(true);
      }, 5000);
    } catch (error) {
      token = "";
      $("#login-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  $("#navigation").innerHTML = Object.entries(pages)
    .map(
      ([key, value], index) =>
        `${index === 5 || index === 8 ? '<div class="nav-divider"></div>' : ""}<a href="#${key}" data-page="${key}"><span class="nav-icon" aria-hidden="true">${icons[key]}</span>${value[0]}</a>`,
    )
    .join("");
  function navigate() {
    const target = location.hash.slice(1);
    page = Object.hasOwn(pages, target) ? target : "overview";
    offset = 0;
    if (!identity) return;
    $("#breadcrumb").textContent = pages[page][0];
    $("#page-title").textContent = pages[page][0];
    $("#page-description").textContent = pages[page][1];
    $("#page-kicker").textContent =
      page === "overview" ? "YOUR CLOUD, AT A GLANCE" : "WORKSPACE / SERVICES";
    document.querySelectorAll("nav a").forEach((node) => {
      node.classList.toggle("active", node.dataset.page === page);
      node.setAttribute(
        "aria-current",
        node.dataset.page === page ? "page" : "false",
      );
    });
    $(".sidebar").classList.remove("open");
    $("#notice").hidden = true;
    const createPages = [
      "overview",
      "compute",
      "bucket",
      "database",
      "loadbalancer",
      "secrets",
      "keys",
    ];
    $("#create").hidden =
      !createPages.includes(page) ||
      !canWrite() ||
      (page === "keys" && identity.role !== "owner");
    $("#create").textContent =
      page === "secrets"
        ? "+ Store secret"
        : page === "keys"
          ? "+ Create API key"
          : "+ Create resource";
    load();
  }
  window.addEventListener("hashchange", navigate);
  $("#logout").addEventListener("click", logout);
  $("#menu-button").addEventListener("click", () =>
    $(".sidebar").classList.toggle("open"),
  );
  $("#refresh").addEventListener("click", () => load());

  async function load(quiet = false) {
    if (!identity) return;
    const version = ++loadVersion;
    if (!quiet) $("#content").innerHTML = empty("Loading your workspace…");
    try {
      let html;
      let nextResourceCache = [];
      if (page === "overview") {
        const [resources, events, bill, jobs, summary] = await Promise.all([
          api("/resources?limit=8"),
          api("/audit?limit=5"),
          api("/billing"),
          api("/operations?limit=5"),
          api("/summary"),
        ]);
        nextResourceCache = resources.items;
        const stats = [
          [
            "Active resources",
            summary.active_resources,
            "Across all services",
            "◈",
          ],
          [
            "Ready workloads",
            summary.ready_resources,
            "Control-plane ready state",
            "▤",
          ],
          [
            "Stored objects",
            summary.objects,
            `${bytes(summary.storage_bytes)} of ${bytes(identity.quotas.storage_bytes)}`,
            "▱",
          ],
          [
            "Estimated usage",
            `$${(bill.total_microusd / 1000000).toFixed(4)}`,
            "Simulation · lifetime estimate",
            "▧",
          ],
        ];
        html = `<div class="stats">${stats.map(([label, value, caption, icon]) => `<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${icon}</span></div><div class="stat-value">${value}</div><div class="stat-caption">${caption}</div></div>`).join("")}</div><div class="section-title"><h2>Your cloud services</h2><span class="helper">Built around your workspace</span></div><div class="service-grid">${["compute", "bucket", "database", "loadbalancer"].map((kind) => `<a class="service-card" href="#${kind}"><span class="service-icon">${icons[kind]}</span><h3>${pages[kind][0]}</h3><p>${{ compute: "Provision isolated container workloads.", bucket: "Store files with verified integrity.", database: "Run PostgreSQL with persistent data.", loadbalancer: "Route traffic across compute targets." }[kind]}</p><div class="service-link"><span>${summary.by_kind[kind] || 0} active resources</span><span>Explore →</span></div></a>`).join("")}</div><div class="section-title"><h2>Recent resources</h2><a href="#operations">View operations →</a></div>${panel("Resource inventory", resourceTable(resources.items), "Latest 8 resources")}<div class="split">${panel("Recent activity", events.items.length ? events.items.map((event) => `<div class="activity-row"><span class="activity-dot"></span><div><strong>${escape(event.action)}</strong><small>${date(event.created_at)} · ${escape(event.actor === "worker" ? "Provisioning worker" : "API key " + event.actor.slice(0, 8))}</small></div></div>`).join("") : empty("Your audit trail starts here", "Accepted operations appear automatically."))}${panel("Workspace posture", `<div class="quality-card"><div class="quality-row"><span>Provider</span><strong>${escape(identity.provider)}</strong></div><div class="quality-row"><span>Access role</span><strong>${escape(identity.role)}</strong></div><div class="quality-row"><span>Resource quota</span><strong>${summary.active_resources} / ${identity.quotas.resources}</strong></div><div class="quality-row"><span>Latest operations</span><strong>${jobs.items.filter((x) => x.state === "failed").length} failed in latest 5</strong></div><p>Readiness reports control-plane state. Application uptime, backups, and host health require separate operational checks.</p></div>`)}</div>`;
      } else if (["compute", "database", "loadbalancer"].includes(page)) {
        const result = await api(
          `/resources?kind=${page}&limit=${pageSize}&offset=${offset}`,
        );
        nextResourceCache = result.items;
        html = panel(
          pages[page][0],
          resourceTable(result.items) + paginate(result.items.length),
          "Workspace resources",
        );
      } else if (page === "bucket") {
        const result = await api(
          `/resources?kind=bucket&limit=${pageSize}&offset=${offset}`,
        );
        nextResourceCache = result.items;
        html = panel(
          "Buckets",
          resourceTable(result.items) + paginate(result.items.length),
        );
        const available = result.items.filter((x) => x.state === "ready");
        if (!available.some((x) => x.id === selectedBucket))
          selectedBucket = available[0]?.id || "";
        if (selectedBucket) {
          const objects = await api(
            `/buckets/${selectedBucket}/objects?limit=100`,
          );
          html += panel(
            "Object explorer",
            `<div class="toolbar"><label class="helper" for="bucket-select">Bucket</label><select id="bucket-select">${available.map((x) => `<option value="${x.id}" ${x.id === selectedBucket ? "selected" : ""}>${escape(x.name)}</option>`).join("")}</select>${canWrite() ? '<button class="secondary" data-action="upload">↑ Upload object</button>' : ""}</div>${
              objects.items.length
                ? table(
                    ["Object key", "Size", "Updated", "Actions"],
                    objects.items.map(
                      (x) =>
                        `<tr><td><strong>${escape(x.key)}</strong></td><td>${bytes(x.size)}</td><td>${date(x.created_at)}</td><td><button class="row-action" data-action="download" data-key="${escape(x.key)}">Download</button>${canWrite() ? `<button class="row-action danger" data-action="delete-object" data-key="${escape(x.key)}">Delete</button>` : ""}</td></tr>`,
                    ),
                  )
                : empty(
                    "This bucket is empty",
                    "Upload your first object to get started.",
                  )
            }`,
            "First 100 objects · API supports pagination",
          );
        }
      } else if (page === "audit") {
        const result = await api(`/audit?limit=${pageSize}&offset=${offset}`);
        html = panel(
          "Audit events",
          (result.items.length
            ? table(
                ["Action", "Actor", "Target", "Time"],
                result.items.map(
                  (x) =>
                    `<tr><td><strong>${escape(x.action)}</strong></td><td>${escape(x.actor)}</td><td>${escape(x.target)}</td><td>${date(x.created_at)}</td></tr>`,
                ),
              )
            : empty("No events yet")) + paginate(result.items.length),
        );
      } else if (page === "operations") {
        const result = await api(
          `/operations?limit=${pageSize}&offset=${offset}`,
        );
        html = panel(
          "Provisioning operations",
          (result.items.length
            ? table(
                ["Operation", "Resource", "Status", "Attempts", "Error"],
                result.items.map(
                  (x) =>
                    `<tr><td><strong>${escape(x.action)}</strong><br><small>${escape(x.id.slice(0, 8))}</small></td><td>${escape(x.resource_id.slice(0, 8))}</td><td>${badge(x.state)}</td><td>${x.attempts}</td><td>${escape(x.error || "—")}</td></tr>`,
                ),
              )
            : empty("No operations yet")) + paginate(result.items.length),
        );
      } else if (page === "secrets") {
        const result = await api(`/secrets?limit=${pageSize}&offset=${offset}`);
        html = panel(
          "Encrypted secrets",
          (result.items.length
            ? table(
                ["Name", "Version", "Type", "Actions"],
                result.items.map(
                  (x) =>
                    `<tr><td><strong>${escape(x.name)}</strong></td><td>v${x.version}</td><td>${x.managed ? "Managed database" : "Workspace"}</td><td>${canWrite() ? `<button class="row-action" data-action="reveal" data-id="${x.id}">Reveal</button>${!x.managed ? `<button class="row-action danger" data-action="delete-secret" data-id="${x.id}">Delete</button>` : ""}` : "Metadata only"}</td></tr>`,
                ),
              )
            : empty(
                "No secrets stored",
                "Values are encrypted at rest and never included in list responses.",
              )) + paginate(result.items.length),
        );
      } else if (page === "keys") {
        if (identity.role !== "owner")
          html = panel(
            "Access restricted",
            empty(
              "Owner access required",
              "Your workspace owner manages API keys.",
            ),
          );
        else {
          const result = await api("/keys");
          html = panel(
            "Workspace API keys",
            table(
              ["Name", "Role", "Expires", "Status", "Actions"],
              result.items.map(
                (x) =>
                  `<tr><td><strong>${escape(x.name)}</strong></td><td>${badge(x.role)}</td><td>${date(x.expires_at)}</td><td>${x.revoked ? "Revoked" : x.expires_at * 1000 < Date.now() ? "Expired" : "Active"}</td><td>${!x.revoked && x.id !== identity.key_id ? `<button class="row-action danger" data-action="revoke" data-id="${x.id}">Revoke</button>` : x.id === identity.key_id ? "Current session" : "—"}</td></tr>`,
              ),
            ),
          );
        }
      } else if (page === "billing") {
        const result = await api("/billing");
        html = `<div class="warning">${escape(result.notice)}</div><div class="stats"><div class="stat"><div class="stat-top">Lifetime estimate</div><div class="stat-value">$${(result.total_microusd / 1000000).toFixed(4)}</div><div class="stat-caption">USD · simulated</div></div></div>${panel(
          "Resource estimates",
          result.items.length
            ? table(
                [
                  "Resource",
                  "Kind",
                  "Metered seconds",
                  "Rate / hour",
                  "Estimate",
                ],
                result.items.map(
                  (x) =>
                    `<tr><td><strong>${escape(x.name)}</strong></td><td>${escape(x.kind)}</td><td>${x.seconds}</td><td>$${(x.hourly_microusd / 1000000).toFixed(3)}</td><td>$${(x.amount_microusd / 1000000).toFixed(4)}</td></tr>`,
                ),
              )
            : empty("Nothing to estimate yet"),
        )}`;
      } else {
        const result = await api("/metrics", { raw: true });
        html =
          panel(
            "Prometheus-compatible metrics",
            `<pre class="metrics">${escape(await result.text())}</pre>`,
            "Authenticated · tenant-scoped",
          ) +
          panel(
            "What these metrics mean",
            `<div class="quality-card"><p>Resource counts reflect the last completed provisioning operation. Queue age exposes delayed or stalled work. These are control-plane metrics; CPU, memory, application health, alert routing, and distributed traces are planned integrations.</p><p>Scrape <strong>/v1/metrics</strong> with a dedicated viewer API key. Investigate when the oldest operation age exceeds 300 seconds.</p></div>`,
          );
      }
      if (version !== loadVersion || !identity) return;
      resourceCache = nextResourceCache;
      $("#content").innerHTML = html;
      $("#content").dataset.view = page;
      $("#updated").textContent =
        `Updated ${new Date().toLocaleTimeString()} · refreshes every 5s`;
    } catch (error) {
      if (version === loadVersion) {
        notice(error.message, true);
        if (!quiet)
          $("#content").innerHTML = empty(
            "Unable to load this view",
            "Refresh to try again.",
          );
      }
    }
  }

  function resourceTable(resources) {
    if (!resources.length)
      return empty(
        "Your cloud starts here",
        "Create a resource to see it in your workspace.",
      );
    return table(
      ["Resource", "Type", "Status", "Configuration", "Created", "Actions"],
      resources.map(
        (x) =>
          `<tr><td><div class="resource-cell"><span class="mini-icon">${icons[x.kind]}</span><div><strong>${escape(x.name)}</strong><small>${escape(x.id.slice(0, 8))}</small></div></div></td><td>${escape(x.kind)}</td><td>${badge(x.state)}</td><td>${x.kind === "bucket" ? "Private bucket" : `${x.spec.cpu} vCPU · ${x.spec.memory_mb} MB`}</td><td>${date(x.created_at)}</td><td><button class="row-action" data-action="inspect" data-id="${x.id}">Details</button>${canWrite() && ["ready", "failed"].includes(x.state) ? `<button class="row-action danger" data-action="delete" data-id="${x.id}">Delete</button>` : ""}</td></tr>`,
      ),
    );
  }
  function openDialog(title, body, label, action) {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = body;
    $("#modal-error").textContent = "";
    $("#modal-submit").textContent = label;
    $("#modal-submit").hidden = !action;
    $("#cancel-modal").textContent = action ? "Cancel" : "Close";
    dialogAction = action;
    $("#modal").showModal();
  }
  function closeDialog() {
    $("#modal").close();
    $("#modal-body").replaceChildren();
    dialogAction = null;
  }
  $("#close-modal").addEventListener("click", closeDialog);
  $("#cancel-modal").addEventListener("click", closeDialog);
  $("#modal").addEventListener("cancel", () => {
    setTimeout(closeDialog, 0);
  });
  $("#modal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || !dialogAction) return;
    submitting = true;
    $("#modal-submit").disabled = true;
    $("#modal-error").textContent = "";
    try {
      await dialogAction(new FormData(event.target));
    } catch (error) {
      $("#modal-error").textContent = error.message;
    } finally {
      submitting = false;
      $("#modal-submit").disabled = false;
    }
  });

  $("#create").addEventListener("click", async () => {
    if (page === "secrets") {
      openDialog(
        "Store a secret",
        '<p>Using an existing name replaces its value and increments its version.</p><label for="secret-name">Name</label><input id="secret-name" name="name" pattern="[a-z](?:[a-z0-9]|-)*" maxlength="63" required><label for="secret-value">Secret value</label><input id="secret-value" name="value" type="password" autocomplete="off" maxlength="8192" required>',
        "Store secret",
        async (form) => {
          await api("/secrets", {
            method: "PUT",
            body: JSON.stringify(Object.fromEntries(form)),
          });
          closeDialog();
          notice("Secret stored securely.");
          load();
        },
      );
    } else if (page === "keys") {
      openDialog(
        "Create an API key",
        '<label for="key-name">Name</label><input id="key-name" name="name" pattern="[a-z](?:[a-z0-9]|-)*" maxlength="63" required><label for="key-role">Role</label><select id="key-role" name="role"><option value="viewer">Viewer · Read metadata and objects</option><option value="operator">Operator · Manage resources and secrets</option><option value="owner">Owner · Full workspace access</option></select>',
        "Create key",
        async (form) => {
          const result = await api("/keys", {
            method: "POST",
            body: JSON.stringify(Object.fromEntries(form)),
          });
          openDialog(
            "Save your API key",
            `<p>This key is shown once. Store it in your password manager.</p><div class="secret-value">${escape(result.token)}</div><p>Expires ${date(result.expires_at)}. Closing this dialog clears the displayed key.</p>`,
            "",
            null,
          );
          load();
        },
      );
    } else {
      const initial = [
        "compute",
        "bucket",
        "database",
        "loadbalancer",
      ].includes(page)
        ? page
        : "compute";
      const key = crypto.randomUUID();
      openDialog(
        "Create a resource",
        `<label for="resource-kind">Service</label><select id="resource-kind" name="kind">${["compute", "bucket", "database", "loadbalancer"].map((kind) => `<option value="${kind}" ${kind === initial ? "selected" : ""}>${pages[kind][0]}</option>`).join("")}</select><label for="resource-name">Resource name</label><input id="resource-name" name="name" placeholder="my-first-workload" pattern="[a-z](?:[a-z0-9]|-)*" maxlength="63" required><div class="form-pair"><div><label for="resource-cpu">vCPU</label><select name="cpu" id="resource-cpu"><option value="1">1 vCPU</option><option value="2">2 vCPU</option></select></div><div><label for="resource-memory">Memory</label><select name="memory" id="resource-memory"><option value="512">512 MB</option><option value="1024">1024 MB</option><option value="256">256 MB</option></select></div></div><div id="targets"></div><p class="helper">Names use lowercase letters, numbers, and hyphens. Provisioning runs asynchronously.</p>`,
        "Create resource",
        async (form) => {
          const result = await api("/resources", {
            method: "POST",
            headers: { "Idempotency-Key": key },
            body: JSON.stringify({
              kind: form.get("kind"),
              name: form.get("name"),
              spec: {
                cpu: Number(form.get("cpu")),
                memory_mb: Number(form.get("memory")),
                targets: form.getAll("targets"),
              },
            }),
          });
          closeDialog();
          notice(
            `Provisioning accepted. Operation ${result.id.slice(0, 8)} is queued.`,
          );
          load();
        },
      );
      const updateTargets = async () => {
        const kind = $("#resource-kind").value;
        $("#targets").replaceChildren();
        $(".form-pair").hidden = kind === "bucket";
        if (kind !== "loadbalancer") return;
        try {
          const result = await api("/resources?kind=compute&limit=100");
          if (
            !$("#modal").open ||
            $("#resource-kind")?.value !== "loadbalancer"
          )
            return;
          const targets = result.items.filter((x) => x.state === "ready");
          $("#targets").innerHTML =
            "<label>Ready compute targets</label>" +
            (targets.length
              ? targets
                  .map(
                    (x) =>
                      `<div class="check-row"><input type="checkbox" name="targets" id="target-${x.id}" value="${x.id}"><label for="target-${x.id}">${escape(x.name)}</label></div>`,
                  )
                  .join("")
              : '<p class="helper">Create a compute resource and wait for it to become ready first.</p>');
        } catch (error) {
          $("#modal-error").textContent = error.message;
        }
      };
      $("#resource-kind").addEventListener("change", updateTargets);
      updateTargets();
    }
  });

  $("#content").addEventListener("change", (event) => {
    if (event.target.id === "bucket-select") {
      selectedBucket = event.target.value;
      load();
    }
  });
  $("#content").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const { action, id, key } = button.dataset;
    try {
      if (action === "next" || action === "previous") {
        offset = Math.max(
          0,
          offset + (action === "next" ? pageSize : -pageSize),
        );
        await load();
      } else if (action === "inspect") {
        const resource = resourceCache.find((x) => x.id === id);
        openDialog(
          resource.name,
          `<dl class="detail-grid"><dt>Resource ID</dt><dd>${escape(resource.id)}</dd><dt>State</dt><dd>${badge(resource.state)}</dd><dt>Created</dt><dd>${date(resource.created_at)}</dd></dl><pre class="detail-json">${escape(JSON.stringify({ spec: resource.spec, output: resource.output }, null, 2))}</pre>`,
          "",
          null,
        );
      } else if (
        ["delete", "revoke", "delete-secret", "delete-object"].includes(action)
      ) {
        const resource = resourceCache.find((x) => x.id === id);
        const target =
          action === "delete"
            ? resource.name
            : action === "delete-object"
              ? key
              : id;
        const idem = crypto.randomUUID();
        openDialog(
          "Confirm deletion",
          `<p>Remove <strong>${escape(target)}</strong>?</p>${resource?.kind === "database" ? '<div class="warning">This deletes the database container, its persistent volume, and credentials. Database data cannot be recovered without a separate backup.</div>' : "<p>This action cannot be undone from the console.</p>"}`,
          action === "revoke" ? "Revoke key" : "Delete",
          async () => {
            const path =
              action === "delete"
                ? `/resources/${id}`
                : action === "revoke"
                  ? `/keys/${id}`
                  : action === "delete-secret"
                    ? `/secrets/${id}`
                    : `/buckets/${selectedBucket}/objects/${encodeURIComponent(key)}`;
            await api(path, {
              method: "DELETE",
              headers: { "Idempotency-Key": idem },
            });
            closeDialog();
            notice(
              action === "delete"
                ? "Deletion queued. Track progress in Operations."
                : "Removed successfully.",
            );
            load();
          },
        );
      } else if (action === "reveal") {
        const result = await api(`/secrets/${id}/reveal`, { method: "POST" });
        openDialog(
          "Secret value",
          `<p>This access has been recorded in the audit trail.</p><div class="secret-value">${escape(result.value)}</div><p>Close this dialog to clear the displayed value.</p>`,
          "",
          null,
        );
      } else if (action === "upload") {
        const bucket = selectedBucket;
        openDialog(
          "Upload an object",
          '<label for="object-file">Choose file (maximum 10 MB)</label><input type="file" id="object-file" name="file" required><label for="object-key">Object key (optional)</label><input id="object-key" name="key" placeholder="Defaults to filename" maxlength="512"><p>Uploading to an existing key replaces its contents.</p>',
          "Upload",
          async (form) => {
            const file = form.get("file");
            const name = form.get("key") || file.name;
            if (file.size > 10485760)
              throw new Error("The console upload limit is 10 MB.");
            await api(
              `/buckets/${bucket}/objects/${encodeURIComponent(name)}`,
              {
                method: "PUT",
                body: file,
                headers: { "Content-Type": "application/octet-stream" },
              },
            );
            closeDialog();
            notice("Object uploaded and integrity hash recorded.");
            load();
          },
        );
      } else if (action === "download") {
        const response = await api(
          `/buckets/${selectedBucket}/objects/${encodeURIComponent(key)}`,
          { raw: true },
        );
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = key.split("/").pop() || "object";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (error) {
      notice(error.message, true);
    }
  });
})();
