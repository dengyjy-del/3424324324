/* ============================================================
   Карточки «поделиться» — как PNL на биржах.

   Рисуются на canvas в 900×1200 (3:4) и отдаются картинкой PNG.
   Три темы: баллы, замеры и обе группы сразу.
   ============================================================ */

const W = 900;
const H = 1200;

// Только строковые имена семейств: парсер шрифтов в canvas отвергает всю
// строку целиком, если встречает ключевые слова вроде -apple-system или
// system-ui, и молча откатывается на дефолтные 10px.
const FONT = '"SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif';

export const THEMES = [
  { id: "scores", label: "Баллы" },
  { id: "metrics", label: "Замеры" },
  { id: "full", label: "Всё сразу" },
];

/* ── примитивы ───────────────────────────────────────────── */

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Мягкое световое пятно — тот же приём, что и в интерфейсе приложения. */
function aura(ctx, x, y, radius, color, alpha) {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
  gradient.addColorStop(0, color.replace("ALPHA", alpha));
  gradient.addColorStop(1, color.replace("ALPHA", "0"));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, W, H);
}

function grain(ctx) {
  const image = ctx.getImageData(0, 0, W, H);
  const { data } = image;
  for (let i = 0; i < data.length; i += 4) {
    const noise = (Math.random() - 0.5) * 11;
    data[i] += noise;
    data[i + 1] += noise;
    data[i + 2] += noise;
  }
  ctx.putImageData(image, 0, 0);
}

function text(ctx, value, x, y, { size = 40, weight = 500, color = "#fff", align = "left", spacing = 0 } = {}) {
  ctx.font = `${weight} ${size}px ${FONT}`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = "alphabetic";

  if (!spacing) {
    ctx.fillText(value, x, y);
    return;
  }

  // Ручной трекинг: letterSpacing поддержан не везде
  const chars = [...value];
  const total = chars.reduce((sum, c) => sum + ctx.measureText(c).width + spacing, -spacing);
  let cursor = align === "center" ? x - total / 2 : align === "right" ? x - total : x;
  ctx.textAlign = "left";
  for (const char of chars) {
    ctx.fillText(char, cursor, y);
    cursor += ctx.measureText(char).width + spacing;
  }
}

function divider(ctx, y, inset = 70) {
  ctx.strokeStyle = "rgba(255,255,255,0.13)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(inset, y);
  ctx.lineTo(W - inset, y);
  ctx.stroke();
}

/* ── фоны ────────────────────────────────────────────────── */

function paintBackground(ctx, theme) {
  if (theme === "scores") {
    const base = ctx.createLinearGradient(0, 0, W, H);
    base.addColorStop(0, "#1a1140");
    base.addColorStop(0.55, "#3a1a52");
    base.addColorStop(1, "#0d0a1c");
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, W, H);
    aura(ctx, W * 0.18, H * 0.16, 620, "rgba(91,75,255,ALPHA)", "0.75");
    aura(ctx, W * 0.88, H * 0.42, 560, "rgba(255,61,113,ALPHA)", "0.6");
  } else if (theme === "metrics") {
    ctx.fillStyle = "#05070c";
    ctx.fillRect(0, 0, W, H);
    aura(ctx, W * 0.5, H * 0.12, 700, "rgba(0,224,198,ALPHA)", "0.32");
    aura(ctx, W * 0.1, H * 0.85, 520, "rgba(91,75,255,ALPHA)", "0.4");

    // Техническая сетка — отсылка к замерам
    ctx.strokeStyle = "rgba(0,224,198,0.07)";
    ctx.lineWidth = 1;
    for (let x = 0; x <= W; x += 45) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, H);
      ctx.stroke();
    }
    for (let y = 0; y <= H; y += 45) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
      ctx.stroke();
    }
  } else {
    ctx.fillStyle = "#07080d";
    ctx.fillRect(0, 0, W, H);
    aura(ctx, W * 0.82, H * 0.14, 600, "rgba(255,61,113,ALPHA)", "0.5");
    aura(ctx, W * 0.14, H * 0.55, 640, "rgba(91,75,255,ALPHA)", "0.62");
    aura(ctx, W * 0.6, H * 0.92, 480, "rgba(0,224,198,ALPHA)", "0.34");
  }
}

/* ── элементы ────────────────────────────────────────────── */

function header(ctx, brand) {
  text(ctx, brand.toUpperCase(), 70, 108, {
    size: 34, weight: 700, spacing: 5, color: "rgba(255,255,255,0.95)",
  });
  text(ctx, "АНАЛИЗ ЛИЦЕВОЙ ЭСТЕТИКИ", 70, 146, {
    size: 19, weight: 600, spacing: 3.4, color: "rgba(255,255,255,0.42)",
  });
}

function footer(ctx, username, refCode) {
  divider(ctx, H - 152);
  text(ctx, username || "", 70, H - 98, { size: 33, weight: 700 });
  text(ctx, "проверь себя", W - 70, H - 98, {
    size: 25, weight: 500, color: "rgba(255,255,255,0.45)", align: "right",
  });

  if (!refCode) return;

  // Код приглашения: тот, кто увидит карточку, сможет ввести его и
  // получить стартовые XP — а автор карточки получит награду.
  text(ctx, "код приглашения", 70, H - 52, {
    size: 19, weight: 600, spacing: 2.4, color: "rgba(255,255,255,0.4)",
  });
  text(ctx, refCode, W - 70, H - 48, {
    size: 30, weight: 700, spacing: 3, align: "right", color: "rgba(255,255,255,0.92)",
  });
}

function scoreRing(ctx, cx, cy, radius, value) {
  ctx.lineWidth = 20;
  ctx.lineCap = "round";

  ctx.strokeStyle = "rgba(255,255,255,0.1)";
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.stroke();

  const gradient = ctx.createLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius);
  gradient.addColorStop(0, "#5b4bff");
  gradient.addColorStop(0.55, "#ff3d71");
  gradient.addColorStop(1, "#00e0c6");
  ctx.strokeStyle = gradient;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * value) / 10);
  ctx.stroke();
}

function rows(ctx, items, startY, gap, { valueColor = "#fff" } = {}) {
  items.forEach((item, index) => {
    const y = startY + index * gap;
    text(ctx, item.title, 70, y, { size: 30, weight: 500, color: "rgba(255,255,255,0.62)" });
    text(ctx, item.value, W - 70, y, { size: 34, weight: 700, align: "right", color: valueColor });
    if (index < items.length - 1) divider(ctx, y + gap * 0.36);
  });
}

/* ── темы ────────────────────────────────────────────────── */

function drawScores(ctx, data) {
  header(ctx, data.brand);

  text(ctx, "ОБЩИЙ БАЛЛ", W / 2, 268, {
    size: 21, weight: 600, spacing: 3.6, align: "center", color: "rgba(255,255,255,0.5)",
  });

  text(ctx, data.overall.toFixed(1), W / 2, 434, {
    size: 168, weight: 700, align: "center",
  });
  text(ctx, "из 10", W / 2, 482, {
    size: 26, weight: 500, align: "center", color: "rgba(255,255,255,0.45)",
  });

  const chip = `${data.tier.emoji}  ${data.tier.code} · ${data.tier.title}`;
  ctx.font = `600 30px ${FONT}`;
  const chipWidth = ctx.measureText(chip).width + 62;
  ctx.fillStyle = "rgba(255,255,255,0.11)";
  roundRect(ctx, (W - chipWidth) / 2, 520, chipWidth, 72, 36);
  ctx.fill();
  text(ctx, chip, W / 2, 566, { size: 30, weight: 600, align: "center" });

  text(ctx, `Выше, чем у ${data.percentile}% пользователей`, W / 2, 646, {
    size: 26, weight: 500, align: "center", color: "rgba(255,255,255,0.55)",
  });

  divider(ctx, 700);
  rows(ctx, data.topScores.map((s) => ({ title: `${s.emoji}  ${s.title}`, value: s.value.toFixed(1) })), 754, 82);

  footer(ctx, data.username, data.refCode);
}

function drawMetrics(ctx, data) {
  header(ctx, data.brand);

  text(ctx, "ЗАМЕРЫ ЛИЦА", 70, 246, {
    size: 46, weight: 700, spacing: 1.5,
  });
  text(ctx, "478 точек · вычислено на устройстве", 70, 288, {
    size: 23, weight: 500, color: "rgba(0,224,198,0.75)",
  });

  divider(ctx, 336);
  rows(ctx, data.metrics.map((m) => ({ title: `${m.emoji}  ${m.title}`, value: m.value })), 400, 86, {
    valueColor: "#00e0c6",
  });

  const boxY = H - 356;
  ctx.fillStyle = "rgba(255,255,255,0.06)";
  roundRect(ctx, 70, boxY, W - 140, 132, 28);
  ctx.fill();
  text(ctx, "ОБЩИЙ БАЛЛ", 106, boxY + 52, {
    size: 19, weight: 600, spacing: 3, color: "rgba(255,255,255,0.45)",
  });
  text(ctx, `${data.tier.code} · ${data.tier.title}`, 106, boxY + 98, {
    size: 27, weight: 600, color: "rgba(255,255,255,0.8)",
  });
  text(ctx, data.overall.toFixed(1), W - 106, boxY + 92, {
    size: 72, weight: 700, align: "right",
  });

  footer(ctx, data.username, data.refCode);
}

function drawFull(ctx, data) {
  header(ctx, data.brand);

  scoreRing(ctx, W / 2, 388, 128, data.overall);
  text(ctx, data.overall.toFixed(1), W / 2, 408, { size: 92, weight: 700, align: "center" });
  text(ctx, "из 10", W / 2, 446, {
    size: 22, weight: 500, align: "center", color: "rgba(255,255,255,0.45)",
  });

  text(ctx, `${data.tier.emoji}  ${data.tier.code} · ${data.tier.title}`, W / 2, 570, {
    size: 32, weight: 620, align: "center",
  });

  divider(ctx, 622);
  text(ctx, "СИЛЬНЫЕ СТОРОНЫ", 70, 678, {
    size: 19, weight: 600, spacing: 3, color: "rgba(255,255,255,0.42)",
  });
  rows(ctx, data.topScores.slice(0, 2).map((s) => ({ title: `${s.emoji}  ${s.title}`, value: s.value.toFixed(1) })), 736, 78);

  divider(ctx, 862);
  text(ctx, "ЗАМЕРЫ", 70, 918, {
    size: 19, weight: 600, spacing: 3, color: "rgba(0,224,198,0.6)",
  });
  rows(ctx, data.metrics.slice(0, 2).map((m) => ({ title: `${m.emoji}  ${m.title}`, value: m.value })), 966, 74, {
    valueColor: "#00e0c6",
  });

  footer(ctx, data.username, data.refCode);
}

const PAINTERS = { scores: drawScores, metrics: drawMetrics, full: drawFull };

/* ── публичное ───────────────────────────────────────────── */

/** Рисует карточку в переданный canvas. */
export function renderCard(canvas, themeId, data) {
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");

  paintBackground(ctx, themeId);
  (PAINTERS[themeId] || drawFull)(ctx, data);
  grain(ctx);
  return canvas;
}

/** Готовит данные отчёта к отрисовке. */
export function cardData(report, brand, username, refCode) {
  const sorted = [...report.scores].sort((a, b) => b.value - a.value);
  return {
    brand: brand || "LOOKSCORE",
    username: username || "",
    refCode: refCode || "",
    overall: report.overall,
    percentile: report.percentile,
    tier: report.tier,
    topScores: sorted.slice(0, 4),
    metrics: (report.measurements || []).slice(0, 6),
  };
}

/** Карточка «Замеры» пуста, если распознавание не сработало. */
export function availableThemes(report) {
  const hasMetrics = (report.measurements || []).length > 0;
  return THEMES.filter((theme) => hasMetrics || theme.id !== "metrics");
}

export function toBlob(canvas) {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}
