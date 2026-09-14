/* 证据框几何换算的前端测试（Node 入口）：node test_geometry.js
 *
 * 判定依据：以“相同图像位置”为准——在给定缩放/平移视图的同一处拖框，
 * 保存的原图比例坐标与手工按逆变换算出的位置一致；反向拖、越界、宽高非正
 * 均拒绝；居中视图再换算回应落回同一图像位置。
 */
'use strict';
const assert = require('assert');
const G = require('./static/geometry.js');

const IMG = { w: 800, h: 1200 };

function test_roundtrip_same_image_position() {
  // 视图：放大 2 倍后向右下平移
  const view = { scale: 2, tx: 130, ty: -240 };
  // 原图上 (100,200)-(300,440) 的矩形，对应视口坐标：
  const a = { x: 130 + 100 * 2, y: -240 + 200 * 2 };   // (330,160)
  const b = { x: 130 + 300 * 2, y: -240 + 440 * 2 };   // (730,640)
  const box = G.proportionRect(a, b, view, IMG.w, IMG.h);
  assert.deepStrictEqual(box, { x: 0.125, y: 0.166667, w: 0.25, h: 0.2 });

  // 重画：比例框经当前视图换算，必须落回拖框时的同一视口（即同一图像位置）。
  // 坐标按 6 位小数保存，容差按该舍入精度给出（亚像素级）。
  const tol = 1e-6 * Math.max(IMG.w, IMG.h) * view.scale * 1.01;
  const screen = G.toScreenRect(box, view, IMG.w, IMG.h);
  assert.ok(Math.abs(screen.x - a.x) <= tol);
  assert.ok(Math.abs(screen.y - a.y) <= tol);
  assert.ok(Math.abs(screen.w - (b.x - a.x)) <= tol);
  assert.ok(Math.abs(screen.h - (b.y - a.y)) <= tol);
  console.log('✓ 缩放/平移后拖框换算为原图比例，重画回到相同图像位置');
}

function test_reverse_drag_and_pan() {
  // 缩小 0.6 倍、向左上平移；从右下往左上反向拖
  const view = { scale: 0.6, tx: -50, ty: 70 };
  const a = { x: -50 + 500 * 0.6, y: 70 + 900 * 0.6 };  // 原图 (500,900)
  const b = { x: -50 + 100 * 0.6, y: 70 + 100 * 0.6 };  // 原图 (100,100)
  const box = G.proportionRect(a, b, view, IMG.w, IMG.h);
  assert.deepStrictEqual(box, { x: 0.125, y: 0.083333, w: 0.5, h: 0.666667 });
  // 与正向拖（交换起终点）结果相同
  const box2 = G.proportionRect(b, a, view, IMG.w, IMG.h);
  assert.deepStrictEqual(box2, box);
  console.log('✓ 反向拖框规范化且与正向拖结果一致');
}

function test_validation_rejects() {
  assert.strictEqual(G.validateBox({ x: 0, y: 0, w: 1, h: 1 }, '段尾短句'), null);
  assert.ok(G.validateBox({ x: 0, y: 0, w: 1, h: 1 }, ''));         // 字段为空
  assert.ok(G.validateBox({ x: 0, y: 0, w: 1, h: 1 }, '   '));
  assert.ok(G.validateBox({ x: 0.1, y: 0.1, w: 0, h: 0.2 }, 'x')); // 宽非正
  assert.ok(G.validateBox({ x: 0.1, y: 0.1, w: 0.2, h: -1 }, 'x'));
  assert.ok(G.validateBox({ x: 1.05, y: 0, w: 0.1, h: 0.1 }, 'x'));   // x 越界
  assert.ok(G.validateBox({ x: 0.9, y: 0.9, w: 0.2, h: 0.2 }, 'x')); // 右下角越界
  assert.ok(G.validateBox({ x: 0, y: -0.2, w: 0.2, h: 0.2 }, 'x'));
  assert.ok(G.validateBox({ x: '0', y: 0, w: 0.2, h: 0.2 }, 'x'));   // 非数字
  assert.ok(G.validateBox(null, 'x'));
  console.log('✓ 字段为空 / 越界 / 宽高非正 / 非数字均被拒绝');
}

function test_center_view_keeps_position() {
  const box = { x: 0.2, y: 0.25, w: 0.1, h: 0.2 };
  const vp = { w: 600, h: 800 };
  const view = G.centerView(box, IMG.w, IMG.h, vp.w, vp.h);
  // 框中心（原图 (200,420)）应居中于视口
  const cxScreen = view.tx + (box.x + box.w / 2) * IMG.w * view.scale;
  const cyScreen = view.ty + (box.y + box.h / 2) * IMG.h * view.scale;
  assert.ok(Math.abs(cxScreen - vp.w / 2) < 1e-9);
  assert.ok(Math.abs(cyScreen - vp.h / 2) < 1e-9);
  // 新视图下重画仍指向同一图像矩形
  const screen = G.toScreenRect(box, view, IMG.w, IMG.h);
  assert.ok(screen.w > 0 && screen.h > 0);
  assert.ok(screen.w <= vp.w && screen.h <= vp.h);
  console.log('✓ 居中视图把证据框中心放到视口中央且位置不变');
}

const tests = [test_roundtrip_same_image_position, test_reverse_drag_and_pan,
  test_validation_rejects, test_center_view_keeps_position];
for (const t of tests) t();
console.log('全部前端几何测试通过');
