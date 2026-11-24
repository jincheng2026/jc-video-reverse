# -*- coding: utf-8 -*-
"""逐帧量每张图在原片里的样子：每一帧在哪（左上角 x、y）、放大几倍、亮度怎么变、糊了多少。
两条片子都能用（原片倍数从 JSON 读）。要先用 逐帧工具.py 缓存 做好原片缓存。

用法：
  python3 -B 量图轨迹.py 逐屏数据.json 缓存文件夹 屏号[,屏号...] [写回] [选项]
      对这几屏里每一张素材图，从这一屏最后一帧往前一帧一帧找：
        1. 在 135×240 的灰度原片上，用 FFT 算「素材放在每个位置时和原片差多少」，在上一帧位置附近找最像的；
           屏里有镜头推近的，同时试几个放大倍数。
        2. 在 540×960 上细找 ±3 像素。
        3. 在找到的位置上，试几档高斯模糊，再用最小二乘拟合「原片 ≈ 倍率 × 素材 + 加亮」，
           倍率小于 1 是变暗或淡入，加亮大于 0 是发白。
      每帧记成 [帧, x, y, 放大, 倍率, 加亮, 模糊]（x、y、模糊按 1080×1920 算）。素材在画面里露出不到 8% 的帧记成看不见。
      写回：把结果写进 JSON 这张图的「轨迹」里（渲染程序见到「轨迹」就照每帧的数画，不再用进场公式和镜头）。
  选项：
      放大=0.004      每帧放大倍数最多变多少（屏里有「镜头」时默认 0.004，没有时默认 0，即不试放大）
      窗=300          每帧位置最多变多少像素（1080 坐标）
      只到=帧号       只量到这一帧为止（往前量到这一帧）
      从=帧号         从这一帧开始往前量（默认这一屏最后一帧）
      全留=1          不去掉和「位置 + 镜头」一样的帧（默认去掉，轨迹只留进场在动的那一段）
      图上写了「裁」[x0,y0,x1,y1] 的，只量这个框里面（图只在框里露出来）。
      图有「进场」起和帧的，只量到进场结束后 4 帧。
      存=文件.json    不写回，把结果存成 {屏号: {素材名: 轨迹}}，方便几个进程同时量、最后用「并入」合进 JSON
  python3 -B 量图轨迹.py 镜头 逐屏数据.json 结果.json 屏号[,屏号] [从第几帧起]
      用「全留=1 放大=0.004 存=结果.json」量出的整屏轨迹，拟合一个匀速推近的镜头（每帧放大、中心、放大为 1 的帧），
      写进这一屏的「镜头」（原来那一屏没量出镜头、其实在慢慢推的用这个）。
  python3 -B 量图轨迹.py 并入 逐屏数据.json 结果1.json [结果2.json ...]
      把「存=」存下的结果写进 JSON 对应图的「轨迹」。
"""
import json, math, os, sys
import numpy as np
from PIL import Image, ImageFilter
from numpy import fft as sfft  # 有的环境里没有 scipy，用 numpy 自带的


def next_fast_len(n):
    """不小于 n、只含因子 2、3、5 的最小整数（FFT 在这种长度上快）。"""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 逐帧工具 as T


def gray(a):
    a = np.asarray(a, np.float32)
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


class Tmpl:
    """一张素材在某个缩放下的模板：灰度、遮罩。"""

    def __init__(self, img1080, s, k):
        # img1080: 1080 坐标下的 RGBA 素材；s: 放大倍数；k: 画面缩小几倍（4 → 270 宽，2 → 540 宽）
        w, h = img1080.size
        nw, nh = max(2, int(round(w * s / k))), max(2, int(round(h * s / k)))
        im = img1080.resize((nw, nh), Image.BILINEAR)
        a = np.asarray(im, np.float32)
        self.g = gray(a[..., :3])
        self.m = a[..., 3] / 255.0
        self.img = im
        self.h, self.w = self.g.shape


class FFTMatch:
    def __init__(self, H, W):
        self.H, self.W = H, W
        self.cache = {}

    def prep_frame(self, o, V):
        self.o, self.V = o, V

    def score(self, t):
        """返回 E(y, x)：模板左上角放在 (x, y) 时的平均平方差，和露出比例；y 从 -(h-1) 到 H-1。"""
        H, W = self.H, self.W
        sh = (next_fast_len(H + t.h - 1), next_fast_len(W + t.w - 1))
        key = (id(t), sh)
        if key not in self.cache:
            fl = lambda a: sfft.rfft2(a[::-1, ::-1], s=sh)
            self.cache[key] = (fl(t.m), fl(t.m * t.g), fl(t.m * t.g * t.g))
        Fm, Fmt, Fmtt = self.cache[key]
        f = lambda a: sfft.rfft2(a, s=sh)
        FV, FVo, FVoo = f(self.V), f(self.V * self.o), f(self.V * self.o * self.o)
        inv = lambda X: sfft.irfft2(X, s=sh)[:H + t.h - 1, :W + t.w - 1]
        Soo, Sot, Stt, Sm = inv(FVoo * Fm), inv(FVo * Fmt), inv(FV * Fmtt), inv(FV * Fm)
        E = (Soo - 2 * Sot + Stt) / np.maximum(Sm, 1e-3)
        vis = Sm / max(t.m.sum(), 1e-3)
        return E, vis


def masked_err(o, V, t_g, t_m, x, y):
    H, W = o.shape
    h, w = t_g.shape
    xs, ys, xe, ye = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if xe <= xs or ye <= ys:
        return None
    oo = o[ys:ye, xs:xe]; vv = V[ys:ye, xs:xe]
    tg = t_g[ys - y:ye - y, xs - x:xe - x]; tm = t_m[ys - y:ye - y, xs - x:xe - x] * vv
    s = tm.sum()
    if s < 1:
        return None
    return float((tm * (oo - tg) ** 2).sum() / s), (oo, tg, tm)


def fit_gain(oo, tg, tm):
    sel = tm > 0.9
    if sel.sum() < 50:
        sel = tm > 0.3
    if sel.sum() < 20:
        return 1.0, 0.0
    A = np.stack([tg[sel], np.ones(sel.sum())], 1)
    (g, c), *_ = np.linalg.lstsq(A, oo[sel], rcond=None)
    return float(g), float(c)


def pip_boxes(data, f):
    out = []
    for seg in data.get('小人', []):
        if seg['起'] <= f < seg['止']:
            out.append(seg['框'])
    return out


def track_item(data, base, scr, it, G, opts, R):
    OS = data['视频'].get('原片倍数', 2)
    im = Image.open(os.path.join(base, '素材', it['素材'] + '.png')).convert('RGBA')
    if OS != 1:
        im = im.resize((im.width // OS, im.height // OS), Image.LANCZOS)
    has_cam = bool(scr.get('镜头'))
    ds = float(opts.get('放大', 0.004 if has_cam else 0))
    win = float(opts.get('窗', 300))
    a, b = scr['起'], scr['止']
    f_from = int(opts.get('从', b - 1))
    mv = it.get('进场', {})
    if '从' not in opts and mv.get('起') is not None and mv.get('帧'):
        # 有「进场」的图只量进场那一段（多量 4 帧），之后照「位置 + 镜头」画，免得每帧的小抖动变成画面抖
        f_from = min(f_from, int(math.ceil(mv['起'] + mv['帧'])) + 4)
    clip = it.get('裁')  # 只在这个框里露出来（四宫格这类：图在自己那一格里滑进来，格子外面看不见）
    f_to = int(opts.get('只到', a))
    # 起点：这一屏最后一帧时图在哪。没有镜头就是「位置」；有镜头按镜头公式算
    x0, y0 = it['位置']
    s0 = 1.0
    if has_cam:
        c = scr['镜头']
        sA, txA, tyA = R.cam_state(c, f_from)
        sB, txB, tyB = R.cam_state(c, c.get('素材帧', c['参考帧']))
        s0 = sA / sB
        tx, ty = txA - s0 * txB, tyA - s0 * tyB
        x0, y0 = s0 * x0 + tx, s0 * y0 + ty
    small, big = {}, {}

    def tm_small(s):
        q = round(s / 0.002) * 0.002
        if q not in small:
            small[q] = Tmpl(im, q, 8)
        return small[q], q

    def tm_big(s, blur=0):
        q = round(s / 0.002) * 0.002
        k = (q, blur)
        if k not in big:
            t = Tmpl(im, q, 2)
            if blur > 0.3:
                bl = t.img.filter(ImageFilter.GaussianBlur(blur / 2))
                a_ = np.asarray(bl, np.float32)
                t.g = gray(a_[..., :3]); t.m = a_[..., 3] / 255.0
            big[k] = t
        return big[k]

    M = FFTMatch(240, 135)
    rows = []
    x, y, s = x0, y0, s0
    lost = False
    for f in range(f_from, f_to - 1, -1):
        if lost:
            rows.append([f, round(x, 1), round(y, 1), round(s, 4), 0.0, 0.0, 0])
            continue
        o540 = G[f].astype(np.float32)
        V540 = np.ones_like(o540)
        for bx0, by0, bx1, by1 in pip_boxes(data, f):
            V540[by0 // 2:by1 // 2, bx0 // 2:bx1 // 2] = 0
        if clip:
            cm = np.zeros_like(V540)
            cx0, cy0, cx1, cy1 = [int(round(v / 2)) for v in clip]
            cm[max(cy0, 0):cy1, max(cx0, 0):cx1] = 1
            V540 *= cm
        o = o540.reshape(240, 4, 135, 4).mean(axis=(1, 3))
        V = (V540.reshape(240, 4, 135, 4).mean(axis=(1, 3)) > 0.99).astype(np.float32)
        M.prep_frame(o, V)
        best = None
        scales = [s] if ds == 0 else [s * (1 + k * ds / 2) for k in range(-2, 3)]
        for s_ in scales:
            t, q = tm_small(s_)
            E, vis = M.score(t)
            # 窗：上一帧位置附近
            cx, cy = x / 8, y / 8  # 1080 → 135 宽坐标
            yy = np.arange(E.shape[0])[:, None] - (t.h - 1)
            xx = np.arange(E.shape[1])[None, :] - (t.w - 1)
            ok = (np.abs(yy - cy) <= win / 8) & (np.abs(xx - cx) <= win / 8) & (vis >= 0.08)
            if not ok.any():
                continue
            Em = np.where(ok, E, np.inf)
            j = int(Em.argmin())
            uy, ux = divmod(j, E.shape[1])
            e = Em.flat[j]
            if best is None or e < best[0]:
                best = (e, (ux - (t.w - 1)) * 8, (uy - (t.h - 1)) * 8, q, float(vis[uy, ux]))
        if best is None:
            lost = True
            rows.append([f, round(x, 1), round(y, 1), round(s, 4), 0.0, 0.0, 0])
            continue
        _, bx, by, bs, vis = best
        # 540 上细找：位置 ±3（540 坐标）、模糊几档
        bx2, by2 = int(round(bx / 2)), int(round(by / 2))
        cand = None
        for blur in (0,):
            t = tm_big(bs, blur)
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    r = masked_err(o540, V540, t.g, t.m, bx2 + dx, by2 + dy)
                    if r and (cand is None or r[0] < cand[0]):
                        cand = (r[0], bx2 + dx, by2 + dy)
        _, px, py = cand
        # 模糊和亮度：在这个位置上试
        fit = None
        for blur in (0, 3, 6, 10, 16, 24, 36):
            t = tm_big(bs, blur)
            r = masked_err(o540, V540, t.g, t.m, px, py)
            if not r:
                continue
            oo, tg, tm = r[1]
            g, c = fit_gain(oo, tg, tm)
            e = float((tm * (oo - (g * tg + c)) ** 2).sum() / max(tm.sum(), 1))
            if fit is None or e < fit[0] * 0.95 - 0.5:  # 糊一点要明显更像才算糊
                fit = (e, blur, g, c)
        e, blur, g, c = fit
        if blur > 0:  # 糊的帧位置再细找一次
            t = tm_big(bs, blur)
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    r = masked_err(o540, V540, g * t.g + c, t.m, px + dx, py + dy)
                    if r and r[0] < e:
                        e, px2, py2 = r[0], px + dx, py + dy
                        px, py = px2, py2
        # 亚像素：在最好位置左右、上下各差一格的误差上配抛物线
        t = tm_big(bs, blur)
        tg_ = g * t.g + c
        sub = [0.0, 0.0]
        e0 = masked_err(o540, V540, tg_, t.m, px, py)
        for ax, (ddx, ddy) in enumerate(((1, 0), (0, 1))):
            em = masked_err(o540, V540, tg_, t.m, px - ddx, py - ddy)
            ep = masked_err(o540, V540, tg_, t.m, px + ddx, py + ddy)
            if e0 and em and ep:
                den = em[0] - 2 * e0[0] + ep[0]
                if den > 1e-6:
                    sub[ax] = float(np.clip(0.5 * (em[0] - ep[0]) / den, -0.5, 0.5))
        x, y, s = (px + sub[0]) * 2.0, (py + sub[1]) * 2.0, bs
        if vis < 0.08:
            lost = True
        rows.append([f, round(x, 1), round(y, 1), round(s, 4), round(g, 3), round(c, 1), blur])
    rows.sort()
    # 从后往前，和「位置 + 镜头」算出来的一样的帧（差不到 3 像素、放大差不到 0.004、亮度没变、不糊）去掉，
    # 渲染程序在轨迹之后就照原来的「位置 + 镜头」画。这样轨迹只记进场那一段真正在动的帧。
    def pred(f):
        if not has_cam:
            return it['位置'][0], it['位置'][1], 1.0
        c = scr['镜头']
        sA, txA, tyA = R.cam_state(c, f)
        sB, txB, tyB = R.cam_state(c, c.get('素材帧', c['参考帧']))
        k = sA / sB
        return k * it['位置'][0] + txA - k * txB, k * it['位置'][1] + tyA - k * tyB, k
    if opts.get('全留') is None:
        while len(rows) > 1:
            r = rows[-1]
            px_, py_, ps_ = pred(r[0])
            same = (abs(r[1] - px_) < 3 and abs(r[2] - py_) < 3 and abs(r[3] - ps_) < 0.004
                    and abs(r[4] - 1) < 0.04 and abs(r[5]) < 5 and r[6] == 0)
            if not same:
                break
            rows.pop()
    # 收拾：亮度几乎没变的记成没变；没有镜头的屏，离「位置」不到 2.5 像素的记成「位置」
    for r in rows:
        if abs(r[4] - 1) < 0.04 and abs(r[5]) < 5:
            r[4], r[5] = 1.0, 0.0
        if r[6] <= 3:  # 3 以下的模糊是对不齐半个像素带出来的，不算糊
            r[6] = 0
        if not has_cam and abs(r[1] - it['位置'][0]) < 2.5 and abs(r[2] - it['位置'][1]) < 2.5 and abs(r[3] - 1) < 1e-3:
            r[1], r[2] = float(it['位置'][0]), float(it['位置'][1])
    return rows


def merge(jp, files):
    data = json.load(open(jp))
    for fn in files:
        res = json.load(open(fn))
        for scr in data['屏']:
            for it in scr.get('图', []):
                rows = res.get(str(scr['号']), {}).get(it.get('素材'))
                if rows:
                    it['轨迹'] = rows
    json.dump(data, open(jp, 'w'), ensure_ascii=False, indent=1)
    print('并入了', len(files), '个结果')


def fit_camera(jp, res_file, which, f_from=None):
    """从「全留=1 放大=0.004」量出的整屏轨迹，拟合一个匀速推近的镜头：每帧放大多少、以哪一点为中心。
    只用图已经停稳（倍率接近 1、不糊）的帧。写进这一屏的「镜头」，并去掉这一屏图的轨迹。"""
    data = json.load(open(jp))
    res = json.load(open(res_file))
    for scr in data['屏']:
        if scr['号'] not in which:
            continue
        S_, F_, P_, Q_ = [], [], [], []
        for it in scr['图']:
            rows = res.get(str(scr['号']), {}).get(it.get('素材'))
            if not rows:
                continue
            for r in rows:
                if abs(r[4] - 1) < 0.05 and r[6] == 0 and (f_from is None or r[0] >= f_from):
                    F_.append(r[0]); S_.append(r[3]); P_.append((r[1], r[2])); Q_.append(tuple(it['位置']))
        F_, S_ = np.array(F_, float), np.array(S_, float)
        A = np.stack([F_, np.ones_like(F_)], 1)
        (k, b0), *_ = np.linalg.lstsq(A, S_, rcond=None)
        # 素材帧：放大为 1 的那一帧
        f1 = -(b0 - 1) / k if abs(k) > 1e-7 else F_[0]
        sf = 1 + k * (F_ - f1)
        # p = c + s (q - c)  →  p - s q = c (1 - s)
        P = np.array(P_); Q = np.array(Q_)
        w = 1 - sf
        ok = np.abs(w) > 1e-3
        if ok.sum() < 3:
            print(f'屏{scr["号"]}：放大几乎不变（每帧 {k:.5f}），不加镜头')
            continue
        cx = float(((P[ok, 0] - sf[ok] * Q[ok, 0]) * w[ok]).sum() / (w[ok] ** 2).sum())
        cy = float(((P[ok, 1] - sf[ok] * Q[ok, 1]) * w[ok]).sum() / (w[ok] ** 2).sum())
        pred = np.stack([cx + sf * (Q[:, 0] - cx), cy + sf * (Q[:, 1] - cy)], 1)
        err = float(np.abs(pred - P).mean())
        f1 = int(round(f1))
        scr['镜头'] = {'每帧放大': round(float(k), 6), '中心': [round(cx), round(cy)], '参考帧': f1, '素材帧': f1,
                     '说明': f'量图轨迹.py 镜头：从整屏轨迹拟合，位置平均差 {err:.1f} 像素'}
        print(f'屏{scr["号"]}：每帧放大 {k * 100:.4f}%，中心 ({cx:.0f}, {cy:.0f})，放大为 1 的帧 {f1}，位置平均差 {err:.1f} 像素')
    json.dump(data, open(jp, 'w'), ensure_ascii=False, indent=1)


def main():
    if sys.argv[1] == '镜头':
        return fit_camera(sys.argv[2], sys.argv[3], [int(v) for v in sys.argv[4].split(',')], int(sys.argv[5]) if len(sys.argv) > 5 else None)
    if sys.argv[1] == '并入':
        return merge(sys.argv[2], sys.argv[3:])
    jp, cdir = sys.argv[1], sys.argv[2]
    which = [int(v) for v in sys.argv[3].split(',')]
    rest = sys.argv[4:]
    write = '写回' in rest
    opts = dict(r.split('=', 1) for r in rest if '=' in r)
    data = json.load(open(jp))
    base = os.path.dirname(os.path.abspath(jp))
    G = T.orig_gray(cdir)
    R = T.load_renderer()
    saved = {}
    for scr in data['屏']:
        if scr['号'] not in which:
            continue
        for it in scr.get('图', []):
            if it.get('片段') or not it.get('素材'):
                continue
            rows = track_item(data, base, scr, it, G, opts, R)
            print(f"屏{scr['号']} {it['素材']}：{len(rows)} 帧")
            if '存' not in opts:
                for r in rows:
                    print('   ', r)
            saved.setdefault(str(scr['号']), {})[it['素材']] = rows
            if write:
                it['轨迹'] = rows
    if '存' in opts:
        json.dump(saved, open(opts['存'], 'w'), ensure_ascii=False)
    if write:
        json.dump(data, open(jp, 'w'), ensure_ascii=False, indent=1)
        print('写回了', jp)


if __name__ == '__main__':
    main()
