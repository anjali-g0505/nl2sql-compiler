// Turn a rendered chart (an inline <svg>) into a PNG, so it can be copied or saved.
// The SVG is serialized with the fonts and colours baked in as inline styles are already
// applied by Recharts, drawn onto a canvas at 2x for a sharp image, and read back as a blob.

import { token } from "./theme";

const SCALE = 2;

export async function chartToPng(svg: SVGSVGElement | null): Promise<Blob | null> {
  if (!svg) return null;
  const { width, height } = svg.getBoundingClientRect();
  if (!width || !height) return null;

  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  // Recharts sizes text through CSS on the page, which a standalone SVG won't have
  clone.style.fontFamily = getComputedStyle(svg).fontFamily;

  const source = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(new Blob([source], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const image = await load(url);
    const canvas = document.createElement("canvas");
    canvas.width = width * SCALE;
    canvas.height = height * SCALE;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.fillStyle = token("--export-bg");             // PNGs with alpha paste badly
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/png"));
  } finally {
    URL.revokeObjectURL(url);
  }
}

function load(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = url;
  });
}

/** Rows as tab-separated text: pastes straight into Excel, Sheets or a message. */
export function rowsAsText(columns: string[], headers: string[], rows: Record<string, unknown>[]): string {
  const lines = [headers.join("\t")];
  for (const row of rows) {
    lines.push(columns.map((c) => stringify(row[c])).join("\t"));
  }
  return lines.join("\n");
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}
