# -*- coding: utf-8 -*-
"""逐帧画面条：把一小段连续的帧排成一张图，每格标帧号和秒数。看一样东西怎么出来、怎么走，用它。
只解要看的那几十帧，轻，在这台电脑上跑就行。

用法：python3 -B huamiantiao.py 视频 输出.jpg 起帧 取几格 [隔几帧，默认 1] [--kuang x0,y0,x1,y1] [--ge 每格宽，默认 216] [--lie 每行几格，默认 10]
  --kuang 只看画面的一块，四个数是 0 到 1 的比例（左、上、右、下）。看小字、小箭头时用，裁完再放大看得清。
例：python3 -B huamiantiao.py 原片/第一条.mp4 画面条/屏12_进场.jpg 627 30
    python3 -B huamiantiao.py 原片/第一条.mp4 画面条/屏12_左上的字.jpg 627 20 1 --kuang 0,0,0.6,0.3 --ge 320
"""
import argparse, os
from PIL import Image, ImageDraw
import lib

ap = argparse.ArgumentParser()
ap.add_argument('video'); ap.add_argument('out'); ap.add_argument('start', type=int); ap.add_argument('count', type=int)
ap.add_argument('step', type=int, nargs='?', default=1)
ap.add_argument('--kuang'); ap.add_argument('--ge', type=int, default=216); ap.add_argument('--lie', type=int, default=10)
a = ap.parse_args()
v = lib.info(a.video)
crop = tuple(float(x) for x in a.kuang.split(',')) if a.kuang else None
ar = v['h'] / v['w'] if not crop else (v['h'] * (crop[3] - crop[1])) / (v['w'] * (crop[2] - crop[0]))
w = a.ge; h = int(round(w * ar / 2)) * 2
fr = lib.rgb_frames_at(a.video, a.start, a.count * a.step, w, h, v['fps'], crop)[::a.step]
if len(fr) == 0:
    lib.die('一帧也没读到，看看起帧是不是超过了片子的总帧数（%d）' % v['n'])
rows = (len(fr) + a.lie - 1) // a.lie
f = lib.font(max(14, w // 12))
im = Image.new('RGB', (a.lie * (w + 4), rows * (h + 28)), (40, 40, 40)); d = ImageDraw.Draw(im)
for i, x in enumerate(fr):
    r, c = divmod(i, a.lie); n = a.start + i * a.step
    im.paste(Image.fromarray(x), (c * (w + 4), r * (h + 28) + 26))
    d.text((c * (w + 4) + 4, r * (h + 28) + 2), f'{n}  {n / v["fps"]:.2f}秒', fill=(255, 255, 0), font=f)
os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
im.save(a.out, quality=88)
print(a.out, im.size, f'第 {a.start} 到 {a.start + (len(fr) - 1) * a.step} 帧，共 {len(fr)} 格')
