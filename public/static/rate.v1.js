/* Раздел «Оценки» внутри основного приложения.
   Живёт рядом с app.v2.js и не трогает его состояние: свой корень,
   свои запросы, свой префикс классов rt-. */

(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const INIT = tg ? tg.initData : "";

  const ROOT = () => document.getElementById("rate-root");

  let st = null;
  let card = null;
  let busy = false;
  let started = false;
  let reason = null;

  async function api(path, opts) {
    const o = opts || {};
    const headers = { "X-Telegram-Init-Data": INIT };
    let body = o.body;
    if (o.json) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(Object.assign({ initData: INIT }, o.json));
    }
    let res;
    try {
      res = await fetch("/api/faces" + path, { method: o.method || "GET", headers, body });
    } catch (e) {
      // Сеть отвалилась: возвращаем свой ответ, иначе исключение уйдёт
      // наверх и экран просто замрёт без объяснений.
      return { ok: false, status: 0, data: { error: "Нет связи с сервером" } };
    }
    let data = {};
    try { data = await res.json(); } catch (e) {}
    return { ok: res.ok, status: res.status, data };
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function haptic(kind) {
    if (!tg || !tg.HapticFeedback) return;
    try {
      if (kind === "ok") tg.HapticFeedback.notificationOccurred("success");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) {}
  }

  /* ── экраны ─────────────────────────────────────────────── */

  function paint(html) { ROOT().innerHTML = html; }

  function screenConsent() {
    paint(
      '<div class="rt-pane">' +
        '<h2 class="rt-h">' + esc(st.disclaimer.title) + "</h2>" +
        '<div class="rt-body">' + esc(st.disclaimer.body) + "</div>" +
        '<div class="rt-links">' +
          (st.links.terms ? '<a href="' + st.links.terms + '" target="_blank" rel="noopener">Условия</a>' : "") +
          (st.links.privacy ? '<a href="' + st.links.privacy + '" target="_blank" rel="noopener">Политика</a>' : "") +
        "</div>" +
        '<button class="rt-btn rt-primary" id="rt-accept">Принимаю, начать</button>' +
      "</div>"
    );
    document.getElementById("rt-accept").onclick = async () => {
      if (busy) return;
      busy = true;
      const r = await api("/consent", { method: "POST", json: {} });
      busy = false;
      if (!r.ok) return void toast("Не удалось сохранить согласие");
      haptic("ok");
      await refresh();
    };
  }

  function screenRegister() {
    paint(
      '<div class="rt-pane">' +
        '<h2 class="rt-h">Анкета</h2>' +
        '<p class="rt-lead">Имя и возраст увидят те, кто будет оценивать.</p>' +
        '<label class="rt-field"><span>Имя</span>' +
          '<input id="rt-name" class="rt-input" maxlength="24" value="' +
          esc(st.suggestedName || "") + '"></label>' +
        '<label class="rt-field"><span>Возраст</span>' +
          '<input id="rt-age" class="rt-input" inputmode="numeric" placeholder="18+"></label>' +
        '<div class="rt-err" id="rt-err"></div>' +
        '<button class="rt-btn rt-primary" id="rt-save">Дальше</button>' +
      "</div>"
    );
    document.getElementById("rt-save").onclick = async () => {
      if (busy) return;
      busy = true;
      const r = await api("/register", {
        method: "POST",
        json: {
          name: document.getElementById("rt-name").value,
          age: document.getElementById("rt-age").value,
        },
      });
      busy = false;
      if (!r.ok) {
        document.getElementById("rt-err").textContent = r.data.error || "Проверьте данные";
        return;
      }
      haptic("ok");
      await refresh();
    };
  }

  function screenPhoto(isReupload) {
    paint(
      '<div class="rt-pane">' +
        '<h2 class="rt-h">' + (isReupload ? "Нужно новое фото" : "Фото анкеты") + "</h2>" +
        '<p class="rt-lead">' +
          (isReupload
            ? "Прежнее фото снято с показа модератором. Загрузите другое, чтобы вернуться в ленту."
            : "Снимок, на котором видно ваше лицо. Только своё фото.") +
        "</p>" +
        '<label class="rt-drop" id="rt-drop">' +
          '<input type="file" id="rt-file" accept="image/jpeg,image/png,image/webp" hidden>' +
          '<img id="rt-prev" hidden alt=""><span id="rt-hint">Выбрать фото</span>' +
        "</label>" +
        '<div class="rt-err" id="rt-err"></div>' +
        '<button class="rt-btn rt-primary" id="rt-up" disabled>Загрузить и начать</button>' +
      "</div>"
    );
    let file = null;
    const input = document.getElementById("rt-file");
    document.getElementById("rt-drop").onclick = () => input.click();
    input.onchange = (e) => {
      file = e.target.files && e.target.files[0];
      if (!file) return;
      const img = document.getElementById("rt-prev");
      img.src = URL.createObjectURL(file);
      img.hidden = false;
      document.getElementById("rt-hint").textContent = "Выбрать другое";
      document.getElementById("rt-up").disabled = false;
    };
    document.getElementById("rt-up").onclick = async () => {
      if (!file || busy) return;
      busy = true;
      const btn = document.getElementById("rt-up");
      const err = document.getElementById("rt-err");
      btn.disabled = true;
      btn.textContent = "Готовим фото…";
      err.textContent = "";

      let r;
      try {
        /* Сжимаем в браузере: снимок с телефона на 5 МБ ужимается до
           двух-трёх сотен килобайт. Заодно решается HEIC с айфона —
           браузер его открывает сам, а на сервер уходит обычный JPEG. */
        const b64 = await shrink(file);
        btn.textContent = "Загружаем…";
        r = await api("/upload-b64", { method: "POST", json: { photo: b64 } });
      } catch (e) {
        /* Canvas не справился — отправляем файл как есть, старым способом. */
        btn.textContent = "Загружаем…";
        const fd = new FormData();
        fd.append("photo", file);
        r = await api("/upload", { method: "POST", body: fd });
      }

      busy = false;
      btn.disabled = false;
      btn.textContent = "Загрузить и начать";

      if (!r.ok) {
        showUploadError(r);
        return;
      }
      haptic("ok");
      await refresh();
    };
  }

  /* Ужимает картинку до 1280 px по длинной стороне и отдаёт base64. */
  async function shrink(file) {
    const MAX = 1280;
    let bmp;
    if (window.createImageBitmap) {
      try {
        // from-image разворачивает снимок по метаданным ориентации,
        // иначе портретное фото с телефона ляжет набок.
        bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      } catch (e) {
        bmp = await createImageBitmap(file);
      }
    } else {
      bmp = await new Promise((res, rej) => {
        const img = new Image();
        img.onload = () => res(img);
        img.onerror = rej;
        img.src = URL.createObjectURL(file);
      });
    }

    const w = bmp.width, h = bmp.height;
    const k = Math.min(1, MAX / Math.max(w, h));
    const cv = document.createElement("canvas");
    cv.width = Math.round(w * k);
    cv.height = Math.round(h * k);
    cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);

    const url = cv.toDataURL("image/jpeg", 0.88);
    if (!url || url.length < 100) throw new Error("canvas пуст");
    return url;
  }

  /* Показываем настоящую причину, а не «что-то пошло не так». */
  function showUploadError(r) {
    haptic("error");
    const err = document.getElementById("rt-err");
    const text = (r.data && r.data.error) || "Не удалось загрузить";
    err.textContent = r.status ? text + " (код " + r.status + ")" : text;

    let btn = document.getElementById("rt-why");
    if (!btn) {
      btn = document.createElement("button");
      btn.id = "rt-why";
      btn.className = "rt-btn rt-ghost";
      btn.textContent = "Показать подробности";
      err.parentNode.insertBefore(btn, err.nextSibling);
    }
    btn.onclick = async () => {
      btn.textContent = "Собираем…";
      const d = await api("/diag");
      const box = document.createElement("pre");
      box.className = "rt-diag";
      box.textContent = d.ok
        ? JSON.stringify(d.data, null, 1)
        : "Диагностика недоступна: код " + d.status;
      btn.replaceWith(box);
    };
  }

  function screenFeed() {
    paint(
      '<div class="rt-stage">' +
        '<div class="rt-card" id="rt-card">' +
          '<img class="rt-photo" id="rt-photo" alt="Анкета">' +
          '<div class="rt-scrim"></div>' +
          '<div class="rt-meta"><b id="rt-nm"></b><span id="rt-ag"></span></div>' +
          '<div class="rt-stamp" id="rt-stamp"></div>' +
          '<button class="rt-flag" id="rt-flag" aria-label="Пожаловаться">!</button>' +
        "</div>" +
        '<div class="rt-empty" id="rt-empty" hidden></div>' +
      "</div>" +
      '<div class="rt-ramp" id="rt-ramp"></div>' +
      '<button class="rt-skip" id="rt-skip">Пропустить</button>' +
      '<div class="rt-sheet" id="rt-sheet" hidden></div>'
    );

    const ramp = document.getElementById("rt-ramp");
    st.grades.forEach((g) => {
      const b = document.createElement("button");
      b.className = "rt-tier";
      b.textContent = g.label;
      b.style.setProperty("--c", g.color);
      b.onclick = () => vote(g);
      ramp.appendChild(b);
    });

    document.getElementById("rt-skip").onclick = skip;
    document.getElementById("rt-flag").onclick = openReport;
    loadCard();
  }

  function setEnabled(on) {
    document.querySelectorAll(".rt-tier").forEach((b) => (b.disabled = !on));
    const s = document.getElementById("rt-skip");
    if (s) s.disabled = !on;
  }

  async function loadCard() {
    setEnabled(false);
    const r = await api("/next");
    if (r.status === 409) return void (await refresh());
    if (!r.ok) return void toast("Не удалось загрузить анкету");

    if (r.data.empty) {
      card = null;
      document.getElementById("rt-card").hidden = true;
      document.getElementById("rt-ramp").hidden = true;
      document.getElementById("rt-skip").hidden = true;
      const e = document.getElementById("rt-empty");
      e.hidden = false;
      e.textContent = r.data.message;
      return;
    }

    card = r.data.card;
    const el = document.getElementById("rt-card");
    el.hidden = false;
    el.classList.remove("rt-out");
    document.getElementById("rt-stamp").classList.remove("rt-on");
    document.getElementById("rt-photo").src = card.photo;
    document.getElementById("rt-nm").textContent = card.name;
    document.getElementById("rt-ag").textContent = card.age;
    setEnabled(true);
  }

  async function vote(g) {
    if (!card || busy) return;
    busy = true;
    setEnabled(false);
    haptic();
    const s = document.getElementById("rt-stamp");
    s.textContent = g.label;
    s.style.color = g.color;
    s.classList.add("rt-on");

    const sent = api("/vote", {
      method: "POST",
      json: { kind: card.kind, id: card.id, grade: g.code },
    });
    await wait(420);
    document.getElementById("rt-card").classList.add("rt-out");
    await wait(170);
    await sent;
    busy = false;
    await loadCard();
  }

  async function skip() {
    if (!card || busy) return;
    busy = true;
    setEnabled(false);
    document.getElementById("rt-card").classList.add("rt-out");
    await wait(170);
    busy = false;
    await loadCard();
  }

  /* ── жалоба ─────────────────────────────────────────────── */

  function openReport() {
    if (!card) return;
    reason = null;
    const sheet = document.getElementById("rt-sheet");
    sheet.hidden = false;
    sheet.innerHTML =
      '<h3 class="rt-h">Пожаловаться</h3>' +
      '<p class="rt-lead">Жалоба уйдёт модератору вместе с фото.</p>' +
      '<div class="rt-reasons">' +
        Object.keys(st.reasons)
          .map((c) => '<button class="rt-reason" data-c="' + c + '">' + esc(st.reasons[c]) + "</button>")
          .join("") +
      "</div>" +
      '<textarea class="rt-input rt-ta" id="rt-cmt" rows="2" maxlength="500" placeholder="Что не так? Необязательно"></textarea>' +
      '<button class="rt-btn rt-danger" id="rt-send" disabled>Отправить</button>' +
      '<button class="rt-btn rt-ghost" id="rt-cancel">Отмена</button>';

    sheet.querySelectorAll(".rt-reason").forEach((b) => {
      b.onclick = () => {
        reason = b.dataset.c;
        sheet.querySelectorAll(".rt-reason").forEach((x) => x.classList.toggle("on", x === b));
        document.getElementById("rt-send").disabled = false;
      };
    });
    document.getElementById("rt-cancel").onclick = () => (sheet.hidden = true);
    document.getElementById("rt-send").onclick = sendReport;
  }

  async function sendReport() {
    if (!card || !reason || busy) return;
    busy = true;
    const r = await api("/report", {
      method: "POST",
      json: {
        kind: card.kind,
        id: card.id,
        reason: reason,
        comment: document.getElementById("rt-cmt").value,
      },
    });
    busy = false;
    document.getElementById("rt-sheet").hidden = true;
    if (!r.ok) return void toast("Не удалось отправить жалобу");
    haptic("ok");
    toast(r.data.duplicate ? "Вы уже жаловались" : "Жалоба отправлена модератору");
    document.getElementById("rt-card").classList.add("rt-out");
    await wait(170);
    await loadCard();
  }

  /* ── прочее ─────────────────────────────────────────────── */

  function screenBlocked(title, text) {
    paint('<div class="rt-pane rt-mid"><h2 class="rt-h">' + esc(title) +
          '</h2><p class="rt-lead">' + esc(text) + "</p></div>");
  }

  let tt = null;
  function toast(text) {
    let el = document.getElementById("rt-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "rt-toast";
      el.className = "rt-toast";
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.hidden = false;
    clearTimeout(tt);
    tt = setTimeout(() => (el.hidden = true), 2200);
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function refresh() {
    const r = await api("/state");
    if (!r.ok) {
      screenBlocked("Раздел недоступен",
        (r.data && r.data.error) || "Попробуйте позже.");
      return;
    }
    st = r.data;
    switch (st.screen) {
      case "consent":  return screenConsent();
      case "register": return screenRegister();
      case "photo":    return screenPhoto(false);
      case "reupload": return screenPhoto(true);
      case "feed":     return screenFeed();
      case "hidden":   return screenBlocked("Анкета скрыта",
        "Ваша анкета временно скрыта модератором. После снятия ограничения потребуется новое фото.");
      case "banned":   return screenBlocked("Доступ закрыт",
        "Раздел недоступен из-за нарушения правил. Обжаловать — " + st.support + ".");
      default:         return screenRegister();
    }
  }

  /* Раздел грузим лениво: только когда на вкладку реально зашли. */
  function watch() {
    document.querySelectorAll('[data-screen="s-rate"]').forEach((tab) => {
      tab.addEventListener("click", () => {
        if (started) return;
        started = true;
        refresh();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }
})();
