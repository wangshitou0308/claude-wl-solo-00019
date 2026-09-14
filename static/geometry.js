/* 证据框几何换算（纯函数，无 DOM 依赖，可在浏览器与 Node 测试中共用）。
 *
 * 阅读区中图像以 translate(tx,ty) scale(s) 显示，证据框按【原图比例坐标】
 * 保存（x/y/w/h 均为 0~1，即相对原图宽高的比例），因此与缩放、平移无关。
 */
'use strict';

(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.EvidenceGeom = factory();
})(typeof self !== 'undefined' ? self : this, () => {
  const EPS = 1e-6;

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  /** 阅读区视口坐标（SVG 客户坐标）→ 原图像素坐标。 */
  function toImagePoint(clientX, clientY, view, imgW, imgH) {
    return {
      x: (clientX - view.tx) / view.scale,
      y: (clientY - view.ty) / view.scale,
      imgW, imgH
    };
  }

  /** 拖框的两个视口角点 → 规范化后的原图比例矩形（允许反向拖出）。 */
  function proportionRect(a, b, view, imgW, imgH) {
    const p1 = toImagePoint(a.x, a.y, view, imgW, imgH);
    const p2 = toImagePoint(b.x, b.y, view, imgW, imgH);
    const x0 = Math.min(p1.x, p2.x), y0 = Math.min(p1.y, p2.y);
    const x1 = Math.max(p1.x, p2.x), y1 = Math.max(p1.y, p2.y);
    const r6 = v => Math.round(v * 1e6) / 1e6;
    return {
      x: r6(x0 / imgW), y: r6(y0 / imgH),
      w: r6((x1 - x0) / imgW), h: r6((y1 - y0) / imgH)
    };
  }

  /** 保存前校验：字段非空、坐标为有限数、宽高为正、不越界。返回错误原因或 null。 */
  function validateBox(box, value) {
    if (value == null || String(value).trim() === '') {
      return '字段为空：请先在该字段填写线索并保存后再框选原文';
    }
    if (!box || typeof box !== 'object') return '坐标格式不正确：缺少矩形数据';
    for (const k of ['x', 'y', 'w', 'h']) {
      const v = box[k];
      if (typeof v !== 'number' || !Number.isFinite(v)) {
        return `坐标 ${k} 必须是数字`;
      }
    }
    if (box.w <= 0 || box.h <= 0) return '矩形宽高必须为正数';
    if (box.x < -EPS || box.y < -EPS ||
        box.x + box.w > 1 + EPS || box.y + box.h > 1 + EPS) {
      return '坐标越界：矩形必须完全落在原图范围内（0~1）';
    }
    return null;
  }

  /** 原图比例坐标 → 当前视图下的视口像素矩形（重画证据层用）。 */
  function toScreenRect(box, view, imgW, imgH) {
    return {
      x: view.tx + box.x * imgW * view.scale,
      y: view.ty + box.y * imgH * view.scale,
      w: box.w * imgW * view.scale,
      h: box.h * imgH * view.scale
    };
  }

  /** 计算使比例矩形居中显示所需的视图参数（scale 钳在 0.05~8）。 */
  function centerView(box, imgW, imgH, vpW, vpH, maxScale = 3) {
    const pad = 0.18;
    const sx = vpW / (box.w * imgW * (1 + 2 * pad));
    const sy = vpH / (box.h * imgH * (1 + 2 * pad));
    const scale = clamp(Math.min(sx, sy), 0.05, maxScale);
    const cx = (box.x + box.w / 2) * imgW;
    const cy = (box.y + box.h / 2) * imgH;
    return { scale, tx: vpW / 2 - cx * scale, ty: vpH / 2 - cy * scale };
  }

  return { toImagePoint, proportionRect, validateBox, toScreenRect, centerView, clamp };
});
