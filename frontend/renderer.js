"use strict";

import "./index.css";
import { initWebGpuGlass } from "./webgpu-glass.js";

const PLAN_KEY = "renewly.plans.v1";
const SETTINGS_KEY = "renewly.settings.v1";
const currencies = ["INR", "USD", "EUR", "GBP", "CAD", "AUD"];
const pageNames = {
  dashboard: "Renewly Home Page",
  "add-plan": "Add Plan",
  analytics: "Analytics",
  assistant: "Smart Assistant",
  purchases: "Purchases",
  calendar: "Calendar",
  timeline: "Renewal Timeline",
  "recycle-bin": "Recycle Bin",
  search: "Search",
  delete: "Delete",
  account: "Account",
  "reset-password": "Reset Password",
  logout: "Logout Account",
  settings: "Settings",
};
const pageSubtitles = {
  dashboard: "Track every plan, renewal and reminder in one place.",
  "add-plan": "Add a subscription to your local plan list.",
  analytics: "See a quick summary of your recurring costs.",
  assistant: "A home for future subscription insights.",
  purchases: "Review the plans you are tracking.",
  calendar: "Keep upcoming renewal dates in view.",
  timeline: "Follow your renewals from soonest to latest.",
  "recycle-bin": "Review recently removed plans.",
  search: "Find a saved plan by name or note.",
  delete: "Remove a plan from this device.",
  account: "Your local Renewly profile.",
  "reset-password": "Manage account security.",
  logout: "Sign-out options for your profile.",
  settings: "Personalize Renewly.",
};
const pageElements = {
  dashboard: document.getElementById("dashboardPage"),
  "add-plan": document.getElementById("addPlanPage"),
  analytics: document.getElementById("analyticsPage"),
  assistant: document.getElementById("assistantPage"),
  purchases: document.getElementById("purchasesPage"),
  calendar: document.getElementById("calendarPage"),
  timeline: document.getElementById("timelinePage"),
  "recycle-bin": document.getElementById("recycleBinPage"),
  search: document.getElementById("searchPage"),
  delete: document.getElementById("deletePage"),
  account: document.getElementById("accountPage"),
  "reset-password": document.getElementById("resetPasswordPage"),
  logout: document.getElementById("logoutPage"),
  settings: document.getElementById("settingsPage"),
};
const navItems = [...document.querySelectorAll(".nav-item")];
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebarToggle");
document.querySelectorAll("[data-window-action]").forEach((button) => {
  button.addEventListener("click", () => {
    const actions = {
      minimize: window.renewlyWindow?.minimize,
      "toggle-maximize": window.renewlyWindow?.toggleMaximize,
      close: window.renewlyWindow?.close,
    };
    actions[button.dataset.windowAction]?.();
  });
});
const defaultSettings = { currency: "INR", clockFormat: "24", showSeconds: false, theme: "light" };
let plans = loadPlans();
let settings = loadSettings();
let previousClockValues = [];
let clockTimeout = null;
let pendingDeletion = null;
let queuedDeletionId = null;
let toastTimer = null;

function loadPlans() {
  try {
    const value = JSON.parse(localStorage.getItem(PLAN_KEY) || "[]");
    return Array.isArray(value) ? value.filter((plan) => plan && plan.id && plan.name) : [];
  } catch {
    return [];
  }
}

function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    return {
      ...defaultSettings,
      ...(stored && typeof stored === "object" ? stored : {}),
      currency: currencies.includes(stored?.currency) ? stored.currency : defaultSettings.currency,
      clockFormat: stored?.clockFormat === "12" ? "12" : "24",
      showSeconds: Boolean(stored?.showSeconds),
      theme: stored?.theme === "dark" ? "dark" : "light",
    };
  } catch {
    return { ...defaultSettings };
  }
}

function savePlans() {
  try {
    localStorage.setItem(PLAN_KEY, JSON.stringify(plans));
    return true;
  } catch {
    showToast("Could not save plans in this app profile.");
    return false;
  }
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    window.renewlyBridge?.saveSettings(settings); // desktop engine (main.py); no-op in Electron
    return true;
  } catch {
    showToast("Could not save settings in this app profile.");
    return false;
  }
}

function applyTheme() {
  const theme = settings.theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = theme;
  const toggle = document.getElementById("themeToggle");
  const isDark = theme === "dark";
  toggle.checked = isDark;
  toggle.setAttribute("aria-checked", String(isDark));
  toggle.setAttribute("aria-label", `Switch to ${isDark ? "light" : "dark"} mode`);
  document.getElementById("themeCaption").textContent = isDark ? "Dark mode" : "Light mode";
}
// Smooth, GPU-friendly theme change.
// Animating colours/shadows on every glass surface (each with backdrop blur) is what made the
// switch stutter. Instead the browser takes one picture of the old look and cross-fades it into
// the live new look on the compositor, which costs almost nothing per frame. Per-element CSS
// transitions are switched off while it runs (the "theme-switching" class).
function switchTheme() {
  const root = document.documentElement;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.classList.add("theme-switching");
  const done = () => root.classList.remove("theme-switching");

  if (typeof document.startViewTransition === "function" && !reduceMotion) {
    const transition = document.startViewTransition(() => { applyTheme(); });
    transition.finished.then(done, done);
    return;
  }
  // Fallback for engines without View Transitions: change in one paint.
  applyTheme();
  void document.body.offsetWidth;
  requestAnimationFrame(() => requestAnimationFrame(done));
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatMoney(amount, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format(Number(amount));
  } catch {
    return `${currency} ${Number(amount).toFixed(2)}`;
  }
}

function monthlyEquivalent(plan) {
  const amount = Number(plan.amount) || 0;
  if (plan.frequency === "weekly") return amount * 52 / 12;
  if (plan.frequency === "quarterly") return amount / 3;
  if (plan.frequency === "half-yearly") return amount / 6;
  if (plan.frequency === "yearly") return amount / 12;
  return amount;
}

function localDate(isoDate) {
  if (!isoDate) return null;
  const [year, month, day] = isoDate.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(isoDate, options = { year: "numeric", month: "short", day: "numeric" }) {
  const date = localDate(isoDate);
  return date ? date.toLocaleDateString(undefined, options) : "Date not set";
}

function dateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function daysUntil(isoDate) {
  const date = localDate(isoDate);
  if (!date) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((date.getTime() - today.getTime()) / 86400000);
}

function renewalLabel(isoDate) {
  const days = daysUntil(isoDate);
  if (days === null) return formatDate(isoDate);
  if (days < 0) return `Overdue by ${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"}`;
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days <= 30) return `In ${days} days`;
  return formatDate(isoDate);
}

function byRenewal(a, b) {
  return String(a.renewalDate || "9999-12-31").localeCompare(String(b.renewalDate || "9999-12-31"));
}

function renderDashboard() {
  const activePlanCount = document.getElementById("activePlanCount");
  const monthlySpend = document.getElementById("monthlySpend");
  const nextRenewalSummary = document.getElementById("nextRenewalSummary");
  const nextRenewalName = document.getElementById("nextRenewalName");
  const planTableBody = document.getElementById("planTableBody");
  const planTableWrap = document.getElementById("planTableWrap");
  const dashboardEmpty = document.getElementById("dashboardEmpty");
  const upcomingList = document.getElementById("upcomingList");
  const upcomingEmpty = document.getElementById("upcomingEmpty");
  const emptyCta = document.getElementById("glassBar");

  activePlanCount.textContent = String(plans.length);
  document.getElementById("accountPlanCount").textContent = String(plans.length);

  const totals = new Map();
  plans.forEach((plan) => totals.set(plan.currency, (totals.get(plan.currency) || 0) + monthlyEquivalent(plan)));
  monthlySpend.replaceChildren();
  if (totals.size) {
    [...totals.entries()].sort(([a], [b]) => a.localeCompare(b)).forEach(([currency, amount]) => {
      monthlySpend.append(makeElement("span", "", formatMoney(amount, currency)));
    });
  } else {
    monthlySpend.textContent = formatMoney(0, settings.currency);
  }

  const ordered = [...plans].sort(byRenewal);
  const next = ordered.find((plan) => plan.renewalDate);
  nextRenewalSummary.textContent = next ? renewalLabel(next.renewalDate) : "—";
  nextRenewalName.textContent = next ? next.name : "No plans yet";

  planTableBody.replaceChildren();
  ordered.forEach((plan) => {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.className = "plan-name-cell";
    nameCell.append(makeElement("span", "plan-icon", plan.icon || "📦"), document.createTextNode(plan.name));
    const badges = [plan.category, plan.paymentStatus === "PAID" ? "Paid" : (plan.paymentStatus ? "Payment pending" : ""), plan.autoRenew ? "Auto-renew" : ""].filter(Boolean);
    if (badges.length) nameCell.append(makeElement("span", "plan-meta-line", badges.join("  ·  ")));
    if (plan.notes) nameCell.append(makeElement("span", "plan-note-cell", plan.notes));
    const priceCell = makeElement("td", "", `${formatMoney(plan.amount, plan.currency)} / ${plan.frequency}`);
    const renewalCell = makeElement("td", "", formatDate(plan.renewalDate));
    const actionCell = makeElement("td", "plan-actions-cell");
    const editButton = makeElement("button", "row-edit-button", "Edit");
    editButton.type = "button";
    editButton.setAttribute("aria-label", `Edit ${plan.name}`);
    editButton.addEventListener("click", () => startEditPlan(plan.id));
    actionCell.append(editButton);
    row.append(nameCell, priceCell, renewalCell, actionCell);
    planTableBody.append(row);
  });

  const hasPlans = plans.length > 0;
  planTableWrap.hidden = !hasPlans;
  dashboardEmpty.hidden = hasPlans;
  emptyCta.classList.toggle("is-visible", !hasPlans);

  upcomingList.replaceChildren();
  ordered.slice(0, 5).forEach((plan) => {
    const item = makeElement("li", "renewal-item");
    const info = document.createElement("div");
    info.append(makeElement("span", "renewal-title", plan.name));
    const days = daysUntil(plan.renewalDate);
    const dateText = days !== null && days <= 30 ? renewalLabel(plan.renewalDate) : formatDate(plan.renewalDate);
    info.append(makeElement("span", "renewal-date", dateText));
    if (days !== null && days < 0) item.classList.add("renewal-item--overdue");
    item.append(info, makeElement("span", "renewal-amount", formatMoney(plan.amount, plan.currency)));
    upcomingList.append(item);
  });
  upcomingEmpty.hidden = ordered.length > 0;
}

function renderFeaturePages() {
  const ordered = [...plans].sort(byRenewal);
  const totals = new Map();
  plans.forEach((plan) => totals.set(plan.currency, (totals.get(plan.currency) || 0) + monthlyEquivalent(plan)));
  const analyticsSpend = document.getElementById("analyticsMonthlySpend");
  analyticsSpend.replaceChildren();
  if (totals.size) {
    [...totals.entries()].sort(([a], [b]) => a.localeCompare(b)).forEach(([currency, amount]) => {
      analyticsSpend.append(makeElement("span", "analytics-money", formatMoney(amount, currency)));
    });
  } else {
    analyticsSpend.textContent = formatMoney(0, settings.currency);
  }
  document.getElementById("analyticsPlanCount").textContent = String(plans.length);
  const next = ordered.find((plan) => plan.renewalDate);
  document.getElementById("analyticsNextRenewal").textContent = next ? renewalLabel(next.renewalDate) : "—";
  document.getElementById("analyticsNextName").textContent = next ? next.name : "No plans yet";

  document.getElementById("purchaseCount").textContent = `${plans.length} ${plans.length === 1 ? "plan" : "plans"}`;
  renderPlanRows("purchaseList", ordered, "No plans saved yet. Add a plan to get started.", "purchase-row");
  renderPlanRows("calendarList", ordered, "No renewal dates yet. Add plans to see them here.", "calendar-row");
  renderPlanRows("timelineList", ordered, "Your renewal timeline will appear here when you add plans.", "timeline-row");
}

function renderPlanRows(id, ordered, emptyMessage, rowClass) {
  const container = document.getElementById(id);
  container.replaceChildren();
  if (!ordered.length) {
    container.append(makeElement("p", "empty-state", emptyMessage));
    return;
  }
  ordered.forEach((plan) => {
    const row = makeElement("article", rowClass === "timeline-row" ? "feature-row feature-row--timeline" : `feature-row ${rowClass}`);
    const date = makeElement("span", "feature-row-date", formatDate(plan.renewalDate));
    const info = document.createElement("div");
    info.append(makeElement("strong", "feature-row-title", plan.name));
    info.append(makeElement("span", "feature-row-detail", renewalLabel(plan.renewalDate)));
    row.append(date, info, makeElement("strong", "feature-row-amount", `${formatMoney(plan.amount, plan.currency)} / ${plan.frequency}`));
    container.append(row);
  });
}

function renderSearch() {
  const query = document.getElementById("searchInput").value.trim().toLocaleLowerCase();
  const results = plans.filter((plan) => `${plan.name} ${plan.notes || ""}`.toLocaleLowerCase().includes(query));
  const list = document.getElementById("searchResults");
  list.replaceChildren();
  results.forEach((plan) => {
    const item = makeElement("li", "search-result");
    const info = document.createElement("div");
    info.append(makeElement("span", "search-result-title", plan.name));
    info.append(makeElement("span", "search-result-detail", `${formatMoney(plan.amount, plan.currency)} / ${plan.frequency} · Renews ${formatDate(plan.renewalDate)}`));
    const remove = makeElement("button", "secondary-button", "Delete");
    remove.type = "button";
    remove.addEventListener("click", () => {
      setActivePage("delete");
      document.getElementById("deletePlanSelect").value = plan.id;
      renderDeletePage();
    });
    item.append(info, remove);
    list.append(item);
  });
  document.getElementById("searchResultCount").textContent = `${results.length} ${results.length === 1 ? "plan" : "plans"}`;
  document.getElementById("searchEmpty").hidden = results.length > 0;
}

function renderDeletePage() {
  const select = document.getElementById("deletePlanSelect");
  const previousValue = select.value;
  select.replaceChildren();
  const ordered = [...plans].sort((a, b) => a.name.localeCompare(b.name));
  if (!ordered.length) {
    const option = new Option("No plans available", "");
    select.add(option);
  } else {
    select.add(new Option("Choose a plan", ""));
    ordered.forEach((plan) => select.add(new Option(`${plan.name} — ${formatMoney(plan.amount, plan.currency)}`, plan.id)));
    if (ordered.some((plan) => plan.id === previousValue)) select.value = previousValue;
  }
  const deleteButton = document.getElementById("deleteButton");
  deleteButton.disabled = !select.value || Boolean(pendingDeletion) || deleteButton.dataset.running === "true";
  document.getElementById("deleteButton").setAttribute("aria-label", select.value ? `Delete ${ordered.find((plan) => plan.id === select.value)?.name || "selected plan"}` : "Delete selected plan");
}

function renderCurrencyOptions() {
  [document.getElementById("currencySetting"), document.getElementById("planCurrency")].forEach((select) => {
    const selected = select.value;
    select.replaceChildren(...currencies.map((currency) => new Option(currency, currency)));
    select.value = currencies.includes(selected) ? selected : settings.currency;
  });
}

function renderSettings() {
  applyTheme();
  document.getElementById("currencySetting").value = settings.currency;
  document.getElementById("planCurrency").value = settings.currency;
  document.getElementById("clockFormat").value = settings.clockFormat;
  document.getElementById("showSeconds").checked = settings.showSeconds;
  updateClock();
}

function renderAll() {
  renderDashboard();
  renderFeaturePages();
  renderSearch();
  renderDeletePage();
  renderSettings();
}

function setActivePage(page) {
  if (page === "refresh") {
    plans = loadPlans();
    settings = loadSettings();
    renderAll();
    showToast("Plans refreshed from this device.");
    page = "dashboard";
  }
  if (!pageElements[page]) return;
  if (page === "add-plan") {
    if (!editRequested) resetPlanForm();
    editRequested = false;
  }
  navItems.forEach((item) => {
    const active = item.dataset.page === page;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  Object.entries(pageElements).forEach(([name, element]) => { element.hidden = name !== page; });
  document.getElementById("pageTitle").textContent = pageNames[page];
  document.getElementById("pageSubtitle").textContent = pageSubtitles[page];
  document.getElementById("pageEyebrow").textContent = page === "dashboard" ? "Good to see you" : "Renewly Glass Edition";
  if (page === "search") renderSearch();
  if (page === "delete") renderDeletePage();
  if (["analytics", "purchases", "calendar", "timeline"].includes(page)) renderFeaturePages();
}

function showToast(message, undo = false) {
  const toast = document.getElementById("undoToast");
  document.getElementById("undoMessage").textContent = message;
  document.getElementById("undoDeleteButton").hidden = !undo;
  toast.hidden = false;
  if (toastTimer) window.clearTimeout(toastTimer);
  if (!undo) toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3200);
}

const fallbackPlanMeta = {
  categories: ["📺 Entertainment", "☁️ Software / Cloud", "🏠 Home", "📱 Mobile", "💳 Finance", "🎓 Education", "📦 Other"],
  icons: ["📦", "🎬", "🎵", "☁️", "💻", "🎮", "🏋️", "📚", "🛒", "💳", "🧠", "🚗", "🏠", "❤️", "⭐"],
  defaultReminderDays: 3,
};
let editingPlanId = null;
let editRequested = false;

function planMeta() {
  const live = window.renewlyBridge?.info?.meta || {};
  return {
    categories: live.categories?.length ? live.categories : fallbackPlanMeta.categories,
    icons: live.icons?.length ? live.icons : fallbackPlanMeta.icons,
    defaultReminderDays: Number.isFinite(live.defaultReminderDays) ? live.defaultReminderDays : fallbackPlanMeta.defaultReminderDays,
  };
}

function fillSelect(select, values, selected) {
  select.replaceChildren(...values.map((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    return option;
  }));
  if (selected && !values.includes(selected)) {
    const extra = document.createElement("option");
    extra.value = selected;
    extra.textContent = selected;
    select.append(extra);
  }
  select.value = selected && select.querySelector(`option[value="${CSS.escape(selected)}"]`) ? selected : values[values.length - 1];
}

function resetPlanForm() {
  const meta = planMeta();
  editingPlanId = null;
  document.getElementById("addPlanForm").reset();
  fillSelect(document.getElementById("planCategory"), meta.categories, "📦 Other");
  fillSelect(document.getElementById("planIcon"), meta.icons, "📦");
  document.getElementById("planReminder").value = meta.defaultReminderDays;
  document.getElementById("planCurrency").value = settings.currency;
  setDefaultRenewalDate();
  document.getElementById("addPlanHeading").textContent = "Add a plan";
  document.getElementById("planSubmitButton").textContent = "Save plan";
  document.getElementById("addPlanStatus").textContent = "";
}

function startEditPlan(id) {
  const plan = plans.find((item) => item.id === id);
  if (!plan) return;
  const meta = planMeta();
  const setValue = (field, value) => { document.getElementById(field).value = value ?? ""; };
  resetPlanForm();
  editingPlanId = plan.id;
  setValue("planName", plan.name);
  setValue("planAmount", plan.amount);
  setValue("planCurrency", plan.currency);
  setValue("planFrequency", plan.frequency);
  setValue("planRenewal", plan.renewalDate);
  fillSelect(document.getElementById("planCategory"), meta.categories, plan.category || "📦 Other");
  fillSelect(document.getElementById("planIcon"), meta.icons, plan.icon || "📦");
  setValue("planReminder", Number.isFinite(plan.reminderDays) ? plan.reminderDays : meta.defaultReminderDays);
  setValue("planPaymentStatus", plan.paymentStatus === "PAID" ? "PAID" : "PENDING PAYMENT");
  setValue("planPaymentMethod", plan.paymentMethod);
  setValue("planLastPayment", plan.lastPaymentDate);
  setValue("planWebsite", plan.website);
  setValue("planAccountEmail", plan.accountEmail);
  document.getElementById("planAutoRenew").checked = Boolean(plan.autoRenew);
  setValue("planNotes", plan.notes);
  document.getElementById("addPlanHeading").textContent = "Edit plan";
  document.getElementById("planSubmitButton").textContent = "Update plan";
  editRequested = true;
  setActivePage("add-plan");
}

function setDefaultRenewalDate() {
  const date = new Date();
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  const lastDay = new Date(year, month + 1, 0).getDate();
  date.setFullYear(year, month, Math.min(date.getDate(), lastDay));
  document.getElementById("planRenewal").value = dateKey(date);
}

function startDeleteConfirmation() {
  if (pendingDeletion) return;
  const id = document.getElementById("deletePlanSelect").value;
  const plan = plans.find((item) => item.id === id);
  if (!plan) return;
  queuedDeletionId = plan.id;
  document.getElementById("confirmDeleteCopy").textContent = `“${plan.name}” (${formatMoney(plan.amount, plan.currency)} / ${plan.frequency}) will be removed from this device.`;
  document.getElementById("deleteConfirmDialog").showModal();
}

function performDelete() {
  const plan = plans.find((item) => item.id === queuedDeletionId);
  queuedDeletionId = null;
  document.getElementById("deleteConfirmDialog").close();
  if (!plan) return;
  const button = document.getElementById("deleteButton");
  button.disabled = true;
  button.dataset.running = "true";
  document.getElementById("deleteStatus").textContent = "Removing plan…";
  window.setTimeout(() => {
    button.dataset.running = "false";
    plans = plans.filter((item) => item.id !== plan.id);
    if (!savePlans()) {
      plans.push(plan);
      document.getElementById("deleteStatus").textContent = "Could not remove the plan. Try again.";
      renderAll();
      return;
    }
    pendingDeletion = { plan, timer: null };
    window.renewlyBridge?.deletePlan(plan.id); // desktop engine moves it to its recycle bin
    renderAll();
    document.getElementById("deleteStatus").textContent = "Plan deleted.";
    showToast(`${plan.name} deleted.`, true);
    pendingDeletion.timer = window.setTimeout(() => {
      pendingDeletion = null;
      document.getElementById("undoToast").hidden = true;
      renderDeletePage();
    }, 8000);
  }, 2200);
}

function undoDelete() {
  if (!pendingDeletion) return;
  window.clearTimeout(pendingDeletion.timer);
  const restored = pendingDeletion.plan;
  pendingDeletion = null;
  plans.push(restored);
  savePlans();
  window.renewlyBridge?.upsertPlan(restored); // desktop engine restores it from its recycle bin
  document.getElementById("undoToast").hidden = true;
  renderAll();
  document.getElementById("deleteStatus").textContent = `${restored.name} restored.`;
}

function updateClock() {
  const clock = document.getElementById("renewlyClock");
  const blocks = [document.getElementById("clockHours"), document.getElementById("clockMinutes"), document.getElementById("clockSeconds")];
  const now = new Date();
  let hour = now.getHours();
  let period = "";
  if (settings.clockFormat === "12") {
    period = hour >= 12 ? "PM" : "AM";
    hour = hour % 12 || 12;
  }
  const allValues = [String(hour).padStart(2, "0"), String(now.getMinutes()).padStart(2, "0"), String(now.getSeconds()).padStart(2, "0")];
  const values = settings.showSeconds ? allValues : allValues.slice(0, 2);
  clock.classList.toggle("clock--no-seconds", !settings.showSeconds);
  const periodElement = document.getElementById("clockPeriod");
  periodElement.hidden = settings.clockFormat !== "12";
  periodElement.textContent = period;
  clock.setAttribute("aria-label", `Current time ${values.join(":")}${period ? ` ${period}` : ""}`);

  values.forEach((value, index) => {
    const block = blocks[index];
    const group = block.querySelector(".clock__digit-group");
    const oldValue = previousClockValues[index];
    if (oldValue === undefined) {
      group.innerHTML = `<span class="clock__digits">${value}</span><span class="clock__digits">${value}</span>`;
      return;
    }
    if (value === oldValue) return;
    // The group is column-reverse: the first child sits at the bottom, the second on top.
    // The roll animation ends at translateY(0), which shows the TOP child, so the new value must be second.
    group.innerHTML = `<span class="clock__digits">${oldValue}</span><span class="clock__digits">${value}</span>`;
    block.classList.remove("clock__block--bounce");
    block.classList.toggle("clock__block--delay1", index === 1);
    block.classList.toggle("clock__block--delay2", index === 2);
    void block.offsetWidth;
    block.classList.add("clock__block--bounce");
    group.addEventListener("animationend", () => block.classList.remove("clock__block--bounce"), { once: true });
  });
  previousClockValues = allValues;
}

function scheduleClockUpdate() {
  if (clockTimeout !== null) window.clearTimeout(clockTimeout);
  const now = Date.now();
  clockTimeout = window.setTimeout(() => {
    updateClock();
    scheduleClockUpdate();
  }, Math.max(20, 1000 - (now % 1000)));
}

navItems.forEach((item) => {
  const label = item.querySelector(".nav-label")?.textContent.trim();
  if (label) {
    item.title = label;
    item.setAttribute("aria-label", label);
  }
  item.addEventListener("click", () => setActivePage(item.dataset.page));
});
sidebarToggle.addEventListener("click", () => {
  const collapsed = sidebar.classList.toggle("closed");
  const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
  sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
  sidebarToggle.setAttribute("aria-label", label);
  sidebarToggle.title = label;
});
document.querySelectorAll("[data-go-page]").forEach((button) => button.addEventListener("click", () => setActivePage(button.dataset.goPage)));
document.getElementById("glassAction").addEventListener("click", () => setActivePage("add-plan"));
document.getElementById("addPlanForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = (id) => document.getElementById(id).value;
  const status = document.getElementById("addPlanStatus");
  const name = value("planName").trim();
  const amount = Number(value("planAmount"));
  const renewalDate = value("planRenewal");
  if (!name || !Number.isFinite(amount) || amount <= 0 || !renewalDate) return;
  const website = value("planWebsite").trim();
  const reminder = Math.max(0, Math.min(3650, Math.round(Number(value("planReminder")) || 0)));
  const fields = {
    name, amount, currency: value("planCurrency"), frequency: value("planFrequency"), renewalDate,
    notes: value("planNotes").trim(), category: value("planCategory"), icon: value("planIcon"),
    reminderDays: reminder, paymentMethod: value("planPaymentMethod").trim(), website,
    accountEmail: value("planAccountEmail").trim(), autoRenew: document.getElementById("planAutoRenew").checked,
    paymentStatus: value("planPaymentStatus"), lastPaymentDate: value("planLastPayment"),
  };
  const editing = editingPlanId ? plans.find((plan) => plan.id === editingPlanId) : null;
  let saved;
  if (editing) {
    Object.assign(editing, fields);
    saved = editing;
  } else {
    saved = { id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`, ...fields };
    plans.push(saved);
  }
  if (!savePlans()) return;
  window.renewlyBridge?.upsertPlan(saved); // desktop engine (main.py) stores it in renewals.json + cloud sync
  resetPlanForm();
  renderAll();
  showToast(editing ? `${name} updated.` : `${name} added.`);
  setActivePage("dashboard");
});
document.getElementById("planCancelButton").addEventListener("click", resetPlanForm);
document.getElementById("searchInput").addEventListener("input", renderSearch);
document.getElementById("deletePlanSelect").addEventListener("change", renderDeletePage);
document.getElementById("deleteButton").addEventListener("click", startDeleteConfirmation);
document.getElementById("confirmDeleteButton").addEventListener("click", performDelete);
document.getElementById("cancelDeleteButton").addEventListener("click", () => document.getElementById("deleteConfirmDialog").close());
document.getElementById("undoDeleteButton").addEventListener("click", undoDelete);
document.getElementById("themeToggle").addEventListener("change", (event) => {
  settings.theme = event.currentTarget.checked ? "dark" : "light";
  switchTheme();
  saveSettings();
});
document.getElementById("currencySetting").addEventListener("change", (event) => {
  settings.currency = event.currentTarget.value;
  saveSettings();
  renderSettings();
  renderDashboard();
});
document.getElementById("clockFormat").addEventListener("change", (event) => {
  settings.clockFormat = event.currentTarget.value;
  saveSettings();
  updateClock();
});
document.getElementById("showSeconds").addEventListener("change", (event) => {
  settings.showSeconds = event.currentTarget.checked;
  saveSettings();
  updateClock();
});
document.getElementById("deleteConfirmDialog").addEventListener("close", () => { queuedDeletionId = null; });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.getElementById("deleteConfirmDialog").open) setActivePage("dashboard");
});

renderCurrencyOptions();
renderAll();
resetPlanForm();
setActivePage("dashboard");
initWebGpuGlass();
scheduleClockUpdate();
window.addEventListener("focus", updateClock);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateClock();
});

// ---- Desktop engine (only active when served by main.py via renewly_bridge.py) ----
function renderAccountInfo() {
  const info = window.renewlyBridge?.info;
  if (!info) return;
  const name = document.getElementById("accountName");
  if (!name) return;
  name.textContent = info.user?.name || "Renewly account";
  document.getElementById("accountEmail").textContent = info.user?.email || "";
  document.getElementById("accountCloud").textContent =
    `Renewly Cloud: ${info.cloud ? "connected" : "not connected"}${info.version ? ` \u00b7 Renewly v${info.version}` : ""}`;
}

function initBridgeUi() {
  const bridge = window.renewlyBridge;
  // Desktop-only content shows with the bridge; the standalone (Electron) text shows without it.
  document.querySelectorAll("[data-bridge-only]").forEach((el) => { el.hidden = !bridge; });
  document.querySelectorAll("[data-standalone-only]").forEach((el) => { el.hidden = Boolean(bridge); });
  if (!bridge) return;

  // These pages are backed by the real Renewly account here, not a "local profile".
  Object.assign(pageSubtitles, {
    account: "Your Renewly account.",
    "reset-password": "Change your Renewly password.",
    logout: "Sign out of Renewly.",
    "recycle-bin": "Restore or remove deleted plans.",
  });

  window.addEventListener("renewly-bridge-error", (event) => showToast(event.detail));
  // Plans, settings or account changed outside this window (cloud sync, desktop Settings window, ...).
  bridge.onExternalChange(() => {
    plans = loadPlans();
    settings = loadSettings();
    renderAll();
    renderAccountInfo();
  });

  // Buttons that open the original desktop windows (full Settings, Profile, Password, Logout, ...).
  document.querySelectorAll("[data-desktop]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await bridge.openDesktop(button.dataset.desktop);
      } catch (error) {
        showToast(error instanceof Error ? error.message : String(error));
      }
    });
  });

  const testButton = document.getElementById("pushTestButton");
  const statusLine = document.getElementById("pushStatus");
  testButton.addEventListener("click", async () => {
    testButton.disabled = true;
    statusLine.textContent = "Sending test\u2026";
    try {
      const result = await bridge.testPush();
      const sent = Number(result?.sent || 0);
      statusLine.textContent = result?.ok && sent > 0
        ? `Test push sent to ${sent} browser${sent === 1 ? "" : "s"}.`
        : "No active browser subscription found. Open the web notification portal and subscribe first.";
    } catch (error) {
      statusLine.textContent = error instanceof Error ? error.message : String(error);
    }
    testButton.disabled = false;
  });

  renderAccountInfo();
}
initBridgeUi();
