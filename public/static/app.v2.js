/* ============================================================
   LOOKSCORE — логика мини-аппа
   ============================================================ */

import { analyseImage, drawMesh, detectorFailed } from "/static/facemesh.v2.js";
import { renderCard, cardData, availableThemes, toBlob } from "/static/sharecard.v2.js";

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);

const state = {
  initData: "",
  period: "day",
  minAge: 16,
  streak: 0,
  channel: { required: false, url: "", title: "" },
  brand: "LOOKSCORE",
  botUsername: "",
  report: null,
  lastScan: null,
  theme: "classic",
  cardTheme: 0,
  refCode: "",
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
  let response;

  try {
    response = await fetch(path, {
      method,
      headers: { "X-Init-Data": state.initData },
      body,
    });
  } catch (_) {
    throw new Error("Нет связи с сервером. Проверь интернет и попробуй снова.");
  }

  if (!response.ok) {
    // Сервер отвечает JSON с полем detail. Если пришло что-то другое —
    // значит упала сама платформа, и код ответа тут единственная зацепка.
    let detail = "";
    try {
      detail = (await response.json()).detail || "";
    } catch (_) {}

    if (response.status === 403 && detail.includes("подписка")) {
      showGate();
      throw new Error("Нужна подписка на канал");
    }

    if (!detail) {
      detail =
        response.status === 401
          ? "Сессия истекла. Закрой и открой приложение заново."
          : `Ошибка ${response.status}. Открой /api/health — там причина.`;
    }
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
      if (button.dataset.screen === "s-today") loadToday();
      if (button.dataset.screen === "s-friends") loadFriends();
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

/* ── Экран «Сегодня» ─────────────────────────────────────── */

const CHECK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.2" ' +
  'stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>';

function renderToday(data) {
  const streak = data.streak;

  $("td-streak").textContent = streak.current;
  $("td-best").textContent = streak.best;
  $("td-flame").classList.toggle("lit", streak.current > 0);
  $("td-streak-label").textContent =
    streak.current === 0
      ? "серия ещё не начата"
      : streak.grace_used
        ? "дней подряд · пропуск прощён"
        : "дней подряд";

  const rank = data.rank;
  $("td-rank-emoji").textContent = rank.emoji;
  $("td-rank-title").textContent = rank.title;
  $("td-rank-caption").textContent = rank.caption;

  if (rank.next) {
    const done = streak.total_days;
    const target = done + rank.next.days_left;
    $("td-rank-fill").style.width = `${Math.min(100, (done / target) * 100)}%`;
    $("td-rank-next").textContent =
      `Ещё ${rank.next.days_left} дн. до ранга «${rank.next.title}» ${rank.next.emoji}`;
  } else {
    $("td-rank-fill").style.width = "100%";
    $("td-rank-next").textContent = "Максимальный ранг достигнут";
  }

  $("td-progress").textContent = `${data.done_today} / ${data.habits.length}`;
  $("td-need").textContent =
    data.done_today >= data.need_today
      ? "День засчитан в серию"
      : `Отметь ещё ${data.need_today - data.done_today}, чтобы день пошёл в серию`;

  $("td-habits").innerHTML = data.habits
    .map(
      (habit) => `
      <button class="habit${habit.done ? " done" : ""}" data-habit="${habit.key}">
        <span class="habit-emoji">${habit.emoji}</span>
        <span class="habit-text"><b>${habit.title}</b><span>${habit.hint}</span></span>
        <span class="tick">${CHECK}</span>
      </button>`
    )
    .join("");

  document.querySelectorAll("[data-habit]").forEach((button) => {
    button.addEventListener("click", () => markHabit(button.dataset.habit));
  });

  const scans = data.scans;
  const gift = scans.gift ? ` · +${scans.gift} в подарок` : "";
  const quotaText = scans.unlimited
    ? `Без ограничений · собрано сегодня: ${scans.used}`
    : scans.left > 0
      ? `Осталось ${scans.left} из ${scans.limit}${gift}`
      : "Лимит исчерпан, новые после полуночи";

  const quotaDots = scans.unlimited
    ? '<span class="quota-inf">∞</span>'
    : `<div class="quota-dots">${Array.from(
        { length: scans.limit },
        (_, i) => `<i class="${i < scans.left ? "left" : ""}"></i>`
      ).join("")}</div>`;

  $("td-quota").innerHTML =
    `<div style="min-width:0"><h3>Отчёты сегодня</h3>` +
    `<p class="tiny" style="margin-top:3px">${quotaText}</p></div>${quotaDots}`;

  // Докупка появляется, только когда попытки на исходе — иначе кнопка
  // мозолит глаза тем, кто и так не исчерпал лимит.
  const buyBox = $("td-buy-scan");
  const showBuy = !scans.unlimited && scans.left === 0 && scans.can_buy;
  buyBox.classList.toggle("hidden", !showBuy);
  if (showBuy) {
    buyBox.innerHTML =
      `<div style="min-width:0"><h3>Нужна ещё попытка?</h3>` +
      `<p class="tiny" style="margin-top:3px">Докупить можно за ` +
      `${scans.extra_price} XP</p></div>` +
      `<button class="shop-price" id="buy-scan-btn">${scans.extra_price} XP</button>`;
    $("buy-scan-btn").addEventListener("click", buyScan);
  }

  const xp = data.xp || { balance: 0 };
  $("td-xp").innerHTML =
    `<div style="min-width:0"><h3>Твои XP</h3>` +
    `<p class="tiny" style="margin-top:3px">Копятся за привычки и отчёты, ` +
    `тратятся на гайды</p></div>` +
    `<span class="xp-amount">${xp.unlimited ? "∞" : xp.balance}</span>`;

  $("td-achievements").innerHTML = data.achievements
    .map(
      (item) => `
      <div class="ach${item.unlocked ? " on" : ""}" title="${item.description}">
        <div class="ach-emoji">${item.emoji}</div>
        <div class="ach-title">${item.title}</div>
      </div>`
    )
    .join("");
}

async function buyScan() {
  haptic("medium");
  try {
    await api("/api/buy-scan", { method: "POST" });
    notifySuccess();
    toast("Попытка добавлена");
    loadToday();
  } catch (error) {
    toast(error.message);
  }
}

async function loadToday() {
  try {
    renderToday(await api("/api/today"));
  } catch (error) {
    toast(error.message);
  }
}


/**
 * Копирование с проверкой результата.
 *
 * navigator.clipboard в Telegram WebView часто резолвится успешно, но в
 * буфер ничего не кладёт. Поэтому пробуем оба способа и проверяем, что
 * текст действительно попал в буфер, а не полагаемся на отсутствие ошибки.
 */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    const written = await navigator.clipboard.readText().catch(() => null);
    if (written === null || written === text) return true;
  } catch (_) {}

  // Запасной путь: скрытое поле и старая команда копирования
  try {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
    document.body.appendChild(field);
    field.select();
    field.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(field);
    return ok;
  } catch (_) {
    return false;
  }
}

function renderFriends(data) {
  state.refCode = data.code || "";
  $("ref-code").textContent = state.refCode || "—";
  $("ref-invited").textContent = data.invited;
  $("ref-earned").textContent = data.invited * data.reward;
  $("ref-reward").textContent = `+${data.reward}`;
  $("ref-note").textContent =
    `За каждого друга по твоему коду — +${data.reward} XP тебе и +${data.bonus} XP ему.`;

  // Код можно применить только один раз, дальше карточка ввода не нужна
  $("ref-enter-card").classList.toggle("hidden", data.used);
  $("ref-enter-note").textContent =
    `Введи код того, кто тебя позвал — получишь +${data.bonus} XP.`;
}

async function loadFriends() {
  try {
    renderFriends(await api("/api/referral"));
  } catch (error) {
    $("ref-code").textContent = "—";
    toast(error.message);
  }
}

function initReferral() {
  $("ref-copy").addEventListener("click", async () => {
    haptic("medium");
    const code = state.refCode;
    if (!code) return;
    toast(
      (await copyText(code))
        ? "Код скопирован"
        : `Скопируй вручную: ${code}`
    );
  });

  $("ref-share").addEventListener("click", () => {
    haptic("medium");
    const code = state.refCode;
    if (!code) return;
    const message =
      `Мой код в ${state.brand}: ${code}\n` +
      `Введи его в ${state.botUsername} и получишь стартовые XP.`;

    if (tg?.openTelegramLink) {
      tg.openTelegramLink(
        `https://t.me/share/url?url=${encodeURIComponent(
          "https://t.me/" + (state.botUsername || "").replace("@", "")
        )}&text=${encodeURIComponent(message)}`
      );
    } else if (navigator.share) {
      navigator.share({ text: message });
    } else {
      copyText(message).then(() => toast("Приглашение скопировано"));
    }
  });

  $("ref-apply").addEventListener("click", async () => {
    haptic("medium");
    const value = ($("ref-input").value || "").trim().toUpperCase();
    if (!value) return toast("Введи код друга");

    const button = $("ref-apply");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>';

    const body = new FormData();
    body.append("code", value);

    try {
      const result = await api("/api/referral", { method: "POST", body });
      notifySuccess();
      toast(`Код принят, +${result.bonus} XP`);
      $("ref-input").value = "";
      loadFriends();
      loadToday();
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Применить код";
    }
  });
}

async function markHabit(key) {
  haptic("medium");
  const body = new FormData();
  body.append("key", key);

  try {
    const data = await api("/api/habit", { method: "POST", body });
    const before = state.streak;
    renderToday(data);
    state.streak = data.streak.current;

    // Отмечаем только рост серии — «минус день» подсвечивать незачем.
    if (data.streak.current > before) {
      notifySuccess();
      toast(`Серия: ${data.streak.current} ${plural(data.streak.current)} подряд`);
    }
  } catch (error) {
    toast(error.message);
  }
}

function plural(n) {
  const tail = n % 100 >= 11 && n % 100 <= 14 ? 0 : n % 10;
  return tail === 1 ? "день" : tail >= 2 && tail <= 4 ? "дня" : "дней";
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
      if (!result.age_ok) {
        showBlocked();
        return;
      }
      const session = await api("/api/session", { method: "POST" });
      session.subscribed ? enterApp() : showGate();
    } catch (error) {
      toast(error.message);
      button.disabled = false;
      button.textContent = "Сохранить";
    }
  });
}

function showGate() {
  $("ob-step-1").classList.add("hidden");
  $("ob-step-2").classList.add("hidden");
  $("ob-blocked").classList.add("hidden");
  $("ob-gate").classList.remove("hidden");
  showScreen("s-onboard");
  $("tabs").classList.remove("visible");
  $("gate-channel").textContent = state.channel.title || "канала";
}

function initGate() {
  $("gate-open").addEventListener("click", () => {
    haptic("medium");
    const url = state.channel.url;
    if (!url) return toast("Ссылка на канал не настроена");
    // openTelegramLink открывает канал внутри клиента, не выбрасывая в браузер
    if (tg?.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank");
  });

  $("gate-check").addEventListener("click", async () => {
    haptic("medium");
    const button = $("gate-check");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>';

    try {
      const result = await api("/api/check-subscription", { method: "POST" });
      if (result.subscribed) {
        notifySuccess();
        $("ob-gate").classList.add("hidden");
        enterApp();
      } else {
        toast("Подписка не найдена. Подпишись и попробуй ещё раз.");
      }
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Я подписался";
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
  showScreen("s-today");
  initTabs();
  loadToday();
  loadGuides();
}

/* ── Скан ────────────────────────────────────────────────── */

const SCAN_STAGES = [
  ["Контур лица найден", 14],
  ["Замер наклона глазной щели", 33],
  ["Линия челюсти и ось симметрии", 55],
  ["Пропорции третей лица", 76],
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

async function sha256(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Размеры и смещение картинки внутри превью при object-fit: cover. */
function coverBox(image, canvas) {
  const scale = Math.max(
    canvas.width / image.naturalWidth,
    canvas.height / image.naturalHeight
  );
  const drawWidth = image.naturalWidth * scale;
  const drawHeight = image.naturalHeight * scale;
  return {
    drawWidth,
    drawHeight,
    offsetX: (canvas.width - drawWidth) / 2,
    offsetY: (canvas.height - drawHeight) / 2,
  };
}

async function runScan(rawFile) {
  const file = await prepareImage(rawFile);

  if (file.size > 4_000_000) {
    toast("Фото слишком тяжёлое. Попробуй другой снимок.");
    return;
  }

  const url = URL.createObjectURL(file);
  const image = $("preview-img");
  image.src = url;
  setScanState("run");
  $("scan-label").textContent = "Ищу лицо";
  $("scan-pct").textContent = "0%";

  const cleanup = () => {
    URL.revokeObjectURL(url);
    $("mesh").classList.remove("on");
  };

  try {
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error("Не удалось открыть фото"));
    });

    const found = await analyseImage(image);

    // Разметка загрузилась, но лица нет — оценивать нечего.
    if (!found && !detectorFailed()) {
      toast("На фото не найдено лицо. Нужен снимок анфас, лицо целиком в кадре.");
      setScanState("idle");
      cleanup();
      return;
    }

    const form = new FormData();
    if (found) {
      // Замеры уже посчитаны на устройстве, поэтому сам снимок не отправляем.
      form.append("photo_hash", await sha256(file));
      form.append("metrics", JSON.stringify(found.metrics));
    } else {
      form.append("photo", file, file.name || "photo.jpg");
    }

    const request = api("/api/rate", { method: "POST", body: form });

    const canvas = $("mesh");
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * (window.devicePixelRatio || 1);
    canvas.height = rect.height * (window.devicePixelRatio || 1);
    const box = found ? coverBox(image, canvas) : null;

    if (found) canvas.classList.add("on");

    let step = 0;
    for (const [label, percent] of SCAN_STAGES) {
      $("scan-label").textContent = label;
      $("scan-pct").textContent = `${percent}%`;
      if (found) drawMesh(canvas, found.landmarks, box, ++step);
      await wait(620);
    }

    const report = await request;
    // Нужно для оценки от пользователя: те же замеры, тот же снимок
    state.lastScan = found ? { hash: await sha256(file), metrics: found.metrics } : null;
    loadToday();
    $("scan-label").textContent = "Готово";
    $("scan-pct").textContent = "100%";
    await wait(380);
    renderReport(report);
    notifySuccess();
  } catch (error) {
    toast(error.message);
    setScanState("idle");
  } finally {
    cleanup();
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

/**
 * Насколько снимку можно доверять.
 *
 * Взято из того, как это устроено у сервисов вроде FreePSL: качество фото
 * показывается отдельно от оценки. Это честнее и снимает главный вопрос
 * «почему на двух фото разные баллы» — чаще всего дело в повороте и свете,
 * а не в лице.
 */
function photoConfidence(metrics) {
  if (!metrics) return null;

  const issues = [];
  let score = 100;

  if (metrics.yaw > 0.05) {
    score -= Math.min(35, (metrics.yaw - 0.05) * 400);
    issues.push("лицо повёрнуто — снимай строго анфас");
  }
  if (metrics.roll > 7) {
    score -= Math.min(20, (metrics.roll - 7) * 1.6);
    issues.push("голова наклонена");
  }
  if (metrics.face_share < 0.28) {
    score -= Math.min(25, (0.28 - metrics.face_share) * 180);
    issues.push("лицо мелко в кадре — подойди ближе");
  }
  if (metrics.skin_variance !== undefined && metrics.skin_variance > 0.22) {
    score -= 10;
    issues.push("резкие тени или шум");
  }

  return { score: Math.max(20, Math.round(score)), issues };
}

function renderReport(report) {
  setScanState("result");
  state.report = report;
  // Своя оценка возможна только там, где лицо действительно распозналось
  $("fb-card").classList.toggle("hidden", !state.lastScan);
  $("fb-range").value = report.overall;
  $("fb-value").textContent = report.overall.toFixed(1);

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

  // Уверенность в снимке
  const conf = photoConfidence(state.lastScan?.metrics);
  const confBox = $("res-confidence");
  if (conf) {
    const tone = conf.score >= 80 ? "var(--aura-mint)" : conf.score >= 55 ? "#ffb443" : "var(--aura-rose)";
    confBox.classList.remove("hidden");
    confBox.innerHTML =
      `<div style="display:flex;align-items:baseline;justify-content:space-between">` +
      `<p class="eyebrow">Качество снимка</p>` +
      `<span class="numeral" style="font-size:19px;color:${tone}">${conf.score}%</span></div>` +
      (conf.issues.length
        ? `<p class="tiny" style="margin-top:7px">${conf.issues.join(" · ")}</p>` +
          `<p class="tiny" style="margin-top:6px;color:var(--ink-3)">` +
          `На таком фото оценка менее устойчива — переснимись и сравни.</p>`
        : `<p class="tiny" style="margin-top:7px">Снимок подходит: анфас, ровно, лицо крупно.</p>`);
  } else {
    confBox.classList.add("hidden");
  }

  const measurements = report.measurements || [];
  $("res-measure-card").classList.toggle("hidden", measurements.length === 0);
  $("res-measurements").innerHTML = measurements
    .map(
      (item) =>
        `<div class="measure"><span class="measure-emoji">${item.emoji}</span>` +
        `<span class="measure-title">${item.title}</span>` +
        `<span class="measure-value">${item.value}</span></div>`
    )
    .join("");

  $("res-tips").innerHTML = report.tips
    .map(
      (tip) =>
        `<div><h3>${tip.emoji} ${tip.title}</h3>` +
        `<p class="tiny" style="margin-top:4px">${tip.text}</p></div>`
    )
    .join("");
}

function initFeedback() {
  $("fb-range").addEventListener("input", (event) => {
    $("fb-value").textContent = Number(event.target.value).toFixed(1);
  });

  $("fb-send").addEventListener("click", async () => {
    if (!state.lastScan) return;
    haptic("medium");

    const body = new FormData();
    body.append("photo_hash", state.lastScan.hash);
    body.append("score", $("fb-range").value);
    body.append("metrics", JSON.stringify(state.lastScan.metrics));

    try {
      await api("/api/feedback", { method: "POST", body });
      notifySuccess();
      toast("Спасибо, учтём. +3 XP");
      $("fb-card").classList.add("hidden");
      loadToday();
    } catch (error) {
      toast(error.message);
    }
  });
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

/* ── Карточка «поделиться» ───────────────────────────────── */

function closeSheet() {
  $("sheet").classList.remove("open");
  $("sheet-veil").classList.remove("open");
}

function markActiveSlide(index) {
  state.cardTheme = index;
  document.querySelectorAll(".card-slide").forEach((slide, i) =>
    slide.classList.toggle("active", i === index)
  );
  document.querySelectorAll("#card-dots i").forEach((dot, i) =>
    dot.classList.toggle("on", i === index)
  );
}

function openSheet() {
  const report = state.report;
  if (!report) return;

  haptic("medium");
  if (!state.refCode) loadFriends();
  const themes = availableThemes(report);
  const data = cardData(report, state.brand, state.botUsername, state.refCode);

  const rail = $("card-rail");
  rail.innerHTML = "";
  themes.forEach((theme, index) => {
    const slide = document.createElement("div");
    slide.className = `card-slide${index === 0 ? " active" : ""}`;
    const canvas = document.createElement("canvas");
    slide.appendChild(canvas);
    rail.appendChild(slide);
    renderCard(canvas, theme.id, data);
    slide.addEventListener("click", () =>
      slide.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" })
    );
  });

  $("card-dots").innerHTML = themes
    .map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`)
    .join("");

  state.cardTheme = 0;
  rail.scrollLeft = 0;
  $("sheet-veil").classList.add("open");
  $("sheet").classList.add("open");
}

/** Определяет карточку по центру видимой области. */
function trackRail() {
  const rail = $("card-rail");
  let timer;
  rail.addEventListener("scroll", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const middle = rail.scrollLeft + rail.clientWidth / 2;
      const slides = [...rail.children];
      let nearest = 0;
      let best = Infinity;
      slides.forEach((slide, i) => {
        const center = slide.offsetLeft + slide.offsetWidth / 2 - rail.offsetLeft;
        const gap = Math.abs(center - middle);
        if (gap < best) {
          best = gap;
          nearest = i;
        }
      });
      if (nearest !== state.cardTheme) {
        haptic();
        markActiveSlide(nearest);
      }
    }, 90);
  });
}

function currentCanvas() {
  return document.querySelectorAll(".card-slide canvas")[state.cardTheme];
}

async function shareCard() {
  const canvas = currentCanvas();
  if (!canvas) return;

  haptic("medium");
  const blob = await toBlob(canvas);
  const file = new File([blob], "lookscore.png", { type: "image/png" });
  const caption = `Мой результат в ${state.brand} ${state.botUsername}`;

  // Web Share умеет отдавать файл прямо в мессенджеры. Если он недоступен
  // (старый WebView), остаётся сохранение картинки.
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], text: caption });
      closeSheet();
    } catch (error) {
      if (error.name !== "AbortError") saveCard();
    }
    return;
  }

  saveCard();
}

function saveCard() {
  const canvas = currentCanvas();
  if (!canvas) return;

  haptic();
  const link = document.createElement("a");
  link.download = "lookscore.png";
  link.href = canvas.toDataURL("image/png");
  link.click();
  toast("Карточка сохранена — теперь её можно отправить куда угодно");
}

function initShare() {
  $("share-open").addEventListener("click", openSheet);
  $("sheet-veil").addEventListener("click", closeSheet);
  $("share-send").addEventListener("click", shareCard);
  $("share-save").addEventListener("click", saveCard);
  trackRail();
}

/* ── Разметка (владелец) ─────────────────────────────────── */

const TIER_HINTS = [
  ["Sub-3", 0.8], ["Sub-5", 1.9], ["LTN", 3.2],
  ["MTN", 4.6], ["HTN", 5.9], ["Chadlite", 6.9], ["Chad", 8.0],
];

const labeling = { queue: [], current: null, done: 0, saved: 0 };

function showProgress(text) {
  $("lb-progress").innerHTML =
    `<div style="min-width:0"><h3>Прогресс</h3>` +
    `<p class="tiny" style="margin-top:3px">${text}</p></div>`;
}

async function nextLabel() {
  $("lb-work").classList.add("hidden");

  while (labeling.queue.length) {
    const file = labeling.queue.shift();
    labeling.done += 1;
    showProgress(
      `Осталось ${labeling.queue.length} · сохранено ${labeling.saved}`
    );

    const prepared = await prepareImage(file, 1280, 0.9);
    const url = URL.createObjectURL(prepared);
    const image = $("lb-img");
    image.src = url;

    try {
      await new Promise((ok, no) => {
        image.onload = ok;
        image.onerror = no;
      });
      const found = await analyseImage(image);
      if (!found) {
        toast("Лицо не найдено, пропускаю");
        URL.revokeObjectURL(url);
        continue;
      }
      labeling.current = {
        hash: await sha256(prepared),
        metrics: found.metrics,
        url,
      };
      $("lb-work").classList.remove("hidden");
      return;
    } catch (_) {
      URL.revokeObjectURL(url);
    }
  }

  showProgress(
    labeling.saved
      ? `Готово. Сохранено в этот заход: ${labeling.saved}`
      : "Выбери фото, чтобы начать"
  );
  labeling.current = null;
}

async function saveLabel() {
  if (!labeling.current) return;
  haptic("medium");

  const body = new FormData();
  body.append("photo_hash", labeling.current.hash);
  body.append("score", $("lb-range").value);
  body.append("metrics", JSON.stringify(labeling.current.metrics));

  try {
    const result = await api("/api/label", { method: "POST", body });
    labeling.saved += 1;
    toast(
      result.added
        ? `Сохранено. Всего примеров: ${result.total}`
        : `Это фото уже размечено. Всего: ${result.total}`
    );
  } catch (error) {
    toast(error.message);
  }

  URL.revokeObjectURL(labeling.current.url);
  nextLabel();
}

function initLabeling() {
  $("lb-quick").innerHTML = TIER_HINTS.map(
    ([name, value]) => `<button data-score="${value}">${name}</button>`
  ).join("");

  $("lb-quick").addEventListener("click", (event) => {
    const value = event.target.dataset?.score;
    if (!value) return;
    haptic();
    $("lb-range").value = value;
    $("lb-value").textContent = Number(value).toFixed(1);
  });

  $("lb-range").addEventListener("input", (event) => {
    $("lb-value").textContent = Number(event.target.value).toFixed(1);
  });

  $("lb-pick").addEventListener("click", () => $("lb-files").click());

  $("lb-files").addEventListener("change", (event) => {
    labeling.queue = [...event.target.files];
    labeling.done = 0;
    labeling.saved = 0;
    event.target.value = "";
    if (labeling.queue.length) nextLabel();
  });

  $("lb-save").addEventListener("click", saveLabel);
  $("lb-skip").addEventListener("click", () => {
    haptic();
    if (labeling.current) URL.revokeObjectURL(labeling.current.url);
    nextLabel();
  });

  showProgress("Выбери фото, чтобы начать");
}

/* ── Оформление ──────────────────────────────────────────── */

const THEMES = [
  { id: "classic", name: "Классика", colors: ["#5b4bff", "#ff3d71", "#00e0c6"], bg: "#07080d" },
  { id: "graphite", name: "Графит", colors: ["#6e7480", "#9aa1ad", "#d8dce3"], bg: "#0a0a0c" },
  { id: "mocha", name: "Мокко", colors: ["#6b5442", "#a87f5e", "#57d6b0"], bg: "#100c0a" },
  { id: "sapphire", name: "Сапфир", colors: ["#2563eb", "#0ea5e9", "#7dd3fc"], bg: "#05080f" },
];

function applyTheme(id) {
  state.theme = id;
  // Классика живёт в :root, поэтому для неё атрибут просто снимается
  if (id && id !== "classic") document.documentElement.dataset.theme = id;
  else delete document.documentElement.dataset.theme;

  const theme = THEMES.find((t) => t.id === id) || THEMES[0];
  try {
    tg?.setHeaderColor(theme.bg);
    tg?.setBackgroundColor(theme.bg);
  } catch (_) {}

  document.querySelectorAll("[data-theme-id]").forEach((card) =>
    card.classList.toggle("on", card.dataset.themeId === id)
  );
}

function initThemes() {
  $("theme-grid").innerHTML = THEMES.map(
    (t) => `
      <button class="theme-card" data-theme-id="${t.id}">
        <span class="theme-swatch" style="background:${t.bg}">
          ${t.colors.map((c) => `<i style="background:${c}"></i>`).join("")}
        </span>
        <span class="theme-name">${t.name}</span>
      </button>`
  ).join("");

  document.querySelectorAll("[data-theme-id]").forEach((card) => {
    card.addEventListener("click", async () => {
      haptic();
      const id = card.dataset.themeId;
      applyTheme(id);

      const body = new FormData();
      body.append("theme", id);
      try {
        await api("/api/theme", { method: "POST", body });
      } catch (_) {
        // Выбор уже применён на экране: если сохранить не вышло,
        // тревожить сообщением незачем, вернётся при следующем входе.
      }
    });
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

async function buyGuide(id, price) {
  haptic("medium");
  const body = new FormData();
  body.append("guide_id", id);

  try {
    await api("/api/buy", { method: "POST", body });
    notifySuccess();
    toast("Гайд открыт");
    await loadGuides();
    loadToday();
  } catch (error) {
    toast(error.message);
  }
}

function renderShop(shop, balance, unlimited) {
  $("shop-balance").innerHTML =
    `<div style="min-width:0"><h3>Баланс</h3>` +
    `<p class="tiny" style="margin-top:3px">Отмечай привычки каждый день — ` +
    `гайд копится примерно за неделю</p></div>` +
    `<span class="xp-amount">${unlimited ? "∞" : balance}</span>`;

  $("shop-list").innerHTML = shop
    .map((guide) => {
      const affordable = unlimited || balance >= guide.price;
      const button = guide.owned
        ? '<span class="shop-price owned">Открыт</span>'
        : `<button class="shop-price${affordable ? "" : " locked"}" ` +
          `data-buy="${guide.id}" data-price="${guide.price}">${guide.price} XP</button>`;

      const body = guide.owned
        ? `<div class="guide-body"><div class="guide-inner"><div class="guide-content">` +
          `${guide.blocks.map(renderBlock).join("")}</div></div></div>`
        : "";

      return `<article class="glass guide shop-item" data-id="${guide.id}">
          <div class="shop-head">
            <div class="guide-emoji">${guide.emoji}</div>
            <div class="guide-meta">
              <h2>${guide.title}</h2>
              <p class="tiny">${guide.tagline}</p>
            </div>
            ${button}
          </div>${body}
        </article>`;
    })
    .join("");

  document.querySelectorAll("[data-buy]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      buyGuide(button.dataset.buy, Number(button.dataset.price));
    });
  });

  // Купленный гайд раскрывается по нажатию, как и бесплатный
  document.querySelectorAll(".shop-item").forEach((item) => {
    if (!item.querySelector(".guide-body")) return;
    item.querySelector(".shop-head").addEventListener("click", () =>
      item.classList.toggle("open")
    );
  });
}

async function loadGuides() {
  let data;
  try {
    data = await api("/api/guides");
    state.guides = data.guides;
    renderShop(data.shop || [], data.balance || 0, data.unlimited);
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
  initGate();
  initShare();
  initReferral();
  initLabeling();
  initFeedback();
  initThemes();
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

    state.channel = session.channel || state.channel;
    state.brand = session.brand || state.brand;
    // Вкладка разметки существует только для владельца
    if (session.is_admin) $("tab-label").classList.remove("hidden");
    applyTheme(session.theme || "classic");
    state.botUsername = session.bot_username || "";

    if (session.onboarded) {
      if (!session.age_ok) showBlocked();
      else if (!session.subscribed) showGate();
      else enterApp();
    }
  } catch (error) {
    toast(error.message);
  }
}

boot();
