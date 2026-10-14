const cache = new Map();

export function productArt(kind) {
  if (cache.has(kind)) return cache.get(kind);
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 480;
  const context = canvas.getContext("2d");
  const dark = "#252a2b";
  const metal = "#545b5b";
  const mint = "#a5d6c1";
  const box = (left, top, width, height, radius, fill = dark) => {
    context.beginPath();
    context.roundRect(left, top, width, height, radius);
    context.fillStyle = fill;
    context.fill();
  };
  const ellipse = (left, top, width, height, fill) => {
    context.beginPath();
    context.ellipse(left + width / 2, top + height / 2, width / 2, height / 2, 0, 0, Math.PI * 2);
    context.fillStyle = fill;
    context.fill();
  };
  const line = (startX, startY, endX, endY, width = 16, color = dark) => {
    context.beginPath();
    context.moveTo(startX, startY);
    context.lineTo(endX, endY);
    context.lineWidth = width;
    context.lineCap = "round";
    context.strokeStyle = color;
    context.stroke();
  };
  const bodyGradient = context.createLinearGradient(270, 0, 380, 0);
  bodyGradient.addColorStop(0, "#343a3a");
  bodyGradient.addColorStop(0.45, "#666e6d");
  bodyGradient.addColorStop(1, "#252a2b");
  ellipse(155, 397, 340, 24, "rgba(25,35,30,.07)");
  if (kind === "microphone") {
    box(268, 84, 104, 220, 46, bodyGradient);
    box(278, 94, 84, 119, 37, "#343b3b");
    for (let stripe = 107; stripe < 200; stripe += 8) line(287, stripe, 354, stripe, 2, "#747d79");
    for (let stripe = 287; stripe < 355; stripe += 8) line(stripe, 107, stripe, 200, 1, "#59625f");
    ellipse(310, 231, 19, 19, mint);
    context.beginPath();
    context.ellipse(320, 222, 77, 101, 0, 0, Math.PI);
    context.lineWidth = 14;
    context.strokeStyle = dark;
    context.stroke();
    box(310, 318, 20, 72, 6);
    ellipse(245, 376, 150, 27, dark);
    ellipse(251, 376, 138, 12, metal);
  } else if (kind === "lavalier") {
    context.beginPath();
    context.moveTo(312, 218);
    context.bezierCurveTo(205, 270, 418, 275, 386, 341);
    context.bezierCurveTo(360, 405, 236, 345, 221, 368);
    context.lineWidth = 6;
    context.strokeStyle = "#414848";
    context.stroke();
    box(276, 132, 66, 103, 22, bodyGradient);
    ellipse(276, 103, 66, 82, metal);
    ellipse(281, 106, 56, 74, dark);
    box(337, 163, 26, 62, 6, "#89918e");
    box(192, 354, 45, 38, 7);
    box(175, 367, 24, 11, 2, "#89918e");
  } else if (kind === "wireless") {
    for (const [left, top] of [[176, 165], [345, 144]]) {
      box(left, top, 113, 173, 20);
      box(left + 9, top + 9, 94, 153, 16, bodyGradient);
      ellipse(left + 30, top + 39, 51, 51, mint);
      ellipse(left + 30, top + 51, 51, 25, dark);
      box(left + 31, top + 119, 51, 12, 5);
      box(left + 37, top - 19, 37, 21, 3, "#89918e");
    }
  } else if (kind === "lighting") {
    ellipse(213, 62, 216, 216, dark);
    ellipse(225, 74, 192, 192, "#fff");
    ellipse(239, 88, 164, 164, mint);
    ellipse(258, 107, 126, 126, dark);
    box(310, 269, 20, 113, 6);
    line(320, 340, 264, 404);
    line(320, 340, 376, 404);
    line(320, 340, 320, 409);
  } else if (kind === "tripod") {
    box(305, 114, 28, 210, 7, bodyGradient);
    box(267, 91, 104, 32, 7, metal);
    box(283, 58, 72, 29, 7);
    line(319, 276, 211, 404);
    line(319, 276, 427, 404);
    line(319, 276, 319, 412);
    line(214, 395, 284, 300, 2, "#89918e");
    line(423, 395, 357, 300, 2, "#89918e");
    box(302, 213, 34, 30, 5, mint);
    line(363, 103, 426, 145, 12);
  } else {
    context.beginPath();
    context.ellipse(320, 210, 119, 130, 0, Math.PI, 2 * Math.PI);
    context.lineWidth = 28;
    context.strokeStyle = dark;
    context.stroke();
    for (const left of [177, 379]) {
      box(left, 215, 84, 139, 30);
      box(left + 13, 222, 59, 119, 25, bodyGradient);
      box(left + 22, 234, 41, 94, 18, mint);
    }
  }
  const image = canvas.toDataURL("image/png");
  cache.set(kind, image);
  return image;
}