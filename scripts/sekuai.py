# -*- coding: utf-8 -*-
"""色块替身：给每个素材做一个同名、同尺寸、同长度的色块，用来证明「重做出来的东西是程序画的，不是把原片贴上去的」。
做法是不改重做程序、只换输入：把素材文件夹整个换成替身文件夹，原样再跑一遍重做程序。
重做片里除了灰格和蓝格以外还在出现、还在动的东西，才是程序画的。
（2026-11-22 红白大字风第二条：脸上的标注看着和原片「对得上」，换成色块一跑全没了，才发现标注是连着照片从原片截下来的。）

用法：python3 -B sekuai.py 素材文件夹或单个文件 替身输出文件夹
  图片（jpg、png、webp）→ 灰格子，上面印着文件名。带透明底的抠图保留原来的透明形状。
  视频（mp4、mov、m4v）→ 蓝格子，同宽高、同帧率、同长度，声音照原样带上。
  别的文件原样复制。子文件夹照原样的结构走。
视频替身要重新编码，素材里有长视频时用 pao.sh 可以放另一台电脑跑。
"""
import os, shutil, subprocess, sys
from PIL import Image, ImageDraw
import lib

IMG = ('.jpg', '.jpeg', '.png', '.webp'); VID = ('.mp4', '.mov', '.m4v')


def card(w, h, color, text):
    im = Image.new('RGB', (w, h), color); d = ImageDraw.Draw(im)
    step = max(24, min(w, h) // 8); line = tuple(max(0, c - 40) for c in color)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=line, width=max(1, step // 24))
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=line, width=max(1, step // 24))
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255), width=max(2, step // 12))
    f = lib.font(max(14, min(w // max(len(text), 6), h // 5)))
    box = d.textbbox((0, 0), text, font=f)
    d.text(((w - box[2]) // 2, (h - box[3]) // 2), text, fill=(255, 255, 255), font=f)
    return im


def one(src, dst):
    ext = os.path.splitext(src)[1].lower(); name = os.path.splitext(os.path.basename(src))[0]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if ext in IMG:
        im = Image.open(src); c = card(im.width, im.height, (120, 120, 120), name)
        if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
            c.putalpha(im.convert('RGBA').getchannel('A'))
        elif ext in ('.jpg', '.jpeg'):
            c = c.convert('RGB')
        c.save(dst); return '图'
    if ext in VID:
        v = lib.info(src); png = dst + '.色块.png'
        card(v['w'] // 2 * 2, v['h'] // 2 * 2, (40, 80, 200), name).save(png)
        cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-loop', '1', '-framerate', v['r'], '-i', png, '-i', src,
               '-map', '0:v', '-map', '1:a?', '-t', f'{v["dur"]:.4f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-c:a', 'copy', dst]
        subprocess.run(cmd, check=True); os.remove(png); return '视频'
    shutil.copy2(src, dst); return '原样'


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', help='素材文件夹或单个文件'); ap.add_argument('out', help='替身输出文件夹')
    a = ap.parse_args()
    src, out = a.src, a.out
    if os.path.abspath(out).startswith(os.path.abspath(src) + os.sep) or os.path.abspath(out) == os.path.abspath(src):
        lib.die('替身输出文件夹不能放在素材文件夹里面，也不能就是它。')
    cnt = {}
    if os.path.isfile(src):
        k = one(src, os.path.join(out, os.path.basename(src))); cnt[k] = 1
    else:
        for root, _, files in os.walk(src):
            for fn in sorted(files):
                if fn.startswith('.'):
                    continue
                p = os.path.join(root, fn)
                k = one(p, os.path.join(out, os.path.relpath(p, src))); cnt[k] = cnt.get(k, 0) + 1
    print('替身做好了：' + '，'.join(f'{k} {n} 个' for k, n in cnt.items()), '→', out)


if __name__ == '__main__':
    main()
