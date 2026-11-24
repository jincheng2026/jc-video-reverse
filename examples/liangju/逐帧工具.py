# -*- coding: utf-8 -*-
"""逐帧工具：把原片解成按帧编号的小数组缓存，再拿渲染程序直接出帧和原片逐帧比（不用先编码成片）。
两条片子都能用：原片路径、帧数都从逐屏数据.json 里读。

用法：
  python3 -B 逐帧工具.py 缓存 逐屏数据.json 缓存文件夹
      把原片解成两份缓存：灰度 540×960（和逐帧验收.py 同一种缩法）、彩色 270×480。只要做一次。
  python3 -B 逐帧工具.py 比 逐屏数据.json 缓存文件夹 屏号[,屏号...] [渲染程序.py]
      用渲染程序把这几屏逐帧出出来，缩到 540×960 灰度，按逐帧验收.py 的三个数（整幅差、小块差、动法差）
      和原片比，报每屏超线帧数和没过线的段落（也报清晰度：复刻是原片的几成，0.7 到 1.4 之外算没过）。屏的第一帧拿上一屏最后一帧的原片当「上一帧」算动法差。
  python3 -B 逐帧工具.py 拼图 逐屏数据.json 缓存文件夹 起帧 止帧 输出.jpg [渲染程序.py]
      把这一段原片（上排）和复刻（下排）每帧缩小拼成一张图，用来自己看是哪里不对。

在别的程序里用：import 逐帧工具 as T；T.orig_gray(cache)、T.orig_rgb(cache) 拿到缓存数组（帧, 高, 宽[, 3]）。
"""
import importlib.util, json, os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SW, SH = 540, 960
B = 60
GX, GY = SW // B, SH // B
T_WHOLE, T_MOVE = 8.0, 12.0
HERE = os.path.dirname(os.path.abspath(__file__))


def load_renderer(path=None):
    path = path or os.path.join(HERE, '复刻渲染.py')
    spec = importlib.util.spec_from_file_location('render_mod', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cmd_cache(jp, cdir):
    data = json.load(open(jp))
    src, n = data['视频']['原片'], data['视频']['止']
    os.makedirs(cdir, exist_ok=True)
    g = np.lib.format.open_memmap(os.path.join(cdir, 'gray540.npy'), 'w+', np.uint8, (n, SH, SW))
    c = np.lib.format.open_memmap(os.path.join(cdir, 'rgb270.npy'), 'w+', np.uint8, (n, 480, 270, 3))
    # 灰度和 逐帧验收.py 同一种读法：只缩小、不换颜色，取视频里存的亮度层（16 到 235）拉到 0 到 255
    p = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-i', src, '-map', '0:v:0', '-fps_mode', 'passthrough',
                          '-vf', f'scale={SW}:{SH}:flags=area', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-'],
                         stdout=subprocess.PIPE, bufsize=SW * SH * 8)
    for i in range(n):
        buf = p.stdout.read(SW * SH * 3 // 2)
        y = np.frombuffer(buf, np.uint8, count=SW * SH).reshape(SH, SW).astype(np.float32)
        g[i] = np.clip(np.round((y - 16.0) * (255.0 / 219.0)), 0, 255).astype(np.uint8)
    p.kill()
    p = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-i', src, '-map', '0:v:0', '-fps_mode', 'passthrough',
                          '-vf', 'scale=270:480:flags=area+accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv:out_range=pc', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         stdout=subprocess.PIPE, bufsize=270 * 480 * 3 * 8)
    for i in range(n):
        c[i] = np.frombuffer(p.stdout.read(270 * 480 * 3), np.uint8).reshape(480, 270, 3)
    p.kill()
    g.flush(); c.flush()
    print('缓存好了', n, '帧')


def orig_gray(cdir):
    return np.load(os.path.join(cdir, 'gray540.npy'), mmap_mode='r')


def orig_rgb(cdir):
    return np.load(os.path.join(cdir, 'rgb270.npy'), mmap_mode='r')


def to_gray540(img):
    """PIL RGB 1080×1920 → 540×960 亮度 float。按 bt709 算亮度（渲染程序编码时用 bt709），
    和 逐帧验收.py 取亮度层再拉到 0 到 255 是同一个量。"""
    a = np.asarray(img.convert('RGB'), np.float32)
    a = a.reshape(SH, 2, SW, 2, 3).mean(axis=(1, 3))
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def blocks(a):
    return a.reshape(GY, B, GX, B).mean(axis=(1, 3))


def sharp(a):
    """清晰度：相邻像素差的平均，和 逐帧验收.py 同一个算法。"""
    return float(np.abs(np.diff(a, axis=1)).mean() + np.abs(np.diff(a, axis=0)).mean())


def sharp_ratio(o, r):
    return (sharp(r) + 0.5) / (sharp(o) + 0.5)


def metrics(o_prev, o, r_prev, r):
    d = np.abs(o - r)
    whole = float(d.mean())
    md = np.abs(blocks(np.abs(o - o_prev)) - blocks(np.abs(r - r_prev)))
    i = int(md.argmax())
    gy, gx = divmod(i, GX)
    return whole, float(md.flat[i]), (gx * B * 2, gy * B * 2)


def segs(mask, idx):
    out, s, last = [], None, None
    for i, m in zip(idx, mask):
        if m:
            if s is None:
                s = i
            elif i - last > 3:
                out.append((s, last + 1)); s = i
            last = i
    if s is not None:
        out.append((s, last + 1))
    return out


def render_range(R, base, data, a, b):
    orig = R.Orig(data['视频']['原片'], max(a - 12, 0))
    out = []
    for f in range(a, b):
        out.append(R.render_frame(base, data, f, orig))
        orig.prune(f - 14)
    orig.close()
    R._ann_cache.clear()
    return out


def cmd_compare(jp, cdir, which, rpath=None, quiet=False):
    data = json.load(open(jp))
    R = load_renderer(rpath)
    R.OS = data['视频'].get('原片倍数', 2)
    base = os.path.dirname(os.path.abspath(jp))
    G = orig_gray(cdir)
    res = {}
    for scr in data['屏']:
        if scr['号'] not in which:
            continue
        a, b = scr['起'], scr['止']
        a0 = max(a - 1, 0)
        frames = render_range(R, base, data, a0, b)
        rs = [to_gray540(fr) for fr in frames]
        W_, M_, P_, Q_ = [], [], [], []
        for k, f in enumerate(range(a0 + 1 if a > 0 else a, b)):
            j = f - a0
            o, op = G[f].astype(np.float32), G[max(f - 1, 0)].astype(np.float32)
            r, rp = rs[j], rs[max(j - 1, 0)]
            w, m, p = metrics(op, o, rp, r)
            W_.append(w); M_.append(m); P_.append(p); Q_.append(sharp_ratio(o, r))
        idx = list(range(a, b))
        W_, M_, Q_ = np.array(W_), np.array(M_), np.array(Q_)
        res[scr['号']] = (W_, M_, P_, Q_)
        if not quiet:
            bq = (Q_ < 0.7) | (Q_ > 1.4)
            print(f"屏{scr['号']:>2} {a}-{b} 动法超线 {(M_ > T_MOVE).sum():>3}  整幅>8 {(W_ > 8).sum():>3}  >12 {(W_ > 12).sum():>3}  清晰度没过 {bq.sum():>3}  整幅中位 {np.median(W_):.1f}")
            for s_, e_ in segs(bq, idx):
                print(f'    清晰度 {s_}-{e_} 几成 {Q_[s_ - a:e_ - a].min():.2f} 到 {Q_[s_ - a:e_ - a].max():.2f}')
            for s, e in segs(M_ > T_MOVE, idx):
                j = s - a + int(M_[s - a:e - a].argmax())
                print(f'    动法 {s}-{e} 最大 {M_[j]:.1f} 在 {a + j} {P_[j]}')
            for s, e in segs(W_ > 8, idx):
                print(f'    整幅 {s}-{e} 最大 {W_[s - a:e - a].max():.1f}')
    return res


def cmd_strip(jp, cdir, a, b, out, rpath=None, scale=0.16):
    data = json.load(open(jp))
    R = load_renderer(rpath)
    R.OS = data['视频'].get('原片倍数', 2)
    base = os.path.dirname(os.path.abspath(jp))
    C = orig_rgb(cdir)
    frames = render_range(R, base, data, a, b)
    tw, th = int(1080 * scale), int(1920 * scale)
    n = b - a
    per = min(n, 12)
    rows = (n + per - 1) // per
    canvas = Image.new('RGB', (per * (tw + 4), rows * (2 * th + 30)), (40, 40, 40))
    d = ImageDraw.Draw(canvas)
    try:
        F = ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc', 16)
    except Exception:
        F = None
    for k, f in enumerate(range(a, b)):
        r_, c_ = divmod(k, per)
        x, y = c_ * (tw + 4), r_ * (2 * th + 30)
        canvas.paste(Image.fromarray(np.asarray(C[f])).resize((tw, th)), (x, y + 20))
        canvas.paste(frames[k].resize((tw, th)), (x, y + 20 + th))
        d.text((x + 2, y + 1), str(f), fill=(255, 255, 0), font=F)
    canvas.save(out, quality=88)
    print(out)


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == '缓存':
        cmd_cache(sys.argv[2], sys.argv[3])
    elif cmd == '比':
        cmd_compare(sys.argv[2], sys.argv[3], [int(x) for x in sys.argv[4].split(',')],
                    sys.argv[5] if len(sys.argv) > 5 else None)
    elif cmd == '拼图':
        cmd_strip(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6],
                  sys.argv[7] if len(sys.argv) > 7 else None)
