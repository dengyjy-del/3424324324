/* ============================================================
   Распознавание лица и замеры геометрии.

   Работает целиком в браузере: модель Face Mesh ставит 478 точек
   на снимок прямо на устройстве. На сервер уходят только числа —
   углы и пропорции. Само фото никуда не отправляется, что заодно
   снимает вопрос приватности.
   ============================================================ */

const VISION_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/" +
  "face_landmarker/float16/1/face_landmarker.task";

// Ключевые точки Face Mesh
const P = {
  eyeLOuter: 33,  eyeLInner: 133, eyeLTop: 159, eyeLBottom: 145,
  eyeROuter: 263, eyeRInner: 362, eyeRTop: 386, eyeRBottom: 374,
  browL: 105, browR: 334,
  cheekL: 234, cheekR: 454,
  jawL: 172, jawR: 397,
  noseL: 129, noseR: 358,
  glabella: 168,   // переносица
  subnasale: 2,    // основание носа
  lipTop: 13,
  forehead: 10,
  chin: 152,
};

// Контур лица — по нему рисуется овал на превью
const FACE_OVAL = [
  10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
  379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
  234, 127, 162, 21, 54, 103, 67, 109,
];

// Пары точек для оценки симметрии (левая, правая)
const MIRROR_PAIRS = [
  [33, 263], [133, 362], [234, 454], [172, 397],
  [129, 358], [61, 291], [105, 334], [58, 288],
];

let landmarker = null;
let loadFailed = false;

/** Загружает модель один раз и держит её в памяти. */
export async function ensureDetector() {
  if (landmarker || loadFailed) return landmarker;

  try {
    const vision = await import(`${VISION_CDN}`);
    const files = await vision.FilesetResolver.forVisionTasks(`${VISION_CDN}/wasm`);

    landmarker = await vision.FaceLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
      runningMode: "IMAGE",
      numFaces: 1,
    });
  } catch (error) {
    // CDN недоступен или устройство не тянет: продукт не должен из-за
    // этого переставать работать, поэтому просто отключаем замеры.
    console.warn("Face Mesh недоступен, работаем без замеров", error);
    loadFailed = true;
  }

  return landmarker;
}

export function detectorReady() {
  return Boolean(landmarker);
}

export function detectorFailed() {
  return loadFailed;
}

/* ── геометрия ───────────────────────────────────────────── */

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

function angleAt(vertex, from, to) {
  const v1 = { x: from.x - vertex.x, y: from.y - vertex.y };
  const v2 = { x: to.x - vertex.x, y: to.y - vertex.y };
  const cos =
    (v1.x * v2.x + v1.y * v2.y) /
    (Math.hypot(v1.x, v1.y) * Math.hypot(v2.x, v2.y) || 1);
  return (Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI;
}

function distanceToAxis(point, top, bottom) {
  const dx = bottom.x - top.x;
  const dy = bottom.y - top.y;
  const length = Math.hypot(dx, dy) || 1;
  return Math.abs(dy * point.x - dx * point.y + bottom.x * top.y - bottom.y * top.x) / length;
}

/**
 * Считает замеры по точкам.
 * Координаты приводятся к пикселям, иначе углы исказит соотношение сторон.
 */
export function computeMetrics(landmarks, width, height) {
  const pt = (index) => ({
    x: landmarks[index].x * width,
    y: landmarks[index].y * height,
  });

  const forehead = pt(P.forehead);
  const chin = pt(P.chin);
  const glabella = pt(P.glabella);
  const subnasale = pt(P.subnasale);
  const cheekL = pt(P.cheekL);
  const cheekR = pt(P.cheekR);

  const faceWidth = dist(cheekL, cheekR);
  const faceHeight = dist(forehead, chin) || 1;

  // Канторальный наклон: положительный — внешний угол глаза выше внутреннего
  const tiltOf = (outer, inner) => {
    const o = pt(outer);
    const i = pt(inner);
    return (Math.atan2(i.y - o.y, Math.abs(o.x - i.x)) * 180) / Math.PI;
  };
  const canthalTilt =
    (tiltOf(P.eyeLOuter, P.eyeLInner) + tiltOf(P.eyeROuter, P.eyeRInner)) / 2;

  // Раскрытие глазной щели
  const aspectOf = (outer, inner, top, bottom) =>
    dist(pt(top), pt(bottom)) / (dist(pt(outer), pt(inner)) || 1);
  const eyeAspect =
    (aspectOf(P.eyeLOuter, P.eyeLInner, P.eyeLTop, P.eyeLBottom) +
      aspectOf(P.eyeROuter, P.eyeRInner, P.eyeRTop, P.eyeRBottom)) / 2;

  // Симметрия: сравниваем расстояния зеркальных точек до оси лица
  let symmetrySum = 0;
  for (const [left, right] of MIRROR_PAIRS) {
    const dl = distanceToAxis(pt(left), forehead, chin);
    const dr = distanceToAxis(pt(right), forehead, chin);
    symmetrySum += 1 - Math.abs(dl - dr) / (dl + dr || 1);
  }
  const symmetry = symmetrySum / MIRROR_PAIRS.length;

  // Трети лица: лоб → переносица → основание носа → подбородок
  const thirds = [
    dist(forehead, glabella),
    dist(glabella, subnasale),
    dist(subnasale, chin),
  ];
  const thirdsTotal = thirds.reduce((a, b) => a + b, 0) || 1;
  const spread = (Math.max(...thirds) - Math.min(...thirds)) / thirdsTotal;
  const thirdsBalance = Math.max(0, 1 - spread * 2.6);

  // Гониальный угол — в точке угла челюсти
  const gonialAngle =
    (angleAt(pt(P.jawL), cheekL, chin) + angleAt(pt(P.jawR), cheekR, chin)) / 2;

  const upperFace = dist(
    { x: (pt(P.browL).x + pt(P.browR).x) / 2, y: (pt(P.browL).y + pt(P.browR).y) / 2 },
    pt(P.lipTop)
  ) || 1;

  // Замеры, которые в луксмаксинге считают отдельно. В модель они пока не
  // входят — им нужна своя разметка, — но копятся с каждым отчётом и войдут
  // при следующем переобучении.
  const mouthWidth = dist(pt(61), pt(291));
  const eyeWidthAvg =
    (dist(pt(P.eyeLOuter), pt(P.eyeLInner)) + dist(pt(P.eyeROuter), pt(P.eyeRInner))) / 2;
  const innerGap = dist(pt(P.eyeLInner), pt(P.eyeRInner));
  const lowerThirdLen = dist(subnasale, chin) || 1;

  // Глубина. Face Mesh отдаёт z для каждой точки, и рельеф оказался
  // сильнее геометрии: у плоского лица подбородок и нос почти не
  // выступают, тогда как ширина лица одинакова и у полного, и у
  // человека с выраженными скулами.
  const zScale = faceWidth || 1;
  const z = (index) => (landmarks[index].z * width) / zScale;
  const oval = FACE_OVAL.map(z);
  const every4 = [];
  for (let i = 0; i < landmarks.length; i += 4) every4.push(z(i));
  const mean4 = every4.reduce((a, b) => a + b, 0) / every4.length;
  const relief = Math.sqrt(
    every4.reduce((a, b) => a + (b - mean4) ** 2, 0) / every4.length
  );

  // Форма лица: округлость и то, как оно сужается книзу. Эти признаки
  // помогают отличать узкое лицо от широкого и полного.
  const midJaw = dist(pt(58), pt(288));
  const lowJaw = dist(pt(P.jawL), pt(P.jawR));

  // Качество снимка. Отдельная величина, а не часть оценки: она объясняет,
  // почему один и тот же человек получает разные баллы на разных фото.
  // Поворот головы считаем по тому, насколько ось лица смещена от центра
  // между скулами — на повёрнутом лице половины несимметричны.
  const axisMid = (forehead.x + chin.x) / 2;
  const cheekMid = (cheekL.x + cheekR.x) / 2;
  const yaw = Math.abs(axisMid - cheekMid) / (faceWidth || 1);

  // Наклон: насколько линия глаз отклонена от горизонтали
  const roll =
    Math.abs(
      Math.atan2(pt(P.eyeROuter).y - pt(P.eyeLOuter).y,
                 pt(P.eyeROuter).x - pt(P.eyeLOuter).x) * 180 / Math.PI
    );

  return {
    yaw,
    roll,
    face_share: faceWidth / width,   // насколько лицо крупное в кадре

    nose_length: dist(glabella, subnasale) / faceHeight,
    // Носогубный угол здесь не считается: он профильный, а на анфас точки
    // носа и губы лежат почти на одной вертикали и угол вырождается в 180°.
    // Вместо него — форма носа: длина к ширине, она измерима спереди.
    nose_shape:
      dist(glabella, subnasale) / (dist(pt(P.noseL), pt(P.noseR)) || 1),
    philtrum: dist(subnasale, pt(P.lipTop)) / lowerThirdLen,
    lip_height: dist(pt(P.lipTop), pt(17)) / (mouthWidth || 1),
    mouth_width: mouthWidth / (faceWidth || 1),
    eye_spacing: innerGap / (faceWidth || 1),
    esr: innerGap / (eyeWidthAvg || 1),
    forehead_height: dist(forehead, glabella) / faceHeight,

    relief,
    nose_proj: z(1) - z(P.glabella),
    cheek_proj: (z(P.cheekL) + z(P.cheekR)) / 2 - z(1),
    chin_proj: z(P.chin) - z(P.glabella),
    oval_flat: Math.max(...oval) - Math.min(...oval),
    brow_proj: (z(P.browL) + z(P.browR)) / 2 - z(P.glabella),

    face_aspect: faceWidth / faceHeight,
    mid_jaw: midJaw / (faceWidth || 1),
    low_jaw: lowJaw / (faceWidth || 1),
    chin_taper: dist(pt(148), pt(377)) / (faceWidth || 1),
    jaw_drop: dist(chin, pt(P.lipTop)) / (faceWidth || 1),
    cheek_to_jaw: dist(cheekL, pt(P.jawL)) / (faceWidth || 1),
    lower_third: dist(subnasale, chin) / faceHeight,

    canthal_tilt: canthalTilt,
    eye_aspect: eyeAspect,
    symmetry,
    thirds_balance: thirdsBalance,
    fwhr: faceWidth / upperFace,
    jaw_ratio: dist(pt(P.jawL), pt(P.jawR)) / (faceWidth || 1),
    gonial_angle: gonialAngle,
    chin_ratio: dist(subnasale, chin) / faceHeight,
    nose_ratio: dist(pt(P.noseL), pt(P.noseR)) / (faceWidth || 1),
  };
}

/* ── признаки по пикселям ────────────────────────────────── */

/**
 * То, чего не видно в координатах точек.
 *
 * Face Mesh ставит точки бровей по шаблону — даже там, где брови нет
 * вовсе. Поэтому её выраженность определяется не геометрией, а тем,
 * насколько эта область темнее кожи. Так же и с кожей: неровности
 * и покраснения живут в пикселях, а не в разметке.
 */
function pixelFeatures(image, landmarks) {
  const W = 260;
  const canvas = document.createElement("canvas");
  const scale = W / (image.naturalWidth || image.width);
  canvas.width = W;
  canvas.height = Math.round((image.naturalHeight || image.height) * scale);

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

  let data;
  try {
    data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  } catch (_) {
    return null; // изображение из другого источника — пиксели недоступны
  }

  const at = (index) => ({
    x: Math.round(landmarks[index].x * canvas.width),
    y: Math.round(landmarks[index].y * canvas.height),
  });

  /** Средняя яркость, её разброс и краснота в круге вокруг точки. */
  function patch(center, radius) {
    let n = 0, sum = 0, sumSq = 0, red = 0;
    const r = Math.max(2, Math.round(radius));
    for (let dy = -r; dy <= r; dy += 1) {
      for (let dx = -r; dx <= r; dx += 1) {
        if (dx * dx + dy * dy > r * r) continue;
        const x = center.x + dx;
        const y = center.y + dy;
        if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) continue;
        const i = (y * canvas.width + x) * 4;
        const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        sum += lum;
        sumSq += lum * lum;
        red += data[i] - (data[i + 1] + data[i + 2]) / 2;
        n += 1;
      }
    }
    if (!n) return { mean: 0, sd: 0, red: 0 };
    const mean = sum / n;
    return {
      mean,
      sd: Math.sqrt(Math.max(0, sumSq / n - mean * mean)),
      red: red / n,
    };
  }

  const faceW = Math.abs(at(454).x - at(234).x) || 1;
  const unit = faceW * 0.055;

  // Брови против кожи под ними: чем темнее бровь, тем выше контраст
  const browL = patch(at(105), unit);
  const browR = patch(at(334), unit);
  // Сравниваем со лбом НАД бровью: под бровью уже начинается тень века,
  // и она темнее самой брови — контраст выходил нулевым.
  const foreL = patch({ x: at(105).x, y: at(105).y - unit * 2.0 }, unit);
  const foreR = patch({ x: at(334).x, y: at(334).y - unit * 2.0 }, unit);
  const skinRef = (foreL.mean + foreR.mean) / 2 || 1;
  const browMean = (browL.mean + browR.mean) / 2;

  // Кожа: щёки и лоб
  const cheekL = patch({ x: (at(234).x + at(1).x) / 2, y: at(234).y }, unit * 1.3);
  const cheekR = patch({ x: (at(454).x + at(1).x) / 2, y: at(454).y }, unit * 1.3);
  const forehead = patch({ x: at(10).x, y: at(10).y + unit * 2.2 }, unit * 1.3);
  const skinMean = (cheekL.mean + cheekR.mean + forehead.mean) / 3 || 1;

  // Любая из областей может оказаться за границей кадра, и тогда счёт
  // вырождается. Отдавать наружу не-число нельзя: оно молча превращается
  // в null при сериализации и портит обучающую выборку.
  const safe = (value, fallback) =>
    Number.isFinite(value) ? value : fallback;

  return {
    // 0 — брови не отличаются от кожи, 0.5 и выше — выраженные тёмные брови
    brow_contrast: safe(Math.max(0, (skinRef - browMean) / skinRef), 0.1),
    // разброс яркости кожи: неровности, высыпания, тени
    skin_variance: safe(
      ((cheekL.sd + cheekR.sd + forehead.sd) / 3) / skinMean, 0.11
    ),
    // краснота относительно общей яркости: воспаления и раздражение
    skin_redness: safe(
      ((cheekL.red + cheekR.red + forehead.red) / 3) / skinMean, 0.35
    ),
  };
}

/** Находит лицо на изображении. Возвращает null, если лица нет. */
export async function analyseImage(imageElement) {
  const detector = await ensureDetector();
  if (!detector) return null;

  const result = detector.detect(imageElement);
  const faces = result?.faceLandmarks || [];
  if (!faces.length) return null;

  const width = imageElement.naturalWidth || imageElement.width;
  const height = imageElement.naturalHeight || imageElement.height;

  const metrics = computeMetrics(faces[0], width, height);
  const pixels = pixelFeatures(imageElement, faces[0]);

  return {
    landmarks: faces[0],
    metrics: pixels ? { ...metrics, ...pixels } : metrics,
  };
}

/* ── отрисовка сетки по реальным точкам ──────────────────── */

export function drawMesh(canvas, landmarks, box, progress) {
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);

  // Точки приходят в системе исходного изображения, а превью обрезано
  // по object-fit: cover — пересчитываем в координаты холста.
  const at = (index) => ({
    x: box.offsetX + landmarks[index].x * box.drawWidth,
    y: box.offsetY + landmarks[index].y * box.drawHeight,
  });

  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  // 1. Овал лица
  if (progress >= 1) {
    ctx.strokeStyle = "rgba(0, 224, 198, 0.85)";
    ctx.lineWidth = 1.6;
    ctx.setLineDash([4, 5]);
    ctx.beginPath();
    FACE_OVAL.forEach((index, i) => {
      const p = at(index);
      i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
    });
    ctx.closePath();
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 2. Линии глаз — наглядно показывают канторальный наклон
  if (progress >= 2) {
    ctx.strokeStyle = "rgba(91, 75, 255, 0.95)";
    ctx.lineWidth = 2;
    [[P.eyeLOuter, P.eyeLInner], [P.eyeROuter, P.eyeRInner]].forEach(([a, b]) => {
      const p1 = at(a);
      const p2 = at(b);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
    });
  }

  // 3. Линия челюсти и ось симметрии
  if (progress >= 3) {
    ctx.strokeStyle = "rgba(255, 61, 113, 0.9)";
    ctx.lineWidth = 2;
    const jaw = [P.cheekL, P.jawL, P.chin, P.jawR, P.cheekR].map(at);
    ctx.beginPath();
    jaw.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.stroke();

    const top = at(P.forehead);
    const bottom = at(P.chin);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.4)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 6]);
    ctx.beginPath();
    ctx.moveTo(top.x, top.y);
    ctx.lineTo(bottom.x, bottom.y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 4. Горизонтали третей лица
  if (progress >= 4) {
    ctx.strokeStyle = "rgba(0, 224, 198, 0.55)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([5, 5]);
    [P.forehead, P.glabella, P.subnasale, P.chin].forEach((index) => {
      const p = at(index);
      ctx.beginPath();
      ctx.moveTo(box.offsetX + box.drawWidth * 0.06, p.y);
      ctx.lineTo(box.offsetX + box.drawWidth * 0.94, p.y);
      ctx.stroke();
    });
    ctx.setLineDash([]);
  }

  // 5. Узлы сетки
  if (progress >= 5) {
    ctx.fillStyle = "rgba(255, 255, 255, 0.75)";
    for (let i = 0; i < landmarks.length; i += 6) {
      const p = at(i);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 0.9, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}
