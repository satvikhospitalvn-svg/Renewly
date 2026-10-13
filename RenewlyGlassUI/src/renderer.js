"use strict";

import "./index.css";
import { initWebGpuGlass } from "./webgpu-glass.js";

const PLAN_KEY = "renewly.plans.v1";
const RECYCLE_KEY = "renewly.recycle-bin.v1";
const SETTINGS_KEY = "renewly.settings.v1";
const PROFILE_KEY = "renewly.profile.v1";
const currencies = ["INR", "USD", "EUR", "GBP", "JPY", "CAD", "AUD"];
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
const defaultSettings = { currency: "INR", clockFormat: "24", showSeconds: false, theme: "light", dateFormat: "DD-MM-YYYY", defaultReminderDays: 3, notificationsEnabled: true, notificationSound: true, defaultPage: "dashboard", dashboardDensity: "comfortable", showSummary: true, showActivePlans: true, showMonthlySpend: true, showNextRenewal: true };
let plans = loadPlans();
let recycleBin = loadRecycleBin();
let settings = loadSettings();
let profile = loadProfile();
let previousClockValues = [];
let clockTimeout = null;
let pendingDeletion = null;
let queuedDeletionId = null;
let editingPlanId = null;
let toastTimer = null;

function loadPlans() {
  try {
    const value = JSON.parse(localStorage.getItem(PLAN_KEY) || "[]");
    return Array.isArray(value) ? value.filter((plan) => plan && plan.id && plan.name).map(normalizePlan) : [];
  } catch {
    return [];
  }
}

function loadRecycleBin() {
  try { const saved = JSON.parse(localStorage.getItem(RECYCLE_KEY) || "[]"); return Array.isArray(saved) ? saved.filter((plan) => plan && plan.id && plan.name).map(normalizePlan) : []; }
  catch { return []; }
}

function saveRecycleBin() {
  try { localStorage.setItem(RECYCLE_KEY, JSON.stringify(recycleBin)); return true; }
  catch { showToast("Could not save the recycle bin on this device."); return false; }
}

function loadProfile() {
  try {
    const stored = JSON.parse(localStorage.getItem(PROFILE_KEY) || "{}");
    return { username: String(stored.username || "Local user"), role: "Local profile", profile_picture: String(stored.profile_picture || ""), profile_updated: String(stored.profile_updated || "") };
  } catch { return { username: "Local user", role: "Local profile", profile_picture: "", profile_updated: "" }; }
}

function saveProfile() {
  try { localStorage.setItem(PROFILE_KEY, JSON.stringify(profile)); return true; }
  catch { showToast("Could not save the profile image or details. Try a smaller image."); return false; }
}

function toIsoDate(value) {
  const text = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const match = text.match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/);
  if (!match) return "";
  return `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`;
}

function toMainDate(value) {
  const iso = toIsoDate(value);
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  return `${day}-${month}-${year}`;
}

function planCycle(plan) {
  const raw = String(plan.billing_cycle || plan.frequency || "Monthly");
  const known = { monthly: "Monthly", quarterly: "Quarterly", "half-yearly": "Half-Yearly", yearly: "Yearly", weekly: "Weekly", custom: "Custom" };
  return known[raw.toLowerCase()] || raw;
}

function normalizePlan(plan) {
  const renewalDate = toIsoDate(plan.renewalDate || plan.renewal_date);
  const lastPaymentDate = toIsoDate(plan.last_payment_date || plan.lastPaymentDate);
  return {
    ...plan,
    id: String(plan.id),
    currency: plan.currency || "INR",
    renewalDate,
    renewal_date: toMainDate(renewalDate),
    billing_cycle: planCycle(plan),
    reminder_days: String(plan.reminder_days ?? plan.reminderDays ?? 3),
    category: plan.category || "📦 Other",
    icon_emoji: plan.icon_emoji || plan.iconEmoji || "📦",
    icon_path: plan.icon_path || plan.iconPath || "",
    payment_method: plan.payment_method || plan.paymentMethod || "",
    payment_status: String(plan.payment_status || plan.paymentStatus || (String(plan.status || "").toLowerCase() === "paid" ? "PAID" : "PENDING PAYMENT")).toUpperCase(),
    website: plan.website || "",
    account_email: plan.account_email || plan.accountEmail || "",
    auto_renew: Boolean(plan.auto_renew ?? plan.autoRenew),
    last_payment_date: toMainDate(lastPaymentDate),
  };
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
      dateFormat: ["DD-MM-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"].includes(stored?.dateFormat) ? stored.dateFormat : defaultSettings.dateFormat,
      defaultReminderDays: Math.min(3650, Math.max(0, Number.isFinite(Number(stored?.defaultReminderDays)) ? Number(stored.defaultReminderDays) : defaultSettings.defaultReminderDays)),
      notificationsEnabled: stored?.notificationsEnabled !== false,
      notificationSound: stored?.notificationSound !== false,
      defaultPage: stored?.defaultPage === "analytics" ? "analytics" : "dashboard",
      dashboardDensity: stored?.dashboardDensity === "compact" ? "compact" : "comfortable",
      showSummary: stored?.showSummary !== false,
      showActivePlans: stored?.showActivePlans !== false,
      showMonthlySpend: stored?.showMonthlySpend !== false,
      showNextRenewal: stored?.showNextRenewal !== false,
    };
  } catch {
    return { ...defaultSettings };
  }
}

function savePlans() {
  try {
    localStorage.setItem(PLAN_KEY, JSON.stringify(plans.map((plan) => ({ ...plan, renewal_date: toMainDate(plan.renewalDate) }))));
    return true;
  } catch {
    showToast("Could not save plans in this app profile.");
    return false;
  }
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
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
  const cycle = planCycle(plan).toLowerCase();
  if (cycle === "weekly") return amount * 52 / 12;
  if (cycle === "quarterly") return amount / 3;
  if (cycle === "half-yearly") return amount / 6;
  if (cycle === "yearly") return amount / 12;
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
  if (!date) return "Date not set";
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const year = String(date.getFullYear());
  if (settings.dateFormat === "DD/MM/YYYY") return `${day}/${month}/${year}`;
  if (settings.dateFormat === "MM/DD/YYYY") return `${month}/${day}/${year}`;
  if (settings.dateFormat === "YYYY-MM-DD") return `${year}-${month}-${day}`;
  if (settings.dateFormat === "DD-MM-YYYY") return `${day}-${month}-${year}`;
  return date.toLocaleDateString(undefined, options);
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
  document.getElementById("dashboardPage").classList.toggle("is-compact", settings.dashboardDensity === "compact");
  const summaryGrid = document.querySelector("#dashboardPage > .summary-grid");
  summaryGrid.hidden = !settings.showSummary;
  summaryGrid.classList.toggle("summary-grid--compact", settings.dashboardDensity === "compact");
  const summaryCards = summaryGrid.querySelectorAll(".summary-card");
  summaryCards[0].hidden = !settings.showActivePlans;
  summaryCards[1].hidden = !settings.showMonthlySpend;
  summaryCards[2].hidden = !settings.showNextRenewal;
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
    nameCell.append(makeElement("span", "plan-title-cell", `${plan.icon_emoji || "📦"} ${plan.name}`));
    nameCell.append(makeElement("span", "plan-meta-cell", `${plan.category || "📦 Other"} · ${plan.payment_status === "PAID" ? "Paid" : "Pending payment"}${plan.auto_renew ? " · Auto-renew" : ""}`));
    if (plan.notes) nameCell.append(makeElement("span", "plan-note-cell", plan.notes));
    const priceCell = makeElement("td", "", `${formatMoney(plan.amount, plan.currency)} / ${planCycle(plan)}`);
    const renewalCell = makeElement("td", "", formatDate(plan.renewalDate));
    const actionsCell = makeElement("td");
    const actionGroup = makeElement("div", "plan-actions");
    const edit = makeElement("button", "secondary-button", "Edit");
    edit.type = "button"; edit.dataset.planAction = "edit"; edit.dataset.planId = plan.id;
    const duplicate = makeElement("button", "secondary-button", "Duplicate");
    duplicate.type = "button"; duplicate.dataset.planAction = "duplicate"; duplicate.dataset.planId = plan.id;
    const payment = makeElement("button", "secondary-button", plan.payment_status === "PAID" ? "Mark pending" : "Mark paid");
    payment.type = "button"; payment.dataset.planAction = "payment"; payment.dataset.planId = plan.id;
    actionGroup.append(edit, duplicate, payment);
    actionsCell.append(actionGroup);
    row.append(nameCell, priceCell, renewalCell, actionsCell);
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

function renderAccount() {
  document.getElementById("accountDisplayName").textContent = profile.username;
  document.getElementById("accountNameInput").value = profile.username;
  document.getElementById("accountRole").textContent = profile.role;
  document.getElementById("accountUpdatedLabel").textContent = profile.profile_updated ? `Last updated: ${profile.profile_updated}` : "Profile stored on this device";
  const avatar = document.getElementById("accountAvatarImage");
  const initial = document.getElementById("accountAvatarInitial");
  avatar.hidden = !profile.profile_picture;
  if (profile.profile_picture) avatar.src = profile.profile_picture;
  initial.hidden = Boolean(profile.profile_picture);
  initial.textContent = (profile.username.trim()[0] || "L").toUpperCase();

  const active = plans.filter((plan) => {
    const remaining = daysUntil(plan.renewalDate);
    return remaining !== null && remaining >= 0 && plan.payment_status !== "PAID";
  });
  const dueSoon = active.filter((plan) => daysUntil(plan.renewalDate) <= 7).length;
  const paid = plans.filter((plan) => plan.payment_status === "PAID").length;
  document.getElementById("accountStatTotal").textContent = String(plans.length);
  document.getElementById("accountStatActive").textContent = String(active.length);
  document.getElementById("accountStatDue").textContent = String(dueSoon);
  document.getElementById("accountStatPaid").textContent = String(paid);
  const valueNode = document.getElementById("accountStatValue");
  const totals = new Map();
  plans.forEach((plan) => totals.set(plan.currency, (totals.get(plan.currency) || 0) + (Number(plan.amount) || 0)));
  valueNode.replaceChildren();
  if (totals.size) [...totals].forEach(([currency, amount]) => valueNode.append(makeElement("span", "account-stat-money", formatMoney(amount, currency))));
  else valueNode.textContent = formatMoney(0, settings.currency);
  document.getElementById("accountStatItems").textContent = String(plans.length + recycleBin.length);
}

function renderRecycleBin() {
  const list = document.getElementById("recycleList");
  list.replaceChildren();
  document.getElementById("recycleCount").textContent = `${recycleBin.length} ${recycleBin.length === 1 ? "item" : "items"}`;
  document.getElementById("recycleEmpty").hidden = recycleBin.length > 0;
  recycleBin.forEach((plan) => {
    const row = makeElement("article", "feature-row recycle-row");
    const details = document.createElement("div");
    details.append(makeElement("strong", "feature-row-title", `${plan.icon_emoji || "📦"} ${plan.name}`));
    details.append(makeElement("span", "feature-row-detail", `${formatMoney(plan.amount, plan.currency)} / ${planCycle(plan)} · deleted plan`));
    const actions = makeElement("div", "account-actions");
    const restore = makeElement("button", "secondary-button", "Restore");
    restore.type = "button";
    restore.addEventListener("click", () => {
      recycleBin = recycleBin.filter((item) => item.id !== plan.id);
      plans.push(plan); savePlans(); saveRecycleBin(); renderAll(); showToast(`${plan.name} restored.`);
    });
    const remove = makeElement("button", "secondary-button", "Delete permanently");
    remove.type = "button";
    remove.addEventListener("click", () => {
      if (!window.confirm(`Permanently delete “${plan.name}”? This cannot be undone.`)) return;
      recycleBin = recycleBin.filter((item) => item.id !== plan.id);
      saveRecycleBin(); renderAll(); showToast(`${plan.name} permanently deleted.`);
    });
    actions.append(restore, remove); row.append(details, actions); list.append(row);
  });
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
    row.append(date, info, makeElement("strong", "feature-row-amount", `${formatMoney(plan.amount, plan.currency)} / ${planCycle(plan)}`));
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
    info.append(makeElement("span", "search-result-detail", `${formatMoney(plan.amount, plan.currency)} / ${planCycle(plan)} · Renews ${formatDate(plan.renewalDate)}`));
    const actions = makeElement("div", "plan-actions");
    const edit = makeElement("button", "secondary-button", "Edit");
    edit.type = "button"; edit.dataset.planAction = "edit"; edit.dataset.planId = plan.id;
    const remove = makeElement("button", "secondary-button", "Delete");
    remove.type = "button"; remove.dataset.planAction = "delete"; remove.dataset.planId = plan.id;
    actions.append(edit, remove);
    item.append(info, actions);
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
  document.getElementById("dateFormat").value = settings.dateFormat;
  document.getElementById("defaultReminderDays").value = settings.defaultReminderDays;
  document.getElementById("notificationsEnabled").checked = settings.notificationsEnabled;
  document.getElementById("notificationSound").checked = settings.notificationSound;
  document.getElementById("defaultPage").value = settings.defaultPage;
  document.getElementById("dashboardDensity").value = settings.dashboardDensity;
  document.getElementById("showSummary").checked = settings.showSummary;
  document.getElementById("showActivePlans").checked = settings.showActivePlans;
  document.getElementById("showMonthlySpend").checked = settings.showMonthlySpend;
  document.getElementById("showNextRenewal").checked = settings.showNextRenewal;
  updateClock();
}

function renderAll() {
  renderDashboard();
  renderFeaturePages();
  renderRecycleBin();
  renderAccount();
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

function setDefaultRenewalDate() {
  const date = new Date();
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  const lastDay = new Date(year, month + 1, 0).getDate();
  date.setFullYear(year, month, Math.min(date.getDate(), lastDay));
  document.getElementById("planRenewal").value = dateKey(date);
}

function resetPlanForm() {
  editingPlanId = null;
  document.getElementById("addPlanForm").reset();
  document.getElementById("addPlanHeading").textContent = "Add a plan";
  document.getElementById("savePlanButton").textContent = "Save plan";
  if (!pageElements["add-plan"].hidden) document.getElementById("pageTitle").textContent = "Add Plan";
  document.getElementById("planCurrency").value = settings.currency;
  document.getElementById("planReminderDays").value = String(settings.defaultReminderDays);
  document.getElementById("planCategory").value = "📦 Other";
  document.getElementById("planPaymentStatus").value = "PENDING PAYMENT";
  document.getElementById("planIconEmoji").value = "📦";
  setDefaultRenewalDate();
}

function editPlan(planId) {
  const plan = plans.find((item) => item.id === String(planId));
  if (!plan) return;
  editingPlanId = plan.id;
  document.getElementById("planName").value = plan.name || "";
  document.getElementById("planAmount").value = Number(plan.amount) || "";
  document.getElementById("planFrequency").value = planCycle(plan);
  document.getElementById("planCurrency").value = currencies.includes(plan.currency) ? plan.currency : settings.currency;
  document.getElementById("planRenewal").value = plan.renewalDate || "";
  document.getElementById("planCategory").value = plan.category || "📦 Other";
  document.getElementById("planReminderDays").value = plan.reminder_days || "3";
  document.getElementById("planPaymentMethod").value = plan.payment_method || "";
  document.getElementById("planPaymentStatus").value = plan.payment_status || "PENDING PAYMENT";
  document.getElementById("planWebsite").value = plan.website || "";
  document.getElementById("planAccountEmail").value = plan.account_email || "";
  document.getElementById("planIconEmoji").value = plan.icon_emoji || "📦";
  document.getElementById("planLastPaymentDate").value = toIsoDate(plan.last_payment_date);
  document.getElementById("planAutoRenew").checked = Boolean(plan.auto_renew);
  document.getElementById("planNotes").value = plan.notes || "";
  document.getElementById("addPlanHeading").textContent = "Edit plan";
  document.getElementById("savePlanButton").textContent = "Save changes";
  setActivePage("add-plan");
  document.getElementById("pageTitle").textContent = "Edit Plan";
}

function duplicatePlan(planId) {
  const source = plans.find((item) => item.id === String(planId));
  if (!source) return;
  const copy = { ...source, id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`, name: `${source.name} (copy)`, payment_status: "PENDING PAYMENT", last_payment_date: "" };
  delete copy.cloud_plan_id;
  delete copy.cloud_sync_fingerprint;
  delete copy.cloud_sync_remote_updated_at;
  plans.push(copy);
  if (!savePlans()) { plans.pop(); return; }
  renderAll();
  showToast(`${source.name} duplicated.`);
}

function togglePlanPaid(planId) {
  const plan = plans.find((item) => item.id === String(planId));
  if (!plan) return;
  const wasPaid = plan.payment_status === "PAID";
  const previousLastPayment = plan.last_payment_date;
  plan.payment_status = wasPaid ? "PENDING PAYMENT" : "PAID";
  plan.last_payment_date = wasPaid ? "" : toMainDate(dateKey(new Date()));
  if (!savePlans()) {
    plan.payment_status = wasPaid ? "PAID" : "PENDING PAYMENT";
    plan.last_payment_date = previousLastPayment;
    return;
  }
  renderAll();
  showToast(`${plan.name} marked ${wasPaid ? "pending" : "paid"}.`);
}

function handlePlanAction(event) {
  const button = event.target.closest("[data-plan-action]");
  if (!button) return;
  const { planAction, planId } = button.dataset;
  if (planAction === "edit") editPlan(planId);
  if (planAction === "duplicate") duplicatePlan(planId);
  if (planAction === "payment") togglePlanPaid(planId);
  if (planAction === "delete") {
    setActivePage("delete");
    document.getElementById("deletePlanSelect").value = planId;
    renderDeletePage();
  }
}

function startDeleteConfirmation() {
  if (pendingDeletion) return;
  const id = document.getElementById("deletePlanSelect").value;
  const plan = plans.find((item) => item.id === id);
  if (!plan) return;
  queuedDeletionId = plan.id;
  document.getElementById("confirmDeleteCopy").textContent = `“${plan.name}” (${formatMoney(plan.amount, plan.currency)} / ${planCycle(plan)}) will be removed from this device.`;
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
    recycleBin.push(plan);
    if (!saveRecycleBin()) {
      recycleBin = recycleBin.filter((item) => item.id !== plan.id);
      plans.push(plan); savePlans(); renderAll();
      document.getElementById("deleteStatus").textContent = "Could not save the recycle bin. The plan was kept.";
      return;
    }
    pendingDeletion = { plan, timer: null };
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
  recycleBin = recycleBin.filter((item) => item.id !== restored.id);
  plans.push(restored);
  savePlans(); saveRecycleBin();
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
document.getElementById("planTableBody").addEventListener("click", handlePlanAction);
document.getElementById("searchResults").addEventListener("click", handlePlanAction);
document.getElementById("cancelPlanEdit").addEventListener("click", () => { resetPlanForm(); setActivePage("dashboard"); });
document.getElementById("addPlanForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = document.getElementById("planName").value.trim();
  const amount = Number(document.getElementById("planAmount").value);
  const renewalDate = document.getElementById("planRenewal").value;
  if (!name || !Number.isFinite(amount) || amount <= 0 || !renewalDate) return;
  const wasEditing = Boolean(editingPlanId);
  const planData = {
    name,
    amount,
    currency: document.getElementById("planCurrency").value,
    billing_cycle: document.getElementById("planFrequency").value,
    renewalDate,
    renewal_date: toMainDate(renewalDate),
    reminder_days: document.getElementById("planReminderDays").value || "3",
    category: document.getElementById("planCategory").value,
    notes: document.getElementById("planNotes").value.trim(),
    icon_emoji: document.getElementById("planIconEmoji").value,
    icon_path: "",
    payment_method: document.getElementById("planPaymentMethod").value,
    payment_status: document.getElementById("planPaymentStatus").value,
    website: document.getElementById("planWebsite").value.trim(),
    account_email: document.getElementById("planAccountEmail").value.trim(),
    auto_renew: document.getElementById("planAutoRenew").checked,
    last_payment_date: toMainDate(document.getElementById("planLastPaymentDate").value),
  };
  let savedName = name;
  if (editingPlanId) {
    const index = plans.findIndex((plan) => plan.id === editingPlanId);
    if (index < 0) { resetPlanForm(); return; }
    const previous = plans[index];
    plans[index] = { ...previous, ...planData };
    if (!savePlans()) { plans[index] = previous; return; }
    document.getElementById("addPlanStatus").textContent = "Plan updated on this device.";
  } else {
    savedName = name;
    plans.push({ id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`, ...planData });
    if (!savePlans()) { plans.pop(); return; }
    document.getElementById("addPlanStatus").textContent = "Plan saved on this device.";
  }
  resetPlanForm();
  renderAll();
  showToast(wasEditing ? `${savedName} updated.` : `${savedName} added.`);
  setActivePage("dashboard");
});
document.getElementById("searchInput").addEventListener("input", renderSearch);
document.getElementById("deletePlanSelect").addEventListener("change", renderDeletePage);
document.getElementById("deleteButton").addEventListener("click", startDeleteConfirmation);
document.getElementById("confirmDeleteButton").addEventListener("click", performDelete);
document.getElementById("cancelDeleteButton").addEventListener("click", () => document.getElementById("deleteConfirmDialog").close());
document.getElementById("undoDeleteButton").addEventListener("click", undoDelete);
document.getElementById("themeToggle").addEventListener("change", (event) => {
  settings.theme = event.currentTarget.checked ? "dark" : "light";
  // Switch themes in a single paint: with transitions disabled for that moment, the browser
  // doesn't have to animate box-shadows/backdrop blurs on dozens of glass surfaces at once.
  const root = document.documentElement;
  root.classList.add("theme-switching");
  applyTheme();
  void document.body.offsetWidth; // flush styles while transitions are off
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
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
function bindSetting(id, key, refresh = () => renderDashboard()) {
  const control = document.getElementById(id);
  control.addEventListener("change", () => {
    settings[key] = control.type === "checkbox" ? control.checked : (control.type === "number" ? Number(control.value) : control.value);
    saveSettings();
    refresh();
  });
}
bindSetting("dateFormat", "dateFormat", renderAll);
bindSetting("defaultReminderDays", "defaultReminderDays", () => {});
bindSetting("notificationsEnabled", "notificationsEnabled", () => {});
bindSetting("notificationSound", "notificationSound", () => {});
bindSetting("defaultPage", "defaultPage", () => {});
bindSetting("dashboardDensity", "dashboardDensity");
bindSetting("showSummary", "showSummary");
bindSetting("showActivePlans", "showActivePlans");
bindSetting("showMonthlySpend", "showMonthlySpend");
bindSetting("showNextRenewal", "showNextRenewal");

function downloadFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
document.getElementById("exportBackup").addEventListener("click", () => {
  downloadFile(`renewly-backup-${dateKey(new Date())}.json`, JSON.stringify({ app: "Renewly", version: 1, exportedAt: new Date().toISOString(), plans, recycle_bin: recycleBin, settings, user: profile }, null, 2), "application/json");
  showToast("Backup exported.");
});
document.getElementById("exportPlansCsv").addEventListener("click", () => {
  const columns = ["name", "amount", "currency", "billing_cycle", "renewal_date", "category", "reminder_days", "payment_method", "payment_status", "website", "account_email", "auto_renew", "last_payment_date", "notes"];
  const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const csv = [columns.join(","), ...plans.map((plan) => columns.map((key) => quote(plan[key])).join(","))].join("\r\n");
  downloadFile(`renewly-plans-${dateKey(new Date())}.csv`, csv, "text/csv;charset=utf-8");
  showToast("Plans exported as CSV.");
});
const backupFile = document.getElementById("backupFile");
document.getElementById("importBackup").addEventListener("click", () => backupFile.click());
backupFile.addEventListener("change", async () => {
  const file = backupFile.files?.[0];
  if (!file) return;
  try {
    const parsed = JSON.parse(await file.text());
    const incomingPlans = Array.isArray(parsed) ? parsed : parsed.plans;
    if (!Array.isArray(incomingPlans) || !incomingPlans.every((plan) => plan && plan.name && plan.id)) throw new Error("Invalid backup");
    if (!window.confirm(`Restore ${incomingPlans.length} plans? This replaces the plans currently stored on this device.`)) return;
    plans = incomingPlans.map(normalizePlan);
    recycleBin = Array.isArray(parsed.recycle_bin) ? parsed.recycle_bin.filter((plan) => plan && plan.id && plan.name).map(normalizePlan) : [];
    savePlans();
    saveRecycleBin();
    if (parsed.settings && typeof parsed.settings === "object") {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...defaultSettings, ...parsed.settings }));
      settings = loadSettings();
    }
    if (parsed.user && typeof parsed.user === "object") {
      profile = { ...profile, username: String(parsed.user.username || profile.username), profile_picture: String(parsed.user.profile_picture || ""), profile_updated: String(parsed.user.profile_updated || ""), role: "Local profile" };
      saveProfile();
    }
    saveSettings(); renderCurrencyOptions(); renderAll();
    showToast("Backup restored.");
  } catch {
    showToast("Could not restore that backup. Choose a Renewly JSON backup.");
  } finally { backupFile.value = ""; }
});
document.getElementById("accountExportBackup").addEventListener("click", () => document.getElementById("exportBackup").click());
document.getElementById("accountImportBackup").addEventListener("click", () => document.getElementById("importBackup").click());
document.getElementById("saveAccountProfile").addEventListener("click", () => {
  const name = document.getElementById("accountNameInput").value.trim();
  const status = document.getElementById("accountStatus");
  if (!name) { status.textContent = "Enter a display name first."; return; }
  profile.username = name;
  profile.profile_updated = new Date().toLocaleString();
  if (saveProfile()) { renderAccount(); status.textContent = "Profile saved on this device."; }
});
const accountAvatarFile = document.getElementById("accountAvatarFile");
document.getElementById("chooseAccountAvatar").addEventListener("click", () => accountAvatarFile.click());
document.getElementById("accountAvatarButton").addEventListener("click", () => accountAvatarFile.click());
accountAvatarFile.addEventListener("change", () => {
  const file = accountAvatarFile.files?.[0];
  if (!file) return;
  if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 8 * 1024 * 1024) {
    document.getElementById("accountStatus").textContent = "Choose a PNG, JPEG, or WebP image smaller than 8 MB.";
    accountAvatarFile.value = "";
    return;
  }
  const url = URL.createObjectURL(file);
  const image = new Image();
  image.onload = () => {
    const canvas = document.createElement("canvas");
    const scale = Math.min(1, 256 / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
    profile.profile_picture = canvas.toDataURL("image/jpeg", .82);
    profile.profile_updated = new Date().toLocaleString();
    const saved = saveProfile();
    renderAccount();
    document.getElementById("accountStatus").textContent = saved ? "Profile picture saved on this device." : "Could not save that picture.";
    URL.revokeObjectURL(url); accountAvatarFile.value = "";
  };
  image.onerror = () => { URL.revokeObjectURL(url); accountAvatarFile.value = ""; document.getElementById("accountStatus").textContent = "That image could not be opened."; };
  image.src = url;
});
document.getElementById("deleteConfirmDialog").addEventListener("close", () => { queuedDeletionId = null; });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.getElementById("deleteConfirmDialog").open) setActivePage("dashboard");
});

renderCurrencyOptions();
renderAll();
setDefaultRenewalDate();
setActivePage(settings.defaultPage);
initWebGpuGlass();
scheduleClockUpdate();
window.addEventListener("focus", updateClock);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateClock();
});
