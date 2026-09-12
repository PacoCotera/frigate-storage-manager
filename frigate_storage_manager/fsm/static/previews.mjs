const $ = (id) => document.getElementById(id);
const active = (task) => ["queued", "running"].includes(task?.state);
const phases = {
  queued: "Waiting to start", connecting: "Connecting to Frigate",
  checking_storage: "Checking the NFS mount", reading_database: "Reading the database",
  protecting_history: "Checking bookmarks and linked history", selecting_media: "Selecting eligible media",
  checking_files: "Checking selected records and media files", checking_references: "Checking related records",
  building_details: "Preparing item explanations", verifying_storage: "Verifying the NFS mount again",
  saving_result: "Saving the preview", completed: "Preview complete", failed: "Preview failed",
  interrupted: "Preview interrupted",
};

export function createPreviewUI({ api, bytes, labels, onBusy, onReady, onRequest, onError, invalidate }) {
  let task = null, submitting = false, serverBusy = false, poll = null;
  let lastShown = null, received = 0, page = 0, generation = 0;

  function busy() { return submitting || active(task) || serverBusy; }
  function notify() { onBusy(busy()); }
  function status() {
    if (!task && !submitting && !serverBusy) return;
    $("preview-status-section").hidden = false;
    $("preview-history").disabled = busy();
    $("preview-show").disabled = busy() || !$("preview-history").value;
    const elapsed = task ? task.elapsed_seconds + (active(task) ? (Date.now() - received) / 1000 : 0) : 0;
    $("preview-status").textContent = serverBusy && !active(task)
      ? "Another user is creating a preview. Waiting for it to finish…" : task
      ? `${phases[task.phase] || "Preparing preview"} · ${Math.floor(elapsed)} seconds${task.total ? ` · ${task.processed} of ${task.total} records` : ""}`
      : (submitting ? "Submitting preview…" : "Another user is creating a preview. Waiting for it to finish…");
    const bar = $("preview-progress");
    bar.hidden = !busy();
    if (task?.total > 0 && task.processed != null) { bar.max = task.total; bar.value = task.processed; }
    else bar.removeAttribute("value");
    if (task) {
      const r = task.request;
      $("preview-scope").textContent = `${r.target} · ${r.cameras.join(", ")} · Before ${new Date(r.cutoff).toLocaleString()} (${r.cutoff}) · Exports ${r.include_exports ? "included" : "preserved"}`;
    }
  }

  function schedule() {
    clearTimeout(poll);
    if (active(task) || serverBusy) poll = setTimeout(check, 1500);
  }
  async function check() {
    try {
      if (active(task)) await accept(await api(`previews/${task.id}`));
      else await acceptStatus(await api("status"), false);
      $("preview-connection").textContent = active(task) ? "You can close this page and return to the saved preview." : $("preview-connection").textContent;
    } catch (e) {
      $("preview-connection").textContent = "Connection interrupted. Reconnecting to the existing preview…";
    } finally { schedule(); }
  }
  async function accept(value, restoreFields = false) {
    if (task?.id !== value.id) invalidate();
    task = value; received = Date.now(); submitting = false; serverBusy = false;
    const option = [...$("preview-history").options].find((o) => o.value === task.id);
    if (option) option.textContent = historyLabel(task);
    if (restoreFields) onRequest(task.request);
    status(); notify();
    if (active(task)) {
      $("preview-connection").textContent = "You can close this page and return to the saved preview.";
    } else if (task.state === "completed") {
      $("preview-connection").textContent = task.expired
        ? "This preview expired or was replaced. Its summary remains visible; create a fresh preview to inspect items."
        : `Saved until ${new Date(task.expires_at * 1000).toLocaleString()}. Up to four previews are retained. This is a snapshot, not a live inventory.`;
      const key = `${task.id}:${task.expired}`;
      if (lastShown !== key) {
        lastShown = key; page = 0; generation++;
        onReady(task);
        $("inspection").hidden = task.expired;
        $("inspection-search").value = "";
        $("inspection-view").value = "selected";
        $("inspection-kind").value = "";
        if (!task.expired) await loadItems();
      }
    } else {
      $("preview-connection").textContent = task.error || "Create a new preview to retry.";
      if (task.diagnostics) {
        $("diagnostics").textContent = JSON.stringify(task.diagnostics, null, 2);
        $("validation-details").open = true;
      }
    }
    schedule();
  }
  const historyLabel = (value) => `${new Date(value.created * 1000).toLocaleString()} · ${value.state}${value.expired ? " (expired)" : ""}`;
  function history(tasks) {
    const select = $("preview-history");
    select.replaceChildren();
    for (const value of tasks) {
      const option = document.createElement("option");
      option.value = value.id;
      option.textContent = historyLabel(value);
      select.append(option);
    }
    if (task) select.value = task.id;
    $("preview-show").disabled = busy() || !select.value;
  }
  async function acceptStatus(state, restore = false) {
    history(state.previews || []);
    serverBusy = state.preview_busy;
    const running = state.previews?.find(active);
    if (running) await accept(running, restore);
    else if (restore && state.previews?.length) await accept(state.previews[0], true);
    else { status(); notify(); schedule(); }
  }
  async function start(request) {
    if (busy()) return;
    invalidate();
    task = null; submitting = true; lastShown = null;
    $("preview-connection").textContent = "";
    $("preview-scope").textContent = "";
    status(); notify();
    $("preview-status-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
    const id = Array.from(crypto.getRandomValues(new Uint8Array(24)), (x) => x.toString(16).padStart(2, "0")).join("");
    try {
      const accepted = await api("preview", { ...request, request_id: id });
      await accept(accepted, accepted.id !== id);
      history((await api("status")).previews || []);
    } catch (e) {
      submitting = false;
      // The POST may have reached the app even if its response was lost.
      try {
        const state = await api("status");
        const existing = state.previews?.find((p) => p.id === id || active(p));
        if (existing) { history(state.previews); await accept(existing, true); return; }
        serverBusy = state.preview_busy;
      } catch { /* Reopening the page retrieves the durable server task. */ }
      $("preview-status").textContent = "Preview request could not be confirmed.";
      $("preview-progress").hidden = true;
      $("preview-connection").textContent = "Reopen this page to check for a saved preview before retrying.";
      onError(e);
    } finally { notify(); schedule(); }
  }

  function cell(row, content) { const td = document.createElement("td"); td.textContent = content; row.append(td); return td; }
  const date = (value) => typeof value === "number" && Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : "—";
  async function loadItems() {
    if (!task || task.state !== "completed" || task.expired) return;
    const serial = ++generation, token = task.id;
    const view = $("inspection-view").value;
    const params = new URLSearchParams({ view, kind: $("inspection-kind").value, search: $("inspection-search").value, page });
    $("inspection-count").textContent = "Loading saved items…";
    $("inspection-items").replaceChildren();
    $("inspection-previous").disabled = true; $("inspection-next").disabled = true;
    try {
      const data = await api(`previews/${token}/items?${params}`);
      if (serial !== generation || token !== task?.id) return;
      const body = $("inspection-items"); body.replaceChildren();
      for (const item of data.items) {
        const row = document.createElement("tr");
        const name = cell(row, `${labels[item.kind] || item.kind}${item.camera ? ` · ${item.camera}` : ""}${item.label ? ` · ${item.label}` : ""}`);
        const id = document.createElement("code"); id.className = "item-id"; id.textContent = item.id; name.append(id);
        cell(row, `${date(item.start)}${item.end != null ? ` → ${date(item.end)}` : ""}`);
        const decision = cell(row, item.reason);
        if (item.related) {
          const button = document.createElement("button"); button.className = "secondary compact";
          button.textContent = `Find related ${labels[item.related.kind] || item.related.kind}: ${item.related.id}`;
          button.onclick = () => { $("inspection-kind").value = item.related.kind; $("inspection-search").value = item.related.id; page = 0; loadItems(); };
          decision.append(button);
        }
        if (item.files?.length) {
          const details = document.createElement("details"), title = document.createElement("summary");
          title.textContent = `${item.files.length} associated media file(s)`; details.append(title);
          for (const file of item.files) { const p = document.createElement("p"); p.className = "item-id"; p.textContent = `${file.path} · ${bytes(file.bytes)}`; details.append(p); }
          decision.append(details);
        }
        body.append(row);
      }
      const start = data.matched ? page * 50 + 1 : 0;
      let message = `${start}–${Math.min((page + 1) * 50, data.matched)} of ${data.matched} matching retained records.`;
      if (view === "preserved") {
        const kinds = $("inspection-kind").value ? [$("inspection-kind").value] : Object.keys(data.preserved_samples);
        message += " Preserved sample: " + kinds.filter((k) => data.preserved_samples[k]).map((k) => `${labels[k] || k} ${data.preserved_samples[k].shown}/${data.preserved_samples[k].total}`).join("; ") + ".";
        if (!data.matched) message += " A missing item may be outside this sample; this does not mean it was selected for cleanup.";
      } else if (!data.matched) message += " No selected records match this filter.";
      $("inspection-count").textContent = message;
      $("inspection-previous").disabled = page === 0;
      $("inspection-next").disabled = (page + 1) * 50 >= data.matched;
    } catch (e) { if (serial === generation) $("inspection-count").textContent = e.message; }
  }
  for (const kind of ["event", "reviewsegment", "recordings", "previews", "export", "timeline", "userreviewstatus", "vec_thumbnails", "vec_descriptions"]) {
    const option = document.createElement("option"); option.value = kind; option.textContent = labels[kind] || kind; $("inspection-kind").append(option);
  }
  $("inspection-form").onsubmit = (e) => { e.preventDefault(); page = 0; loadItems(); };
  for (const id of ["inspection-view", "inspection-kind"]) $(id).onchange = () => { page = 0; loadItems(); };
  $("inspection-previous").onclick = () => { page--; loadItems(); };
  $("inspection-next").onclick = () => { page++; loadItems(); };
  const showSaved = async () => { invalidate(); try { await accept(await api(`previews/${$("preview-history").value}`), true); } catch (e) { onError(e); } };
  $("preview-history").onchange = showSaved;
  $("preview-show").onclick = showSaved;
  setInterval(() => { if (active(task)) status(); }, 1000);
  return { start, acceptStatus, busy, invalidate: () => { generation++; lastShown = null; } };
}
