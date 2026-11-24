# -*- coding: utf-8 -*-
"""并排对比片：左原片、中重做、右「去掉原片素材」（素材换成色块后重跑出来的那条）。给用户和剪辑师看的。
要重新编码，可以放另一台电脑跑。

用法：python3 -B sanlian.py 原片 重做片 输出.mp4 [--zheng 证明片] [--yuan-qi 重做片第 0 帧对应的原片帧号，默认 0] [--biaoti 中间那栏的标题，默认「重做」]
  不给 --zheng 就只拼左右两栏。声音用原片对应那一段的。长度以重做片为准。
"""
import argparse, os, subprocess
from PIL import Image, ImageDraw
import lib

ap = argparse.ArgumentParser()
ap.add_argument('yuan'); ap.add_argument('chong'); ap.add_argument('out')
ap.add_argument('--zheng'); ap.add_argument('--yuan-qi', type=int, default=0); ap.add_argument('--biaoti', default='重做')
a = ap.parse_args()
v, vr = lib.info(a.yuan), lib.info(a.chong)
W = 540; H = int(round(W * v['h'] / v['w'] / 2)) * 2
cols = [('原片', ''), (a.biaoti, '')] + ([('去掉原片素材', '灰格是照片，蓝格是视频，其余都是程序画的')] if a.zheng else [])
bar = Image.new('RGB', (W * len(cols), 84), (20, 20, 20)); d = ImageDraw.Draw(bar)
for i, (t, s) in enumerate(cols):
    d.text((i * W + 16, 8 if s else 22), t, fill=(255, 255, 255), font=lib.font(34))
    if s:
        d.text((i * W + 16, 52), s, fill=(170, 170, 170), font=lib.font(19))
png = a.out + '.标题条.png'; os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True); bar.save(png)
ins = ['-i', a.yuan, '-i', a.chong] + (['-i', a.zheng] if a.zheng else []) + ['-loop', '1', '-framerate', v['r'], '-i', png]
k = len(cols)
# 原片按帧号掐头（从头数，慢一点但一帧不差），声音按同一个时间掐
fc = f'[0:v]trim=start_frame={a.yuan_qi},setpts=PTS-STARTPTS,scale={W}:{H}[v0];[0:a]atrim=start={a.yuan_qi / v["fps"]:.5f},asetpts=PTS-STARTPTS[au];' if v['audio'] else f'[0:v]trim=start_frame={a.yuan_qi},setpts=PTS-STARTPTS,scale={W}:{H}[v0];'
fc += ''.join(f'[{i}:v]scale={W}:{H}[v{i}];' for i in range(1, k)) + ''.join(f'[v{i}]' for i in range(k)) + f'hstack={k}[v];[{k}:v][v]vstack[o]'
cmd = ['ffmpeg', '-loglevel', 'error', '-y'] + ins + ['-filter_complex', fc, '-map', '[o]'] + (['-map', '[au]'] if v['audio'] else []) + ['-t', f'{vr["dur"]:.4f}',
       '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', a.out]
subprocess.run(cmd, check=True); os.remove(png)
print('拼好了', a.out, f'{W * k}×{H + 84}，{vr["dur"]:.1f} 秒')
