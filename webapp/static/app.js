/* ============================================================
   LOOKSCORE — логика мини-аппа
   ============================================================ */

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);

const state = {
  initData: "",
  period: "day",
  minAge: 16,
  guides: [],
};

/* ── Служебное ───────────────────────────────────────────── */

function haptic(style = "light") {
  try {
    tg?.HapticFeedback?.impactOccurred(style);
  } catch (_) {}
}

function notifySuccess() {
  try {
    tg?.HapticFeedback?.notificationOccurred("success");
  } catch (_) {}
}

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    headers: { "X-Init-Data": state.initData },
    body,
  });

  if (!response.ok) {
    let detail = "Что-то пошло не так";
    try {
      detail = (await response.json()).detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

/* ── Навигация ───────────────────────────────────────────── */

function showScreen(id) {
  document
    .querySelectorAll(".screen")
    .forEach((el) => el.classList.toggle("active", el.id === id));
  document.querySelectorAll(".screen").forEach((el) => (el.scrollTop = 0));
}

function movePill(pill, target, container) {
  const box = container.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  pill.style.width = `${rect.width}px`;
  pill.style.transform = `translateX(${rect.left - box.left}px)`;
}

function initTabs() {
  const tabs = $("tabs");
  const pill = $("tab-pill");
  const buttons = [...tabs.querySelectorAll(".tab")];

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      haptic();
      buttons.forEach((b) => b.classList.toggle("active", b === button));
      movePill(pill, button, tabs);
      showScreen(button.dataset.screen);
      if (button.dataset.screen === "s-profile") {
        loadProfile();
        // Размеры считаются только на видимом экране, иначе подложка
        // сегментов схлопывается в ноль.
        requestAnimationFrame(syncSegmentPill);
      }
    });
  });

  tabs.classList.add("visible");
  requestAnimationFrame(() =>
    movePill(pill, buttons.find((b) => b.classList.contains("active")), tabs)
  );
}

/* ── Онбординг ───────────────────────────────────────────── */

function buildAgeGrid() {
  const grid = $("age-grid");
  let chosen = null;

  for (let age = 13; age <= 28; age += 1) {
    const cell = document.createElement("button");
    cell.className = "age-cell";
    cell.textContent = age === 28 ? "28+" : age;
    cell.dataset.age = age;

    cell.addEventListener("click", () => {
      haptic();
      grid.querySelectorAll(".age-cell").forEach((c) => c.classList.remove("on"));
      cell.classList.add("on");
      chosen = age;
      $("age-save").disabled = false;
    });

    grid.appendChild(cell);
  }

  $("age-save").addEventListener("click", async () => {
    if (!chosen) return;
    haptic("medium");
    const button = $("age-save");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>';

    try {
      const form = new FormData();
      form.append("age", String(chosen));
      const result = await api("/api/age", { method: "POST", body: form });
      result.age_ok ? enterApp() : showBlocked();
    } catch (error) {
      toast(error.message);
      button.disabled = false;
      button.textContent = "Сохранить";
    }
  });
}

function showBlocked() {
  $("ob-step-1").classList.add("hidden");
  $("ob-step-2").classList.add("hidden");
  $("ob-blocked").classList.remove("hidden");
  $("min-age-txt").textContent = state.minAge;
}

function enterApp() {
  showScreen("s-scan");
  initTabs();
  loadGuides();
}

/* ── Скан ────────────────────────────────────────────────── */

const SCAN_STAGES = [
  ["Инициализация модуля", 8],
  ["Поиск лицевых маркеров", 27],
  ["Замер угловых характеристик", 51],
  ["Оценка текстур и пропорций", 74],
  ["Сборка отчёта", 93],
];

function setScanState(name) {
  $("scan-idle").classList.toggle("hidden", name !== "idle");
  $("scan-run").classList.toggle("hidden", name !== "run");
  $("scan-result").classList.toggle("hidden", name !== "result");
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Уменьшает снимок до отправки.
 *
 * Причина жёсткая: у serverless-функций Vercel лимит тела запроса 4.5 МБ, а
 * современный телефон отдаёт 8-12 МБ. Без этого шага загрузка падала бы с
 * ошибкой платформы ещё до нашего кода.
 *
 * Побочный выигрыш: вместо нескольких мегабайт уходит 200-400 КБ, то есть
 * отчёт приходит быстрее, особенно на мобильном интернете.
 */
async function prepareImage(file, maxSide = 1280, quality = 0.85) {
  if (!/^image\//.test(file.type)) return file;

  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));

    // Мелкий и уже лёгкий файл трогать незачем.
    if (scale === 1 && file.size < 1_800_000) {
      bitmap.close?.();
      return file;
    }

    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);

    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", quality)
    );
    if (!blob) return file;

    return new File([blob], "photo.jpg", { type: "image/jpeg" });
  } catch (_) {
    // Старый браузер без createImageBitmap — отправляем как есть.
    return file;
  }
}

async function runScan(rawFile) {
  const file = await prepareImage(rawFile);

  if (file.size > 4_000_000) {
    toast("Фото слишком тяжёлое. Попробуй другой снимок.");
    return;
  }

  const url = URL.createObjectURL(file);
  $("preview-img").src = url;
  setScanState("run");
  requestAnimationFrame(() => $("calipers").classList.add("on"));

  const form = new FormData();
  form.append("photo", file, file.name || "photo.jpg");

  // Запрос уходит сразу, анимация идёт параллельно — так ожидание
  // ощущается работой, а не задержкой.
  const request = api("/api/rate", { method: "POST", body: form });

  for (const [label, percent] of SCAN_STAGES) {
    $("scan-label").textContent = label;
    $("scan-pct").textContent = `${percent}%`;
    await wait(620);
  }

  try {
    const report = await request;
    $("scan-label").textContent = "Готово";
    $("scan-pct").textContent = "100%";
    await wait(380);
    renderReport(report);
    notifySuccess();
  } catch (error) {
    toast(error.message);
    setScanState("idle");
  } finally {
    URL.revokeObjectURL(url);
    $("calipers").classList.remove("on");
  }
}

function countUp(element, target, duration = 1300) {
  const start = performance.now();
  function frame(now) {
    const progress = Math.min(1, (now - start) / duration);
    // easeOutExpo — быстрый старт, мягкая посадка на финальное число
    const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
    element.textContent = (target * eased).toFixed(1);
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function renderReport(report) {
  setScanState("result");

  const circumference = 2 * Math.PI * 94;
  const ring = $("ring-value");
  ring.style.strokeDasharray = `${circumference}`;
  ring.style.strokeDashoffset = `${circumference}`;

  countUp($("res-score"), report.overall);
  requestAnimationFrame(() => {
    ring.style.strokeDashoffset = `${circumference * (1 - report.overall / 10)}`;
  });

  $("res-tier").innerHTML =
    `<span>${report.tier.emoji}</span><span>${report.tier.code}</span>` +
    `<span class="muted" style="font-weight:520">${report.tier.title}</span>`;
  $("res-percentile").textContent =
    `Выше, чем у ${report.percentile}% пользователей · ${report.tier.comment}`;

  const params = $("res-params");
  params.innerHTML = "";
  report.scores.forEach((score, index) => {
    const row = document.createElement("div");
    row.className = "param";
    row.style.animationDelay = `${0.25 + index * 0.055}s`;
    row.innerHTML =
      `<span class="param-emoji">${score.emoji}</span>` +
      `<span class="param-title">${score.title}</span>` +
      `<span class="param-value">${score.value.toFixed(1)}</span>`;
    params.appendChild(row);
  });

  $("res-tips").innerHTML = report.tips
    .map(
      (tip) =>
        `<div><h3>${tip.emoji} ${tip.title}</h3>` +
        `<p class="tiny" style="margin-top:4px">${tip.text}</p></div>`
    )
    .join("");
}

function initScan() {
  const input = $("file");
  const drop = $("drop");

  drop.addEventListener("click", () => {
    haptic();
    input.click();
  });
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") input.click();
  });

  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) runScan(file);
    input.value = "";
  });

  $("scan-again").addEventListener("click", () => {
    haptic();
    setScanState("idle");
  });
}

/* ── Профиль ─────────────────────────────────────────────── */

function paintStats(stats, user) {
  if (user) {
    $("pf-name").textContent = user.name || "—";
    const avatar = $("pf-avatar");
    if (user.photo) {
      avatar.outerHTML = `<img class="avatar" id="pf-avatar" src="${user.photo}" alt="">`;
    } else {
      avatar.textContent = (user.name || "?").trim().charAt(0).toUpperCase();
    }
  }

  if (!stats) {
    $("pf-tier").textContent = "Отчётов пока нет";
    return;
  }

  $("pf-best").textContent = stats.best.toFixed(1);
  $("pf-avg").textContent = stats.average.toFixed(1);
  $("pf-count").textContent = stats.count;
  $("pf-tier").textContent = `Последний отчёт: ${stats.last.toFixed(1)}`;
}

async function loadProfile() {
  try {
    const data = await api("/api/profile");
    paintStats(data.stats, data.user);
    await loadHistory(state.period);
  } catch (error) {
    toast(error.message);
  }
}

async function loadHistory(period) {
  try {
    const data = await api(`/api/history?period=${period}`);
    drawChart(data.points);
  } catch (error) {
    toast(error.message);
  }
}

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function formatLabel(label, period) {
  if (period === "month") {
    const [year, month] = label.split("-");
    return `${MONTHS[Number(month) - 1]} ${year.slice(2)}`;
  }
  if (period === "week") return label.replace(/^\d{4}-W/, "нед. ");
  const [, month, day] = label.split("-");
  return `${day} ${MONTHS[Number(month) - 1]}`;
}

function drawChart(points) {
  const box = $("chart-box");

  if (!points.length) {
    box.innerHTML =
      '<div class="chart-empty">Здесь появится динамика,<br>когда наберётся несколько отчётов.</div>';
    return;
  }

  if (points.length === 1) {
    const only = points[0];
    box.innerHTML =
      `<div class="chart-empty">Пока один отчёт: <b>${only.value.toFixed(1)}</b><br>` +
      "Линия появится со второго.</div>";
    return;
  }

  const W = 320;
  const H = 148;
  const padX = 14;
  const padTop = 16;
  const padBottom = 26;

  const values = points.map((p) => p.value);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = Math.max(0.8, rawMax - rawMin);
  const min = Math.max(0, rawMin - span * 0.35);
  const max = Math.min(10, rawMax + span * 0.35);

  const x = (i) => padX + (i * (W - padX * 2)) / (points.length - 1);
  const y = (v) =>
    padTop + (1 - (v - min) / (max - min || 1)) * (H - padTop - padBottom);

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ");
  const area = `${line} L${x(points.length - 1)},${H - padBottom} L${x(0)},${H - padBottom} Z`;

  const dots = points
    .map(
      (p, i) =>
        `<circle cx="${x(i)}" cy="${y(p.value)}" r="3.4" fill="#07080d" ` +
        `stroke="#5b4bff" stroke-width="2"/>`
    )
    .join("");

  const gridLines = [0, 0.5, 1]
    .map(
      (t) =>
        `<line x1="${padX}" y1="${padTop + t * (H - padTop - padBottom)}" ` +
        `x2="${W - padX}" y2="${padTop + t * (H - padTop - padBottom)}" ` +
        `stroke="rgba(255,255,255,0.055)" stroke-width="1"/>`
    )
    .join("");

  const first = formatLabel(points[0].label, state.period);
  const last = formatLabel(points[points.length - 1].label, state.period);

  const delta = points[points.length - 1].value - points[0].value;
  const deltaText =
    delta > 0.05 ? `+${delta.toFixed(1)}` : delta < -0.05 ? delta.toFixed(1) : "0.0";
  const deltaColor =
    delta > 0.05 ? "#00e0c6" : delta < -0.05 ? "rgba(242,243,247,0.5)" : "var(--ink-3)";

  box.innerHTML = `
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px">
      <span class="tiny">За период</span>
      <span class="numeral" style="font-size:17px;color:${deltaColor}">${deltaText}</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
      ${gridLines}
      <path d="${area}" fill="url(#areaGrad)"/>
      <path d="${line}" fill="none" stroke="url(#ringGrad)" stroke-width="2.4"
            stroke-linecap="round" stroke-linejoin="round"/>
      ${dots}
      <text x="${padX}" y="${H - 6}" fill="rgba(242,243,247,0.34)" font-size="10.5"
            font-family="-apple-system,sans-serif">${first}</text>
      <text x="${W - padX}" y="${H - 6}" fill="rgba(242,243,247,0.34)" font-size="10.5"
            text-anchor="end" font-family="-apple-system,sans-serif">${last}</text>
    </svg>`;
}

function syncSegmentPill() {
  const container = $("segments");
  const active = container.querySelector(".segment.active");
  if (active && active.getBoundingClientRect().width) {
    movePill($("seg-pill"), active, container);
  }
}

function initSegments() {
  const container = $("segments");
  const pill = $("seg-pill");
  const buttons = [...container.querySelectorAll(".segment")];

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      haptic();
      buttons.forEach((b) => b.classList.toggle("active", b === button));
      movePill(pill, button, container);
      state.period = button.dataset.period;
      loadHistory(state.period);
    });
  });

  window.addEventListener("resize", () => {
    syncSegmentPill();
    const tabs = $("tabs");
    const activeTab = tabs.querySelector(".tab.active");
    if (activeTab) movePill($("tab-pill"), activeTab, tabs);
  });
}

/* ── Гайды ───────────────────────────────────────────────── */

const CHEVRON =
  '<svg class="guide-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M9 18l6-6-6-6"/></svg>';

function renderBlock(block) {
  const heading = block.heading ? `<h3>${block.heading}</h3>` : "";
  if (block.list) {
    return `<div class="guide-block">${heading}<ul>${block.list
      .map((item) => `<li>${item}</li>`)
      .join("")}</ul></div>`;
  }
  return `<div class="guide-block">${heading}<p>${block.text}</p></div>`;
}

async function loadGuides() {
  try {
    const data = await api("/api/guides");
    state.guides = data.guides;
  } catch (error) {
    return;
  }

  $("guides-list").innerHTML = state.guides
    .map(
      (guide) => `
      <article class="glass guide" data-id="${guide.id}">
        <div class="guide-head">
          <div class="guide-emoji">${guide.emoji}</div>
          <div class="guide-meta">
            <h2>${guide.title}</h2>
            <p class="tiny">${guide.tagline} · ${guide.read_minutes} мин</p>
          </div>
          ${CHEVRON}
        </div>
        <div class="guide-body"><div class="guide-inner">
          <div class="guide-content">${guide.blocks.map(renderBlock).join("")}</div>
        </div></div>
      </article>`
    )
    .join("");

  document.querySelectorAll(".guide-head").forEach((head) => {
    head.addEventListener("click", () => {
      haptic();
      head.parentElement.classList.toggle("open");
    });
  });
}

/* ── Старт ───────────────────────────────────────────────── */

async function boot() {
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#07080d");
      tg.setBackgroundColor("#07080d");
    } catch (_) {}
    state.initData = tg.initData || "";
  }

  if (!state.initData) {
    $("ob-step-1").innerHTML =
      '<div class="mark">🔒</div>' +
      '<h1 style="margin-top:24px">Открой через Telegram</h1>' +
      '<p class="muted" style="margin-top:12px;font-size:15px">' +
      "Приложение авторизует пользователя подписью Telegram, поэтому в обычном " +
      "браузере оно не работает. Запусти его кнопкой в боте.</p>";
    return;
  }

  buildAgeGrid();
  initScan();
  initSegments();

  $("ob-next").addEventListener("click", () => {
    haptic("medium");
    $("ob-step-1").classList.add("hidden");
    $("ob-step-2").classList.remove("hidden");
  });

  try {
    const session = await api("/api/session", { method: "POST" });
    state.minAge = session.min_age;
    paintStats(session.stats, session.user);

    if (session.onboarded) {
      session.age_ok ? enterApp() : showBlocked();
    }
  } catch (error) {
    toast(error.message);
  }
}

boot();
