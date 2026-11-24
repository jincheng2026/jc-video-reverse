# -*- coding: utf-8 -*-
"""逐帧对照条：上面一排原片、下面一排重做，每格标原片帧号。看验收没过线的段落到底差在哪，用它。
只解几十帧，轻，在这台电脑上跑就行（zsh pao.sh --benji …）。

用法：python3 -B duizhaotiao.py 原片 重做片 输出.jpg 起帧 取几格 [隔几帧，默认 1] [--yuan-qi 重做片第 0 帧对应的原片帧号，默认 0] [--di 第三条片子]
  起帧写原片的帧号。--di 给了就多拼一排（比如上一版，或者把原片换成色块重跑出来的证明片）。
"""
import argparse, os
from PIL import Image, ImageDraw
import lib

ap = argparse.ArgumentParser()
ap.add_argument('yuan'); ap.add_argument('chong'); ap.add_argument('out'); ap.add_argument('start', type=int); ap.add_argument('count', type=int)
ap.add_argument('step', type=int, nargs='?', default=1); ap.add_argument('--yuan-qi', type=int, default=0); ap.add_argument('--di')
a = ap.parse_args()
v = lib.info(a.yuan); w = 216; h = int(round(w * v['h'] / v['w'] / 2)) * 2
if a.start < a.yuan_qi:
    lib.die(f'起帧 {a.start} 比重做片对应的原片起帧 {a.yuan_qi} 还早，重做片里没有这一帧。')
rows = [lib.rgb_frames_at(a.yuan, a.start, a.count * a.step, w, h, v['fps'])[::a.step]]
for p in [a.chong] + ([a.di] if a.di else []):
    rows.append(lib.rgb_frames_at(p, a.start - a.yuan_qi, a.count * a.step, w, h, v['fps'])[::a.step])
k = min(len(r) for r in rows)
if k == 0:
    lib.die('有一条片子在这一段一帧也没读到，检查起帧和 --yuan-qi。')
f = lib.font(18)
im = Image.new('RGB', (k * (w + 4), len(rows) * (h + 4) + 26), (40, 40, 40)); d = ImageDraw.Draw(im)
for i in range(k):
    d.text((i * (w + 4) + 4, 2), str(a.start + i * a.step), fill=(255, 255, 0), font=f)
    for j, r in enumerate(rows):
        im.paste(Image.fromarray(r[i]), (i * (w + 4), 26 + j * (h + 4)))
os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
im.save(a.out, quality=88)
print(a.out, im.size, '从上到下：原片、重做' + ('、第三条' if a.di else ''))
