"use strict";

const defaultTitle = "歷史資料一覽表";
const titleKey = "access-report-title";
const formatNumber = new Intl.NumberFormat("zh-Hant-TW");
const formatSyncTime = new Intl.DateTimeFormat("zh-Hant-TW", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const byId = (id) => document.getElementById(id);
const fields = byId("filter-fields");
const filters = byId("filters");
const yearSelect = byId("year");
const summary = byId("result-summary");
const recordsFrame = byId("records-frame");
const tableScroll = byId("table-scroll");
const pageSize = byId("page-size");
const previous = byId("previous-page");
const next = byId("next-page");
const jumpPage = byId("jump-page");
const jumpSubmit = byId("jump-submit");
const print = byId("print");
const exportButton = byId("export");
const start = byId("start");
const end = byId("end");
const filterError = byId("filter-error");
const feedback = byId("feedback");
const titleInput = byId("title-input");
const titleDialog = byId("title-dialog");
const columnNames = ["device", "door", "department", "name", "employeeId", "card", "datetime", "event"];
const optionFields = [
  ["department", "departments", "全部部門"],
  ["device", "devices", "全部裝置"],
  ["door", "doors", "全部門禁點"],
  ["event", "events", "全部事件"],
];

let year = null;
let years = [];
let reportTitle = storedTitle();
let applied = null;
let loadingOptions = false;
let recordRequest = null;
let retryAction = null;
let statusTimer = null;
let snapshotTimer = null;
let refreshId = 0;
let activeSource = "";
let optionsSnapshotId = null;
let exporting = false;

function storedTitle() {
  try {
    return localStorage.getItem(titleKey) || defaultTitle;
  } catch {
    return defaultTitle;
  }
}

function updateReportTitle() {
  byId("report-heading").textContent = reportTitle;
}

async function readApi(path, params, signal) {
  const query = params?.size ? `?${params}` : "";
  const response = await fetch(path + query, { signal }).catch(() => {
    throw new Error("目前無法連到查詢服務，請重試。");
  });
  const data = await response.json().catch(() => {
    throw new Error("無法讀取查詢資料，請重試。");
  });
  if (!response.ok) {
    const message = data.message || data.error;
    const error = new Error(typeof message === "string" ? message : "服務暫時無法回應，請重試。");
    throw Object.assign(error, { status: response.status, state: data.state, source: data.source });
  }
  return data;
}

function updateSource({ state, source, snapshot }) {
  byId("source").dataset.state = state;
  let label = state === "ready" ? "資料庫已就緒" : state === "error" ? "資料庫無法讀取" : "正在讀取資料";
  if (state === "ready" && snapshot) {
    const synced = new Date(snapshot.syncedAt);
    if (!Number.isNaN(synced.getTime())) label = `同步於 ${formatSyncTime.format(synced)}`;
  }
  const sourceStatus = byId("source-status");
  if (sourceStatus.textContent !== label) sourceStatus.textContent = label;

  if (source) {
    const filename = byId("source-name");
    if (filename.textContent !== source) filename.textContent = source;
    filename.hidden = false;
  }
}

function showFeedback(message, retry) {
  byId("feedback-message").textContent = message;
  retryAction = retry;
  feedback.hidden = false;
}

function clearFeedback() {
  feedback.hidden = true;
  retryAction = null;
}

function showTableMessage(heading, detail) {
  const row = document.createElement("tr");
  const cell = row.insertCell();
  cell.colSpan = columnNames.length;
  cell.className = "empty-cell";
  const title = document.createElement("strong");
  title.textContent = heading;
  cell.append(title);
  if (detail) {
    const description = document.createElement("span");
    description.textContent = detail;
    cell.append(description);
  }
  byId("records").replaceChildren(row);
}

function pagination() {
  const pages = applied ? Math.ceil(applied.total / applied.pageSize) : 0;
  const page = pages ? applied.page : 0;
  return { page, pages };
}

function updateControls() {
  const busy = loadingOptions || recordRequest !== null;
  const { page, pages } = pagination();
  const hasRows = pages > 0;

  yearSelect.disabled = busy || years.length === 0;
  byId("year-query").disabled = yearSelect.disabled;
  fields.disabled = busy || year === null;
  pageSize.disabled = busy || applied === null;
  previous.disabled = busy || page < 2;
  next.disabled = busy || page >= pages;
  jumpPage.max = Math.max(1, pages);
  jumpPage.disabled = jumpSubmit.disabled = busy || pages < 2;
  print.disabled = busy || !hasRows;
  exportButton.disabled = busy || !hasRows || exporting;
  recordsFrame.setAttribute("aria-busy", String(busy));
}

function renderRows(rows) {
  if (rows.length === 0) {
    showTableMessage("沒有符合條件的紀錄", "可調整日期時間或清除部分查詢條件。");
    return;
  }

  const rowElements = document.createDocumentFragment();
  for (const record of rows) {
    const row = document.createElement("tr");
    for (const column of columnNames) {
      const cell = row.insertCell();
      const rawValue = record[column];
      const value = rawValue === null || rawValue === undefined ? "" : String(rawValue);
      cell.textContent = value || "—";
      if (!value) cell.classList.add("cell-missing");
      if (column === "employeeId" || column === "card") cell.classList.add("cell-number");
      if (column === "name") cell.classList.add("cell-name");
      if (column === "datetime") {
        cell.classList.add("cell-datetime");
        if (value) cell.textContent = value.replace("T", " ");
      }
    }
    rowElements.append(row);
  }
  byId("records").replaceChildren(rowElements);
}

function renderResults(resetScroll = true) {
  const { page, pages } = pagination();
  summary.textContent = `${applied.params.get("year")} 年度 · 共 ${formatNumber.format(applied.total)} 筆`;
  byId("page-label").textContent = `第 ${formatNumber.format(page)} / ${formatNumber.format(pages)} 頁`;
  pageSize.value = String(applied.pageSize);
  renderRows(applied.rows);
  if (resetScroll) {
    jumpPage.value = "";
    tableScroll.scrollTop = 0;
  }
}

async function runQuery(params, page = 1, size = Number(pageSize.value)) {
  recordRequest?.abort();
  const request = new AbortController();
  recordRequest = request;
  const query = new URLSearchParams(params);
  query.set("page", page);
  query.set("pageSize", size);

  clearFeedback();
  updateControls();
  summary.textContent = "正在查詢…";
  if (!applied) showTableMessage("正在查詢門禁紀錄");

  try {
    const result = await readApi("/api/records", query, request.signal);
    if (recordRequest !== request) return;
    applied = { ...result, params: new URLSearchParams(params) };
    renderResults();
    updateSource({ state: "ready", source: activeSource, snapshot: result.snapshot });
    return true;
  } catch (error) {
    if (request.signal.aborted) return;
    if (applied) {
      renderResults();
    } else {
      summary.textContent = "查詢失敗";
      showTableMessage("暫時無法顯示紀錄");
    }
    showFeedback(error.message, () => runQuery(params, page, size));
  } finally {
    if (recordRequest === request) {
      recordRequest = null;
      updateControls();
    }
  }
}

async function navigatePage(page) {
  if (await runQuery(applied.params, page)) {
    tableScroll.focus({ preventScroll: true });
    byId("results").scrollIntoView({ block: "start" });
  }
}

function populateYears(available, selected) {
  years = available;
  yearSelect.replaceChildren(...years.map((value) => new Option(String(value), value)));
  if (selected == null) yearSelect.add(new Option("無可查詢年度", ""));
  else yearSelect.value = String(selected);
}

function populateOptions(data, preserveFilters = false) {
  const selectedYear = yearSelect.value || data.year;
  year = data.year ?? null;
  optionsSnapshotId = data.snapshot?.id ?? null;
  if (!preserveFilters || data.years.some((value, i) => value !== years[i]) || data.years.length !== years.length) {
    populateYears(data.years, preserveFilters && data.years.includes(Number(selectedYear)) ? selectedYear : year);
  }

  for (const [id, key, label] of optionFields) {
    const select = byId(id);
    const selected = preserveFilters ? select.value : "";
    const options = data[key].map(({ value, label: text }) => new Option(text, value));
    if (selected && !options.some((item) => item.value === selected)) options.push(new Option(selected, selected));
    select.replaceChildren(new Option(label, "", true), ...options);
    select.value = selected;
  }

  if (year !== null) {
    for (const input of [start, end]) {
      input.min = `${year}-01-01T00:00`;
      input.max = `${year}-12-31T23:59`;
    }
  }
}

async function readYearOptions(params) {
  let notified = false;
  while (true) {
    try {
      return await readApi("/api/options", params);
    } catch (error) {
      if (error.status !== 503 || error.state !== "loading") throw error;
      if (!notified) {
        const selectedYear = params.get("year");
        updateSource({ state: "loading", source: error.source });
        summary.textContent = selectedYear ? `正在讀取 ${selectedYear} 年度資料…` : "正在讀取年度資料…";
        if (!applied) showTableMessage("正在讀取門禁紀錄", error.message);
        notified = true;
      }
      await new Promise((resolve) => setTimeout(resolve, 1700));
    }
  }
}

async function loadYear(selection) {
  clearFeedback();
  loadingOptions = true;
  updateControls();
  summary.textContent = "正在載入年度…";
  const params = new URLSearchParams();
  if (selection) params.set("year", selection);

  try {
    const options = await readYearOptions(params);
    activeSource = options.source || activeSource;
    updateSource({ state: "ready", source: activeSource, snapshot: options.snapshot });
    filters.reset();
    populateOptions(options);
    if (year === null) {
      summary.textContent = "尚無可查詢年度";
      showTableMessage("資料庫沒有門禁紀錄");
      return;
    }
    await runQuery(new URLSearchParams({ year }));
  } catch (error) {
    if (applied) {
      renderResults();
      updateSource({ state: "ready", source: activeSource, snapshot: applied.snapshot });
    } else {
      summary.textContent = "年度載入失敗";
      showTableMessage("無法載入查詢選項");
      updateSource({ state: "error", source: error.source });
    }
    showFeedback(error.message, () => loadYear(selection));
  } finally {
    loadingOptions = false;
    updateControls();
  }
}

async function refreshPublishedPreview() {
  try {
    if (document.hidden || loadingOptions || recordRequest || exporting) return;
    const current = applied;
    const generation = refreshId;
    const status = await readApi("/api/status", new URLSearchParams({ live: "1" }));
    const mayRefresh = () => (
      generation === refreshId && current === applied && !loadingOptions && !recordRequest && !exporting
    );
    if (!mayRefresh() || status.state !== "ready") return;
    const snapshot = status.snapshots?.[year ?? status.year];
    if (year === null && snapshot) {
      await loadYear();
      return;
    }
    if (status.years.some((value, i) => value !== years[i]) || status.years.length !== years.length) {
      const selected = Number(yearSelect.value);
      populateYears(status.years, status.years.includes(selected) ? selected : (year ?? status.year));
    }
    if (!snapshot || !current || (snapshot.id === current.snapshot?.id && snapshot.id === optionsSnapshotId)) return;

    const params = new URLSearchParams(current.params);
    params.set("page", current.page);
    params.set("pageSize", current.pageSize);
    const [options, result] = await Promise.all([
      readApi("/api/options", new URLSearchParams({ year })),
      readApi("/api/records", params),
    ]);
    if (!mayRefresh() || result.snapshot?.id !== snapshot.id || options.snapshot?.id !== snapshot.id) return;
    if (!result.rows.length && result.page > 1) {
      params.set("page", 1);
      const first = await readApi("/api/records", params);
      if (!mayRefresh() || first.snapshot?.id !== snapshot.id) return;
      Object.assign(result, first);
    }
    populateOptions(options, true);
    activeSource = options.source || activeSource;
    applied = { ...result, params: current.params };
    renderResults(false);
    updateControls();
    updateSource({ state: "ready", source: activeSource, snapshot: result.snapshot });
  } catch {
    // Keep the last displayed results, and retry the next time the preview checks for updates.
  } finally {
    snapshotTimer = setTimeout(refreshPublishedPreview, 10_000);
  }
}

async function initialize() {
  clearTimeout(statusTimer);
  clearFeedback();
  const current = ++refreshId;
  summary.textContent = "正在讀取資料";
  loadingOptions = true;
  updateControls();
  showTableMessage("正在讀取門禁紀錄", "首次讀取較大的資料庫可能需要一些時間。");
  updateSource({ state: "loading" });

  const poll = async (refresh = false) => {
    try {
      const params = refresh ? new URLSearchParams({ refresh: "1" }) : undefined;
      const status = await readApi("/api/status", params);
      if (refreshId !== current) return;
      if (status.snapshots && snapshotTimer === null) {
        snapshotTimer = setTimeout(refreshPublishedPreview, 10_000);
      }
      updateSource(status);
      if (status.state === "ready") {
        activeSource = status.source || activeSource;
        if (Array.isArray(status.years)) populateYears(status.years, status.year);
        await loadYear();
        return;
      }
      if (status.state === "error") throw new Error(status.message || "無法讀取資料庫。");
      if (status.message && summary.textContent !== status.message) summary.textContent = status.message;
      statusTimer = setTimeout(poll, 1700);
    } catch (error) {
      if (refreshId !== current) return;
      updateSource({ state: "error" });
      loadingOptions = false;
      updateControls();
      summary.textContent = "資料庫尚未就緒";
      showTableMessage("暫時無法顯示紀錄");
      showFeedback(error.message, initialize);
    }
  };

  await poll(true);
}

function clearFilterError() {
  filterError.hidden = true;
  start.removeAttribute("aria-invalid");
  end.removeAttribute("aria-invalid");
}

function submitFilters(event) {
  event.preventDefault();
  clearFilterError();
  if (start.value && end.value && start.value > end.value) {
    filterError.textContent = "結束日期時間須晚於或等於起始日期時間。";
    filterError.hidden = false;
    end.setAttribute("aria-invalid", "true");
    end.focus();
    return;
  }

  const params = new URLSearchParams({ year });
  for (const [key, value] of new FormData(filters)) {
    if (value.trim()) params.set(key, value.trim());
  }
  runQuery(params);
}

byId("year-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (yearSelect.value) loadYear(yearSelect.value);
});
filters.addEventListener("submit", submitFilters);
filters.addEventListener("reset", clearFilterError);
byId("clear-filters").addEventListener("click", () => {
  filters.reset();
  runQuery(new URLSearchParams({ year }));
});
start.addEventListener("input", clearFilterError);
end.addEventListener("input", clearFilterError);

byId("retry").addEventListener("click", () => retryAction?.());
pageSize.addEventListener("change", () => navigatePage(1));
previous.addEventListener("click", () => navigatePage(applied.page - 1));
next.addEventListener("click", () => navigatePage(applied.page + 1));
byId("page-jump").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!jumpPage.disabled) navigatePage(jumpPage.valueAsNumber);
});
print.addEventListener("click", () => window.print());

async function downloadReport() {
  if (!applied || exporting) return;
  const params = new URLSearchParams(applied.params);
  params.set("title", reportTitle);
  const label = byId("export-label");
  const status = byId("export-status");
  exporting = true;
  label.textContent = "正在匯出…";
  status.textContent = "正在準備 Excel 檔案。";
  clearFeedback();
  updateControls();

  try {
    const response = await fetch(`/api/export?${params}`);
    if (!response.ok) throw new Error();
    const header = response.headers.get("Content-Disposition");
    const encodedName = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encodedName ? decodeURIComponent(encodedName) : `${defaultTitle}.xlsx`;
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    status.textContent = "已送出 Excel 下載。";
  } catch {
    status.textContent = "";
    showFeedback("無法匯出 Excel，請重試。", downloadReport);
  } finally {
    exporting = false;
    label.textContent = "匯出 Excel";
    updateControls();
  }
}

exportButton.addEventListener("click", downloadReport);

byId("edit-title").addEventListener("click", () => {
  titleInput.value = reportTitle;
  titleDialog.showModal();
  titleInput.select();
});
byId("cancel-title").addEventListener("click", () => titleDialog.close());
byId("title-form").addEventListener("submit", (event) => {
  event.preventDefault();
  reportTitle = titleInput.value.trim() || defaultTitle;
  try {
    localStorage.setItem(titleKey, reportTitle);
  } catch {
    // The title still applies to this session when browser storage is unavailable.
  }
  updateReportTitle();
  titleDialog.close();
});

updateReportTitle();
initialize();
