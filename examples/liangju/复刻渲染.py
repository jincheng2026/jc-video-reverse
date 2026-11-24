# -*- coding: utf-8 -*-
"""颜值分析口播复刻渲染程序。

用法：
  python3 复刻渲染.py 取素材 逐屏数据.json          # 按「素材」一节从原片截图、抠图、抹字，存进 素材/
  python3 复刻渲染.py 渲染 逐屏数据.json [屏号,...]   # 不写屏号就渲染全部屏；写了只重渲这几屏
  python3 复刻渲染.py 合成 逐屏数据.json              # 把各屏片段接起来，配原片声音，出成片
  python3 复刻渲染.py 单帧 逐屏数据.json 帧号 输出.png  # 调试用：出某一帧

所有坐标都按 1080×1920 算；原片是 2160×3840，截素材时自动乘 2。
每一屏只用 JSON 里的一条数据描述。排法、进场动作、大字出法、标注各是一组函数，
JSON 里写函数名（中文）和参数。加新屏只加数据，遇到新排法、新动作才加函数。
"""
import json, math, os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
RED = (249, 26, 2)
YELLOW = (255, 185, 15)
OS = 2  # 原片是 1080 画面的几倍：第一条原片 2160×3840 是 2，第二条原片就是 1080×1920，写在 视频.原片倍数
WHITE = (255, 255, 255)
COLORS = {'红': RED, '白': WHITE, '黄': YELLOW, '灰': (200, 200, 200)}

# ---------------------------------------------------------------- 工具


def ease(t, kind='减速', p=2.0):
    t = min(max(t, 0.0), 1.0)
    if kind == '减速':
        return 1 - (1 - t) ** p
    if kind == '加速':
        return t ** p
    if kind == '先慢后快再慢':
        return t * t * (3 - 2 * t)
    return t


def prog(f, a, n):
    """帧 f 在区间 [a, a+n] 里走到了几成，0 到 1。"""
    if n <= 0:
        return 1.0 if f >= a else 0.0
    return min(max((f - a) / n, 0.0), 1.0)


def box_blur_np(a, r):
    """a: HxW float，盒式模糊（积分图），用来做抹字。"""
    if r < 1:
        return a
    r = int(r)
    p = np.pad(a, ((r + 1, r), (r + 1, r)), mode='edge')
    c = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    out = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return out / (k * k)


def inpaint(rgb, mask):
    """很简单的抹字：从小到大的模糊里取周围好像素的平均，补进 mask 里。rgb: HxWx3 uint8, mask: HxW bool。"""
    out = rgb.astype(np.float32).copy()
    known = (~mask).astype(np.float32)
    todo = mask.copy()
    for r in [2, 4, 8, 16, 32, 64, 128, 256, 512]:  # 256、512 是第二轮加的：大块（比如整个小人）原来补不满，中间留着原来的像素
        den = box_blur_np(box_blur_np(known, r), r)
        ok = todo & (den > 0.15)
        if ok.any():
            for c in range(3):
                num = box_blur_np(box_blur_np(out[..., c] * known, r), r)
                ch = out[..., c]
                ch[ok] = (num[ok] / den[ok])
        todo &= ~ok
        if not todo.any():
            break
    # 补的地方再轻轻抹匀一点
    sm = np.stack([box_blur_np(out[..., c], 2) for c in range(3)], -1)
    out[mask] = sm[mask]
    return np.clip(out, 0, 255).astype(np.uint8)


def dilate(m, r):
    return box_blur_np(m.astype(np.float32), r) > 0.01


def ellipse_alpha(w, h, feather, grow=1.0):
    """椭圆虚边：中间不透明，边上 feather 比例内渐隐。grow>1 时椭圆比框大，只削掉四个角。"""
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - w / 2 + .5) / (w / 2 * grow)) ** 2 + ((yy - h / 2 + .5) / (h / 2 * grow)) ** 2)
    return np.clip((1 - d) / max(feather, 1e-3), 0, 1)


class Orig:
    """按顺序读原片（缩到 1080×1920），带一点前看缓存。"""

    def __init__(self, path, start):
        self.path, self.next, self.cache, self.proc = path, start, {}, None
        self.start = start

    def _open(self):
        self.proc = subprocess.Popen(
            ['ffmpeg', '-loglevel', 'quiet', '-ss', f'{self.start / FPS:.4f}', '-i', self.path,
             '-vf', f'scale={W}:{H}:flags=area+accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv:out_range=pc', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
            stdout=subprocess.PIPE)

    def get(self, n):
        if n in self.cache:
            return self.cache[n]
        if n < self.next and (self.proc is not None or n < self.start):
            # 要的帧在读到的位置之前（第二条第 38 屏：上一屏最后一帧定住当底），单独读这一帧
            raw = subprocess.run(['ffmpeg', '-loglevel', 'quiet', '-ss', f'{max(n - 0.5, 0) / FPS:.4f}', '-i', self.path,
                                  '-frames:v', '1', '-vf', f'scale={W}:{H}:flags=area+accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv:out_range=pc', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                 stdout=subprocess.PIPE).stdout
            self.cache[n] = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
            return self.cache[n]
        if self.proc is None:
            self._open()
        while self.next <= n:
            buf = self.proc.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                raise RuntimeError(f'原片读到头了：第 {self.next} 帧')
            self.cache[self.next] = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            self.next += 1
        return self.cache[n]

    def prune(self, keep_from):
        for k in [k for k in self.cache if self.start <= k < keep_from]:
            del self.cache[k]

    def close(self):
        if self.proc:
            self.proc.stdout.close(); self.proc.kill()


# ---------------------------------------------------------------- 取素材


def color_mask(sub, kind):
    sub = sub.astype(np.int16)
    if kind == '整块':
        return np.ones(sub.shape[:2], bool)
    if kind == '红':
        return (sub[..., 0] > 150) & (sub[..., 1] < 110) & (sub[..., 2] < 110)
    if kind == '红宽':  # 连半透明的红字也算上
        return (sub[..., 0] - np.maximum(sub[..., 1], sub[..., 2])) > 45
    if kind == '黄':
        return (sub[..., 0] > 180) & (sub[..., 1] > 120) & (sub[..., 2] < 100)
    if kind == '白':
        return sub.min(2) > 215
    if kind == '白字带影':  # 白字连同它的暗边
        return sub.min(2) > 200
    raise ValueError(kind)


def overlay_mask(img, items, ox, oy):
    """img 是原分辨率的一块（左上角在原片的 (ox, oy)），items 是 [[种类, 画面框(1080 坐标)], ...]。"""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), bool)
    for kind, box in items:
        bx0, by0, bx1, by1 = [v * OS for v in box]
        bx0, bx1, by0, by1 = bx0 - ox, bx1 - ox, by0 - oy, by1 - oy
        sl = (slice(min(max(by0, 0), h), min(max(by1, 0), h)), slice(min(max(bx0, 0), w), min(max(bx1, 0), w)))
        if sl[0].stop - sl[0].start <= 0 or sl[1].stop - sl[1].start <= 0:
            continue
        mask[sl] |= color_mask(img[sl], kind)
    return mask


def grab_frames(src, frames, fdir):
    os.makedirs(fdir, exist_ok=True)
    for f in sorted(set(frames)):
        out = os.path.join(fdir, f'f_{f:04d}.png')
        if not os.path.exists(out):
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', f'{f / FPS:.4f}', '-i', src,
                            '-frames:v', '1', '-vf', 'scale=flags=accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv:out_range=pc',
                            out], check=True)


def find_offset(crop, valid, frame, rng, x0, y0):
    """在 frame（整幅原分辨率）里找 crop 现在在哪：返回 (dx, dy)，crop 左上角原本在 (x0, y0)。"""
    s = 8
    c = crop[::s, ::s].astype(np.float32).mean(2)
    v = valid[::s, ::s]
    F = frame.astype(np.float32).mean(2)[::s, ::s]
    H_, W_ = F.shape
    h, w = c.shape
    best = None
    dx0, dx1, dy0, dy1 = rng
    for dy in range(dy0 // s, dy1 // s + 1):
        for dx in range(dx0 // s, dx1 // s + 1):
            X, Y = x0 // s + dx, y0 // s + dy
            xs, ys = max(X, 0), max(Y, 0)
            xe, ye = min(X + w, W_), min(Y + h, H_)
            if xe - xs < w * 0.3 or ye - ys < h * 0.2:
                continue
            sub = F[ys:ye, xs:xe]
            cc = c[ys - Y:ye - Y, xs - X:xe - X]
            vv = v[ys - Y:ye - Y, xs - X:xe - X]
            if vv.sum() < 50:
                continue
            e = np.abs(sub - cc)[vv].mean()
            if best is None or e < best[0]:
                best = (e, dx * s, dy * s)
    if best is None:
        return None
    # 细找
    e0, bx, by = best
    c2 = crop.astype(np.float32).mean(2)[::2, ::2]
    v2 = valid[::2, ::2]
    F2 = frame.astype(np.float32).mean(2)
    for dy in range(by - 8, by + 9, 2):
        for dx in range(bx - 8, bx + 9, 2):
            X, Y = x0 + dx, y0 + dy
            xs, ys = max(X, 0), max(Y, 0)
            xe, ye = min(X + crop.shape[1], F2.shape[1]), min(Y + crop.shape[0], F2.shape[0])
            sub = F2[ys:ye:2, xs:xe:2]
            cc = c2[(ys - Y) // 2:(ys - Y) // 2 + sub.shape[0], (xs - X) // 2:(xs - X) // 2 + sub.shape[1]]
            vv = v2[(ys - Y) // 2:(ys - Y) // 2 + sub.shape[0], (xs - X) // 2:(xs - X) // 2 + sub.shape[1]]
            if cc.shape != sub.shape or vv.sum() < 50:
                continue
            e = np.abs(sub - cc)[vv].mean()
            if e < best[0]:
                best = (e, dx, dy)
    return best[1], best[2], best[0]


def cmd_take_assets(data, base, only=None):
    src = data['视频']['原片']
    outdir = os.path.join(base, '素材')
    os.makedirs(outdir, exist_ok=True)
    fdir = os.path.join(data['视频']['临时目录'], '素材帧')
    todo = [a for a in data['素材'] if not only or a['名'] in only]
    need = [a['帧'] for a in todo] + [f for a in todo for f in a.get('补干净', {}).get('帧', [])]
    grab_frames(src, need, fdir)
    load = lambda f: np.asarray(Image.open(os.path.join(fdir, f'f_{f:04d}.png')).convert('RGB'))
    for a in todo:
        im = load(a['帧'])
        x0, y0, x1, y1 = [v * OS for v in a['框']]  # 原分辨率
        crop = im[y0:y1, x0:x1].copy()
        h, w = crop.shape[:2]
        note = []
        # 1. 先从别的帧把被大字、标注盖住的地方补回来（这些帧里图还没被盖，或者正滑着、被盖的地方不一样）
        cl = a.get('补干净')
        if cl:
            m = dilate(overlay_mask(crop, cl['盖住'], x0, y0), cl.get('外扩', 6))
            for f in cl['帧']:
                if not m.any():
                    break
                F = load(f)
                off = find_offset(crop, ~m, F, cl.get('搜索', [-16, 16, -16, 16]), x0, y0)
                if off is None:
                    continue
                dx, dy, err = off
                if err > cl.get('容差', 20):
                    note.append(f'第{f}帧对不上(差{err:.0f})没用')
                    continue
                mf = dilate(overlay_mask(F, cl['盖住'], 0, 0), cl.get('外扩', 6))
                yy, xx = np.where(m)
                sy, sx = yy + y0 + dy, xx + x0 + dx
                ok = (sy >= 0) & (sy < F.shape[0]) & (sx >= 0) & (sx < F.shape[1])
                yy, xx, sy, sx = yy[ok], xx[ok], sy[ok], sx[ok]
                good = ~mf[sy, sx]
                crop[yy[good], xx[good]] = F[sy[good], sx[good]]
                m[yy[good], xx[good]] = False
                note.append(f'第{f}帧补了{int(good.sum())}点(挪{dx},{dy},差{err:.0f})')
            if m.any():
                crop = inpaint(crop, m)
                note.append(f'剩{int(m.sum())}点抹掉')
        # 2. 剩下抠不干净的，按颜色抹掉
        mask = overlay_mask(crop, a.get('抹掉', []), x0, y0)
        if mask.any():
            mask = dilate(mask, a.get('抹掉外扩', 6))
            crop = inpaint(crop, mask)
            note.append('抹掉了')
        img = Image.fromarray(crop)
        if a.get('抠法') == '黑底椭圆':
            al = ellipse_alpha(w, h, a.get('虚边', 0.18), a.get('椭圆放大', 1.0))
            lum = crop.max(2).astype(np.float32)
            al = al * np.clip(lum / 28.0, 0, 1)
            img = img.convert('RGBA')
            img.putalpha(Image.fromarray((al * 255).astype(np.uint8)))
        elif a.get('抠法') == '黑底':  # 第二条：黑底上的图形状不规则，只按亮度抠，不削椭圆
            lum = crop.max(2).astype(np.float32)
            al = np.clip((lum - a.get('黑阈', 10)) / 25.0, 0, 1)
            img = img.convert('RGBA')
            img.putalpha(Image.fromarray((al * 255).astype(np.uint8)))
        elif a.get('抠法') == '按颜色':  # 第二条第 6 屏：弯的黄箭头、小标签，按颜色抠成贴图
            m = color_mask(crop, a.get('颜色', '黄'))
            m = dilate(m, a.get('外扩', 1))
            al = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1))
            img = img.convert('RGBA')
            img.putalpha(al)
        img.save(os.path.join(outdir, a['名'] + '.png'))
        print('素材', a['名'], img.size, '；'.join(note))


# ---------------------------------------------------------------- 字


_font_cache = {}


def font(path, size, index=0):
    k = (path, size, index)
    if k not in _font_cache:
        _font_cache[k] = ImageFont.truetype(path, size, index=index)
    return _font_cache[k]


_glyph_cache = {}


def text_chars(style, text, box, vertical=False):
    """把一行（或竖排一列）字排进 box（墨迹框），返回每个字的 (RGBA 图层, 左上角, 字的中心)。
    字体：宋体 Black，向右斜 13 度（原片的字本机没有，凑合用）。"""
    key = (json.dumps(style, sort_keys=True), text, tuple(box), vertical)
    if key in _glyph_cache:
        return _glyph_cache[key]
    S = 3
    F = font(style['字体'], 160 * S, style.get('字体序号', 0))
    shear = math.tan(math.radians(style.get('斜', 13)))
    color = COLORS.get(style.get('颜色', '白'), WHITE)
    masks = []
    adv = [F.getlength(c) for c in text]
    lh = F.getbbox('国')[3] - F.getbbox('国')[1]
    pad = 120 * S
    if not vertical:
        cw, ch = int(sum(adv) + 2 * pad), int(lh * 1.6 + 2 * pad)
    else:
        cw, ch = int(max(adv) + 2 * pad), int(lh * style.get('行距', 1.15) * len(text) + 2 * pad)
    pos = pad
    for c, a_ in zip(text, adv):
        m = Image.new('L', (cw, ch), 0)
        d = ImageDraw.Draw(m)
        if not vertical:
            d.text((pos, pad), c, font=F, fill=255)
            pos += a_
        else:
            d.text((pad + (max(adv) - a_) / 2, pos), c, font=F, fill=255)
            pos += lh * style.get('行距', 1.15)
        m = m.transform((cw, ch), Image.AFFINE, (1, shear, -shear * ch / 2, 0, 1, 0), resample=Image.BICUBIC)
        masks.append(m)
    union = np.maximum.reduce([np.asarray(m) for m in masks])
    ys, xs = np.where(union > 20)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    bx0, by0, bx1, by1 = box
    tw, th = int(round(bx1 - bx0)), int(round(by1 - by0))
    out = []
    for m in masks:
        m = m.crop((x0, y0, x1, y1)).resize((tw, th), Image.LANCZOS)
        if style.get('加粗'):
            m = m.filter(ImageFilter.MaxFilter(2 * int(style['加粗']) + 1))
        arr = np.asarray(m)
        cols = np.where(arr.max(0) > 20)[0]
        rows = np.where(arr.max(1) > 20)[0]
        if len(cols) == 0:
            continue
        lay = Image.new('RGBA', (tw + 16, th + 16), (0, 0, 0, 0))
        sh_a = style.get('影', 0.45)
        if sh_a > 0:
            sh = m.filter(ImageFilter.GaussianBlur(4)).point(lambda v: int(v * sh_a))
            shadow = Image.new('RGBA', m.size, (0, 0, 0, 255)); shadow.putalpha(sh)
            lay.alpha_composite(shadow, (4, 5))
        fill = Image.new('RGBA', m.size, color + (255,)); fill.putalpha(m)
        lay.alpha_composite(fill, (0, 0))
        cx = bx0 + (cols.min() + cols.max()) / 2
        cy = by0 + (rows.min() + rows.max()) / 2
        out.append((lay, (int(bx0), int(by0)), (cx, cy)))
    _glyph_cache[key] = out
    return out


BOUNCE_SX = [0.23, 0.52, 0.80, 1.0, 1.0, 0.83, 0.71, 0.90, 1.0, 0.94, 0.93, 1.0, 0.99, 0.955]  # 第 19 屏实测


def scaled(lay, ox, oy, cx, cy, sx, sy):
    """以 (cx, cy)（画面坐标）为中心缩放一个字图层，返回新图层和左上角。"""
    if abs(sx - 1) < 1e-3 and abs(sy - 1) < 1e-3:
        return lay, (ox, oy)
    w, h = lay.size
    nw, nh = max(1, int(round(w * sx))), max(1, int(round(h * sy)))
    l2 = lay.resize((nw, nh), Image.BICUBIC)
    nox = cx - (cx - ox) * sx
    noy = cy - (cy - oy) * sy
    return l2, (int(round(nox)), int(round(noy)))


def mul_alpha(lay, a):
    if a >= 0.999:
        return lay
    l2 = lay.copy()
    l2.putalpha(l2.getchannel('A').point(lambda v: int(v * a)))
    return l2


# 大字出法：每个函数给出某一帧每个字的 (不透明度, 横向比例, 纵向比例, 上移像素)
def 出法_逐字写出(k, n_chars, p):
    """k: 相对出场起点的帧数。字按先后排开，每个字 p['每字淡入'] 帧淡入。"""
    span = p.get('写完帧', 4)
    fade = p.get('每字淡入', 2)
    res = []
    for i in range(n_chars):
        t0 = span * i / max(n_chars - 1, 1) if n_chars > 1 else 0
        if 'f字' in p:
            t0 = p['f字'][i]
        a = prog(k + 1, t0, fade) if k + 1 > t0 else 0.0
        res.append((a, 1, 1, 0))
    return res


def 出法_弹一下(k, n_chars, p):
    if k < 0:
        return [(0, 1, 1, 0)] * n_chars
    sx = BOUNCE_SX[int(k)] if int(k) < len(BOUNCE_SX) else 1.0
    a = 1.0 if k >= 1 else 0.6
    return [(a, sx, 1, 0)] * n_chars


def 出法_淡入(k, n_chars, p):
    n = p.get('帧', 4)
    t = prog(k, 0, n)
    e = ease(t)
    s0 = p.get('起始比例', 1.0)
    s = s0 + (1 - s0) * e
    up = p.get('上移', 0) * (1 - e)
    return [(t, s, s, up)] * n_chars


TEXT_IN = {'逐字写出': 出法_逐字写出, '弹一下': 出法_弹一下, '淡入': 出法_淡入}


def table_at(tb, f, key):
    """每帧一个数的表 {起, key: [...]}：表之前返回 None（不画），表之后用最后一行。"""
    i = int(f) - int(tb['起'])
    if i < 0:
        return None
    v = tb[key]
    return v[min(i, len(v) - 1)]


def draw_text(frame, item, f, style_base):
    style = dict(style_base)
    style.update(item.get('样式', {}))
    style['颜色'] = item.get('色', '白')
    if item.get('逐帧'):  # 量字和标注.py 量出来的每帧每个字的透明度，起止、淡出、闪都由它定
        row = table_at(item['逐帧'], f, '透明')
        tbs = item.get('逐帧入形')
        in_shape = bool(tbs) and 0 <= int(f) - int(tbs['起']) < len(tbs['值'])
        if not in_shape and (row is None or max(row) <= 0.01):
            return
        chars = text_chars(style, item['字'], item['框'], item.get('竖排', False))
        k = f - item['起']
        states = TEXT_IN[item.get('出法', '淡入')](k, len(chars), item.get('参数', {})) if item.get('逐帧带形变') else [(1, 1, 1, 0)] * len(chars)
        gs, gx, gy = 1.0, 0, 0
        if item.get('逐帧形变'):  # 整段字一起放大（以字框中心）和挪动，量字和标注.py「形变」量的
            v = table_at(item['逐帧形变'], f, '值')
            i_ = int(f) - int(item['逐帧形变']['起'])
            if v is not None and i_ < len(item['逐帧形变']['值']):
                gs, gx, gy = v
        bx0, by0, bx1, by1 = item['框']
        bcx, bcy = (bx0 + bx1) / 2, (by0 + by1) / 2
        shape = None
        if in_shape:  # 每个字 [透明度, 横向几成, 横向拖影像素, 糊半径]，量字入形.py 定的；表以后照旧按透明度表
            shape = tbs['值'][int(f) - int(tbs['起'])]
            row = [c[0] for c in shape]
            states = [(1, c[1], 1, 0) for c in shape]
            if tbs.get('靠左'):  # 横向压扁时以字的左边为准（原片第 2、4 屏的字是从左边撑开的）
                chars = [(lay, (ox, oy), (ox + (lay.getbbox() or (0, 0, 0, 0))[0], cy)) for lay, (ox, oy), (cx, cy) in chars]
        target = frame
        if shape and any(c[2] > 1 or c[3] > 0 for c in shape):
            target = Image.new('RGBA', frame.size, (0, 0, 0, 0))
        for (lay, (ox, oy), (cx, cy)), (_, sx, sy, up), a in zip(chars, states, row):
            if a <= 0.01:
                continue
            l2, (nx, ny) = scaled(lay, ox, oy, cx, cy, sx, sy)
            if abs(gs - 1) > 1e-3:
                l2, (nx, ny) = scaled(l2, nx, ny, bcx, bcy, gs, gs)
            target.alpha_composite(mul_alpha(l2, a), (int(nx + gx), int(ny - up + gy)))
        if target is not frame:
            hb, gb = max(c[2] for c in shape), max(c[3] for c in shape)
            x0, y0 = max(bx0 - 300, 0), max(by0 - 120, 0)
            x1, y1 = min(bx1 + 300, frame.width), min(by1 + 120, frame.height)
            part = target.crop((x0, y0, x1, y1))
            if gb > 0:
                part = part.filter(ImageFilter.GaussianBlur(gb))
            if hb > 1:
                arr = np.asarray(part, np.float32)
                pm = arr[..., :3] * arr[..., 3:4] / 255  # 先乘透明度再拖，边上才不发黑
                k = int(hb)
                def hb_(a2):
                    p2 = np.pad(a2, ((0, 0), (k // 2, k - k // 2), (0, 0)))
                    c2 = np.cumsum(p2, axis=1, dtype=np.float64)
                    c2 = np.concatenate([np.zeros_like(c2[:, :1]), c2], axis=1)
                    return (c2[:, k:k + a2.shape[1]] - c2[:, :a2.shape[1]]) / k
                pm2, al2 = hb_(pm), hb_(arr[..., 3:4])
                rgb2 = np.where(al2 > 0.5, pm2 * 255 / np.maximum(al2, 1e-3), 0)
                part = Image.fromarray(np.concatenate([np.clip(rgb2, 0, 255), np.clip(al2, 0, 255)], -1).astype(np.uint8), 'RGBA')
            frame.alpha_composite(part, (x0, y0))
        return
    k = f - item['起']
    if k < 0 or ('止' in item and f >= item['止']):
        return
    chars = text_chars(style, item['字'], item['框'], item.get('竖排', False))
    fn = TEXT_IN[item.get('出法', '淡入')]
    states = fn(k, len(chars), item.get('参数', {}))
    out_a = 1.0
    if '淡出' in item:  # [起, 帧数]
        oa, on = item['淡出']
        out_a = 1 - prog(f, oa, on)
    out_a *= blink_alpha(f, item.get('闪'))
    for (lay, (ox, oy), (cx, cy)), (a, sx, sy, up) in zip(chars, states):
        a *= out_a
        if a <= 0.01 or sx <= 0.01:
            continue
        l2, (nx, ny) = scaled(lay, ox, oy, cx, cy, sx, sy)
        frame.alpha_composite(mul_alpha(l2, a), (nx, int(ny - up)))


# ---------------------------------------------------------------- 标注


def blink_alpha(f, b):
    """闪：从 b['起'] 开始每 b['周期'] 帧闪一次：淡出、全灭、淡入。"""
    if not b or f < b['起']:
        return 1.0
    p = (f - b['起']) % b['周期']
    fo, off, fi = b.get('淡出', 4), b.get('灭', 3), b.get('淡入', 3)
    if p < fo:
        return 1 - p / fo
    if p < fo + off:
        return 0.0
    if p < fo + off + fi:
        return (p - fo - off + 1) / (fi + 1)
    return 1.0


def dashed_path(d, pts, dash, gap, width, fill, length_frac=1.0, skip_r=0):
    segs = list(zip(pts[:-1], pts[1:]))
    total = sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in segs)
    limit = total * length_frac
    s_glob = 0.0
    on = True
    rem = dash
    for (x0, y0), (x1, y1) in segs:
        L = math.hypot(x1 - x0, y1 - y0)
        if L == 0:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        s = 0.0
        while s < L and s_glob < limit:
            step = min(rem, L - s, limit - s_glob)
            if on:
                a, b = s, s + step
                if skip_r:
                    a = max(a, skip_r) if True else a
                    b = min(b, L - skip_r)
                if b > a:
                    d.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * b, y0 + uy * b)], fill=fill, width=width)
            s += step; s_glob += step; rem -= step
            if rem <= 1e-6:
                on = not on
                rem = dash if on else gap
    return total


def ann_layer(item, f, SS=2):
    """画一个标注，返回 1080×1920 的 RGBA 图层（未乘整体不透明度）。"""
    lay = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    kind = item['种类']
    col = COLORS.get(item.get('色', '黄' if item['种类'] in ('黄箭头', '箭头线') else '红'), RED) + (255,)
    k = f - item['起']
    if kind == '红圈':
        r = item.get('外径', 30) / 2 * SS
        wd = int(item.get('线宽', 7) * SS)
        for x, y in item['点']:
            d.ellipse([x * SS - r, y * SS - r, x * SS + r, y * SS + r], outline=col, width=wd)
    elif kind in ('虚线', '虚线框'):
        frac = 1.0
        if '_画到' in item:  # 逐帧画到：直接给画到几成
            frac = item['_画到']
        elif item.get('画出帧'):
            frac = ease(prog(k, 0, item['画出帧']), '先慢后快再慢')
        lines = item['线'] if '线' in item else [item['点']]
        for pts in lines:
            if kind == '虚线框':
                pts = pts + [pts[0]]
            dashed_path(d, [(x * SS, y * SS) for x, y in pts], item.get('段', 22) * SS, item.get('空', 16) * SS,
                        int(item.get('线宽', 8) * SS), col, frac)
    elif kind == '箭头线':  # 一条带箭头的线，从「起点」画到「终点」（终点是箭头）
        (x0, y0), (x1, y1) = item['起点'], item['终点']
        frac = item['_画到'] if '_画到' in item else (ease(prog(k, 0, item['画出帧']), '减速') if item.get('画出帧') else 1.0)
        x1, y1 = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
        wd = item.get('线宽', 10)
        L = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        hl, hw = item.get('头长', 34), item.get('头宽', 26)
        bx, by = x1 - ux * hl, y1 - uy * hl
        d.line([(x0 * SS, y0 * SS), (bx * SS, by * SS)], fill=col, width=int(wd * SS))
        d.polygon([(x1 * SS, y1 * SS), ((bx - uy * hw) * SS, (by + ux * hw) * SS), ((bx + uy * hw) * SS, (by - ux * hw) * SS)], fill=col)
    elif kind == '黄箭头':
        x, y = item['位置']
        ang = math.radians(item.get('方向', 45))  # 箭头指向，0=向右，90=向下
        bob = item.get('顶', 6) * (0.5 - 0.5 * math.cos(2 * math.pi * max(k, 0) / item.get('周期', 10)))
        x += math.cos(ang) * bob; y += math.sin(ang) * bob
        L = item.get('长', 50)
        ux, uy = math.cos(ang), math.sin(ang)
        px, py = -uy, ux
        tip = (x + ux * L / 2, y + uy * L / 2)
        tail = (x - ux * L / 2, y - uy * L / 2)
        hb = (x + ux * L * 0.02, y + uy * L * 0.02)
        hw = L * 0.42
        sw = L * 0.16
        poly = [tip, (hb[0] + px * hw, hb[1] + py * hw), (hb[0] + px * sw, hb[1] + py * sw),
                (tail[0] + px * sw, tail[1] + py * sw), (tail[0] - px * sw, tail[1] - py * sw),
                (hb[0] - px * sw, hb[1] - py * sw), (hb[0] - px * hw, hb[1] - py * hw)]
        d.polygon([(a * SS, b * SS) for a, b in poly], fill=col)
    return lay.resize((W, H), Image.LANCZOS)


_ann_cache = {}


def draw_ann(frame_rgba, item, f, cam=None, follow=None):
    k = f - item['起']
    draw_frac = None
    if item.get('逐帧透明'):  # 量字和标注.py 量出来的每帧透明度（和「画到几成」），起止、淡入淡出、闪都由它定
        a = table_at(item['逐帧透明'], f, '值')
        if a is None:
            return
        a *= item.get('不透明', 1.0)
        if item.get('逐帧画到'):
            v = table_at(item['逐帧画到'], f, '值')
            i = int(f) - int(item['逐帧画到']['起'])
            if v is not None and i < len(item['逐帧画到']['值']):
                draw_frac = max(v, 0.02)
    else:
        if k < 0 or ('止' in item and f >= item['止']):
            return
        a = 1.0
        if item.get('淡入帧'):
            a *= prog(k, 0, item['淡入帧'])
        if '淡出' in item:
            oa, on = item['淡出']
            a *= 1 - prog(f, oa, on)
        a *= blink_alpha(f, item.get('闪'))
        a *= item.get('不透明', 1.0)
    if a <= 0.01:
        return
    if draw_frac is not None:
        key = (id(item), 'frac', round(draw_frac, 2), f if item['种类'] == '黄箭头' else -1)
        if key not in _ann_cache:
            it2 = dict(item); it2['画出帧'] = 1000; it2['_画到'] = draw_frac
            _ann_cache[key] = ann_layer(it2, item['起'])
    else:
        animated = item['种类'] in ('黄箭头', '箭头线') or (item.get('画出帧') and k < item['画出帧'] and not item.get('逐帧透明'))
        key = (id(item), f if animated else -1)
        if key not in _ann_cache:
            it2 = item
            if item.get('逐帧透明') and item.get('画出帧'):
                it2 = dict(item); it2.pop('画出帧')
            _ann_cache[key] = ann_layer(it2, f)
    lay = _ann_cache[key]
    if follow is not None:
        dx, dy = follow
        if dx or dy:
            lay = lay.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, -dy), resample=Image.BILINEAR)
    if cam is not None:
        lay = cam(lay)
    frame_rgba.alpha_composite(mul_alpha(lay, a))


# ---------------------------------------------------------------- 图（排法里的每一张图）和它的进场动作

_asset_cache = {}


def asset(base, name):
    if name not in _asset_cache:
        im = Image.open(os.path.join(base, '素材', name + '.png'))
        im = im.convert('RGBA')
        if OS != 1:
            im = im.resize((im.width // OS, im.height // OS), Image.LANCZOS)  # 原分辨率 → 1080 画面
        _asset_cache[name] = im
    return _asset_cache[name]


def 动作_滑入(item, f, x, y, w, h):
    m = item['进场']
    t = ease(prog(f, m['起'], m['帧']), m.get('缓动', '减速'), m.get('幂', 2.0))  # 第二条上下滑入是「先慢后快再慢」
    side = m['从']
    if side == '左':
        sx, sy = -(x + w), y
    elif side == '右':
        sx, sy = W, y
    elif side == '上':
        sx, sy = x, -(y + h)
    else:
        sx, sy = x, H
    return sx + (x - sx) * t, sy + (y - sy) * t, 1.0


def 动作_淡入(item, f, x, y, w, h):
    m = item['进场']
    return x, y, prog(f, m['起'], m['帧'])


def 动作_挪位置(item, f, x, y, w, h):
    m = item['进场']
    t = ease(prog(f, m['起'], m['帧']), m.get('缓动', '先慢后快再慢'))
    fx, fy = m['从位置']
    return fx + (x - fx) * t, fy + (y - fy) * t, 1.0


def 动作_无(item, f, x, y, w, h):
    return x, y, 1.0


def wipe_mask(w, h, t, side='左', ang=0.0, soft=20):
    """擦出用的遮罩：t=0 全遮住，t=1 全露出。side 是从哪边开始露；ang 让擦的那条边斜着（度）；soft 是边的虚化宽度。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    k = math.tan(math.radians(ang))
    if side in ('左', '右'):
        pos = xx + k * (yy - h / 2)
        span0, span1 = -abs(k) * h / 2 - soft, w + abs(k) * h / 2 + soft
        if side == '右':
            pos = w - pos
    else:
        pos = yy + k * (xx - w / 2)
        span0, span1 = -abs(k) * w / 2 - soft, h + abs(k) * w / 2 + soft
        if side == '下':
            pos = h - pos
    edge = span0 + (span1 - span0) * t
    return np.clip((edge - pos) / max(soft, 1) + 0.5, 0, 1)


def 动作_擦出(item, f, x, y, w, h):
    return x, y, 1.0


ENTER = {'滑入': 动作_滑入, '淡入': 动作_淡入, '挪位置': 动作_挪位置, '无': 动作_无, '光斑': 动作_无, '擦出': 动作_擦出}


def track_state(tr, f):
    """「轨迹」：每帧一行 [帧, x, y, 放大, 倍率, 加亮, 模糊]（量图轨迹.py 量出来的），可以再跟两个数 [歪几度, 拖影像素]（量倾斜.py 量的）。
    返回这一帧的那一行；在轨迹之前返回 None（不画）。轨迹之后不走这里（place_images 照原来的公式画）。"""
    if f < tr[0][0]:
        return None
    i = int(f) - int(tr[0][0])
    if i >= len(tr):
        return tr[-1]
    row = tr[i]
    if row[0] != int(f):  # 轨迹有断档时按帧号找
        row = min(tr, key=lambda r: abs(r[0] - f))
    return row


_track_cache = {}


def motion_blur(im, k, vertical):
    """顺着一个方向（竖或横）拖 k 像素的平均，先乘透明度再拖，边上不发黑。"""
    arr = np.asarray(im.convert('RGBA'), np.float32)
    if vertical:
        arr = arr.transpose(1, 0, 2)
    pm = np.concatenate([arr[..., :3] * arr[..., 3:4] / 255, arr[..., 3:4]], -1)
    pad = np.pad(pm, ((0, 0), (k // 2, k - k // 2), (0, 0)), mode='edge')
    c = np.cumsum(pad, axis=1, dtype=np.float64)
    c = np.concatenate([np.zeros_like(c[:, :1]), c], axis=1)
    out = (c[:, k:k + pm.shape[1]] - c[:, :pm.shape[1]]) / k
    a = out[..., 3:4]
    rgb = np.where(a > 0.5, out[..., :3] * 255 / np.maximum(a, 1e-3), 0)
    res = np.concatenate([rgb, a], -1)
    if vertical:
        res = res.transpose(1, 0, 2)
    return Image.fromarray(np.clip(res, 0, 255).astype(np.uint8), 'RGBA')


def draw_tracked(canvas, base, it, f):
    """照「轨迹」这一帧的数画一张图：放大、亮度（倍率×原色+加亮）、高斯模糊，再贴到 (x, y)。返回 (x, y) 或 None。"""
    row = track_state(it['轨迹'], f)
    if row is None:
        return None
    _, x, y, s, g, c, bl = row[:7]
    ang = row[7] if len(row) > 7 else 0      # 歪几度（逆时针为正，绕图中心），量倾斜.py 量的
    mb = row[8] if len(row) > 8 else 0       # 顺着滑的方向拖影几像素
    if g < it.get('轨迹看不见', 0.15) and abs(c) < 30:
        return None
    vert = it.get('进场', {}).get('从') in ('上', '下') or it.get('拖影方向') == '竖'
    key = (it['素材'], round(s, 4), round(g, 3), round(c, 1), bl, round(ang, 2), round(mb), vert)
    im = _track_cache.get(key)
    if im is None:
        im = asset(base, it['素材'])
        if abs(s - 1) > 1e-4:
            im = im.resize((max(1, int(round(im.width * s))), max(1, int(round(im.height * s)))), Image.BICUBIC)
        if abs(g - 1) > 1e-3 or abs(c) > 0.05:
            arr = np.asarray(im, np.float32).copy()
            arr[..., :3] = arr[..., :3] * g + c
            im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')
        if bl and bl > 0.3:
            im = im.filter(ImageFilter.GaussianBlur(bl))
        if mb and mb >= 2:
            im = motion_blur(im, int(round(mb)), vert)
        if abs(ang) > 0.05:
            w0, h0 = im.size
            im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
            im.info['挪'] = ((w0 - im.width) / 2, (h0 - im.height) / 2)
        if len(_track_cache) > 400:
            _track_cache.clear()
        _track_cache[key] = im
    dx_, dy_ = im.info.get('挪', (0, 0))  # 歪了以后图变大，左上角往外挪，图中心不动
    ix, iy = int(round(x + dx_)), int(round(y + dy_))
    if '裁' in it:  # 只在这个框里露出来（四宫格：图在自己那一格里滑进来）
        cx0, cy0, cx1, cy1 = it['裁']
        sx0, sy0 = max(cx0 - ix, 0), max(cy0 - iy, 0)
        sx1, sy1 = min(cx1 - ix, im.width), min(cy1 - iy, im.height)
        if sx1 <= sx0 or sy1 <= sy0:
            return x, y
        im = im.crop((sx0, sy0, sx1, sy1))
        ix, iy = ix + sx0, iy + sy0
    if ix > -im.width and iy > -im.height and ix < W and iy < H:
        canvas.alpha_composite(im, (ix, iy))
    return x, y


def place_images(base, scr, f, orig, tracked=None):
    """按排法摆图：底色 + 每张图（带自己的进场动作）。返回 RGBA 图层和每张图的位移（给跟着图走的标注用）。
    tracked 是一个列表时，带「轨迹」的图不画在这里，放进列表留给 photo_layer 在镜头之后画（轨迹量的是画面上的实际位置）。"""
    bg = tuple(scr.get('底色', [3, 3, 3]))
    canvas = Image.new('RGBA', (W, H), bg + (255,))
    offsets = {}
    for it in scr.get('图', []):
        if it.get('轨迹') and f <= it['轨迹'][-1][0]:  # 轨迹之后照原来的「位置 + 进场 + 镜头」画
            if tracked is not None:
                tracked.append(it)
            else:
                p = draw_tracked(canvas, base, it, f)
                if p:
                    rx, ry = it.get('标注参照', it['位置'])
                    offsets[it.get('名', it['素材'])] = (p[0] - rx, p[1] - ry)
            continue
        if it.get('片段'):
            a_, b_ = it['片段']  # 原片里的起止帧（包含 a_，不含 b_）
            n = min(max(f, a_), b_ - 1)
            im = Image.fromarray(orig.get(n)).convert('RGBA')
            if it.get('压暗'):  # 第二条第 38 屏：全屏真人原地压暗，[起帧, 帧数, 暗到几成]
                a0, an, lv = it['压暗']
                k_ = 1 - (1 - lv) * prog(f, a0, an)
                if k_ < 0.999:
                    im = Image.fromarray((np.asarray(im, np.float32) * [k_, k_, k_, 1]).astype(np.uint8), 'RGBA')
            canvas.alpha_composite(im)
            continue
        im = asset(base, it['素材'])
        x, y = it['位置']
        w, h = im.size
        fn = ENTER[it.get('进场', {}).get('法', '无')]
        nx, ny, a = fn(it, f, x, y, w, h)
        if '同时淡入' in it.get('进场', {}):  # 边挪边淡入
            a *= prog(f, *it['进场']['同时淡入'])
        if it.get('压暗'):  # 第二条第 38 屏：定格的那一帧压暗，[起帧, 帧数, 暗到几成]
            a0, an, lv = it['压暗']
            k_ = 1 - (1 - lv) * prog(f, a0, an)
            if k_ < 0.999:
                im = Image.fromarray((np.asarray(im, np.float32) * [k_, k_, k_, 1]).astype(np.uint8), 'RGBA')
        if it.get('闪'):  # 第二条第 6 屏：贴图（弯的黄箭头）一亮一灭
            a *= blink_alpha(f, it['闪'])
        if it.get('止') is not None and f >= it['止']:
            a = 0
        rx, ry = it.get('标注参照', it['位置'])
        offsets[it.get('名', it['素材'])] = (nx - rx, ny - ry)
        if a <= 0.01:
            continue
        im2 = mul_alpha(im, a) if a < 0.999 else im
        if it.get('进场', {}).get('法') == '光斑':
            m = it['进场']
            t = prog(f, m['起'], m['帧'])
            if t < 1:
                r_ = m.get('最大模糊', 40) * (1 - t) ** 1.5
                rgb = im2.filter(ImageFilter.GaussianBlur(r_)) if r_ > 0.3 else im2
                arr = np.asarray(rgb, np.float32)
                k_ = m.get('发白', 0.8) * (1 - t)
                arr[..., :3] = arr[..., :3] + (255 - arr[..., :3]) * k_
                if m.get('显出', True):
                    arr[..., 3] = np.maximum(arr[..., 3], 0)
                im2 = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')
        if it.get('进场', {}).get('法') == '擦出':  # 第二条第 25 屏：图表一行行从左往右擦出来
            m = it['进场']
            t = ease(prog(f, m['起'], m['帧']), m.get('缓动', '线性'))
            if t < 1:
                wm = wipe_mask(w, h, t, m.get('从', '左'), m.get('斜', 0), m.get('虚边', 20))
                arr = np.asarray(im2, np.float32).copy()
                arr[..., 3] *= wm
                im2 = Image.fromarray(arr.astype(np.uint8), 'RGBA')
        canvas.alpha_composite(im2, (int(round(nx)), int(round(ny))))
    return canvas, offsets


def cam_state(c, f):
    """镜头在第 f 帧的状态 (放大倍数, 横移, 竖移)：画面上的点 = 倍数 × 原点 + 移动。
    两种写法：匀速缓推（每帧放大、中心、参考帧、起）；或关键帧 [[帧, 倍数, 横移, 竖移], ...]，中间线性插。"""
    if '关键帧' in c:
        ks = c['关键帧']
        if f <= ks[0][0]:
            return tuple(ks[0][1:])
        for (f0, *a), (f1, *b) in zip(ks[:-1], ks[1:]):
            if f0 <= f <= f1:
                t = (f - f0) / (f1 - f0)
                return tuple(x + (y - x) * t for x, y in zip(a, b))
        (f0, *a), (f1, *b) = ks[-2], ks[-1]
        t = (f - f0) / (f1 - f0)
        return tuple(x + (y - x) * t for x, y in zip(a, b))
    r = c['每帧放大']
    s = 1 + r * (max(f, c.get('起', -10 ** 9)) - c['参考帧'])
    cx, cy = c['中心']
    return s, cx * (1 - s), cy * (1 - s)


def camera_fn(scr, f):
    """镜头缓推：每屏一个，图和标注一起推，大字不推。
    返回 apply(图层, 参考帧)：把在「参考帧」那一刻的画面变到第 f 帧的样子。"""
    c = scr.get('镜头')
    if not c:
        return None
    s1, tx1, ty1 = cam_state(c, f)

    def apply(lay, ref_frame=None):
        rf = c['参考帧'] if ref_frame is None else ref_frame
        s0, tx0, ty0 = cam_state(c, rf)
        s = s1 / s0
        tx, ty = tx1 - s * tx0, ty1 - s * ty0
        if abs(s - 1) < 1e-4 and abs(tx) < 0.05 and abs(ty) < 0.05:
            return lay
        return lay.transform((W, H), Image.AFFINE, (1 / s, 0, -tx / s, 0, 1 / s, -ty / s),
                             resample=Image.BICUBIC)
    return apply


def photo_layer(base, scr, f, orig):
    tracked = []
    lay, offs = place_images(base, scr, f, orig, tracked)
    cam = camera_fn(scr, f)
    if cam is not None:
        # 素材是在「素材帧」那一刻截的，图层按那一刻的镜头算
        lay = cam(lay, scr['镜头'].get('素材帧', scr['镜头']['参考帧']))
    for it in tracked:  # 带「轨迹」的图：量的就是画面上的实际位置，不再过镜头
        p = draw_tracked(lay, base, it, f)
        if p:
            rx, ry = it.get('标注参照', it['位置'])
            offs[it.get('名', it['素材'])] = (p[0] - rx, p[1] - ry)
    return lay, offs, cam


def overlay_layer(base, scr, f, offs, cam, style):
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    for an in scr.get('标注', []):
        fol = offs.get(an['跟着图']) if an.get('跟着图') else None
        draw_ann(ov, an, f, cam, fol)
    for tx in scr.get('大字', []):
        draw_text(ov, tx, f, style)
    return ov


# ---------------------------------------------------------------- 换屏进法（只作用在图这一层）


def blur(im, r):
    if r < 0.3:
        return im
    return im.filter(ImageFilter.GaussianBlur(r))


def motion_blur(im, r, ang=0):
    """横向（或带角度）拖影：几份平移叠加。"""
    if r < 1:
        return im
    n = 9
    acc = np.zeros((H, W, 3), np.float32)
    rgb = im.convert('RGB')
    for i in range(n):
        o = (i / (n - 1) - 0.5) * r
        dx, dy = o * math.cos(math.radians(ang)), o * math.sin(math.radians(ang))
        acc += np.asarray(rgb.transform((W, H), Image.AFFINE, (1, 0, dx, 0, 1, dy), resample=Image.BILINEAR),
                          np.float32)
    return Image.fromarray((acc / n).astype(np.uint8)).convert('RGBA')


def burn(im, k):
    """闪白：亮的地方先爆白。k 0→1。"""
    if k <= 0:
        return im
    a = np.asarray(im.convert('RGB'), np.float32)
    a = a * (1 + 6 * k) + 150 * k
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert('RGBA')


def whiten(im, k):
    if k <= 0:
        return im
    a = np.asarray(im.convert('RGB'), np.float32)
    a = a + (255 - a) * k
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert('RGBA')


def 进法_直接切(old, new, k, p):
    return new


def 进法_发白模糊(old, new, k, p):
    rs = p.get('模糊', [40, 14, 4])
    ws = p.get('发白', [0.45, 0.2, 0.05])
    if k >= len(rs):
        return new
    return whiten(blur(new, rs[k]), ws[k])


def 进法_叠化(old, new, k, p):
    t = prog(k, 0, p['帧'])
    return Image.blend(old, new, t)


def 进法_横推(old, new, k, p):
    t = ease(prog(k + 1, 0, p['帧']), '先慢后快再慢')
    off = int(round(W * t))
    out = Image.new('RGBA', (W, H), (0, 0, 0, 255))
    out.alpha_composite(old, (-off, 0)) if off < W else None
    out.alpha_composite(new, (W - off, 0))
    return out


def settle_zoom(im, k, p):
    """进场时从放大的样子回落到原大：参数 起始放大、回落帧。"""
    s0 = p.get('起始放大')
    if not s0:
        return im
    t = ease(prog(k, 0, p.get('回落帧', 10)), '减速', p.get('回落幂', 2))
    s = s0 + (1 - s0) * t
    if abs(s - 1) < 1e-4:
        return im
    cx, cy = p.get('回落中心', [W / 2, H / 2])
    return im.transform((W, H), Image.AFFINE, (1 / s, 0, cx - cx / s, 0, 1 / s, cy - cy / s),
                        resample=Image.BICUBIC)


def 进法_甩动模糊(old, new, k, p):
    if k >= 0:
        new = settle_zoom(new, k, p)
    return _甩动模糊(old, new, k, p)


def _甩动模糊(old, new, k, p):
    pre = p.get('前', 3)
    rs_old = p.get('旧拖影', [30, 90, 200])  # k = -pre .. -1
    rs_new = p.get('新拖影', [220, 110, 40, 10])  # k = 0 ..
    ang = p.get('角度', 0)
    if k < 0:
        return motion_blur(old, rs_old[k + pre], ang)
    if k < len(rs_new):
        im = new
        if p.get('滑'):
            dx0, dy0 = p['滑']
            t = ease(prog(k, 0, p.get('滑帧', 10)), '减速', 2)
            im = im.transform((W, H), Image.AFFINE, (1, 0, -dx0 * (1 - t), 0, 1, -dy0 * (1 - t)),
                              resample=Image.BILINEAR)
        return motion_blur(im, rs_new[k], ang)
    if p.get('滑'):
        dx0, dy0 = p['滑']
        t = ease(prog(k, 0, p.get('滑帧', 10)), '减速', 2)
        return new.transform((W, H), Image.AFFINE, (1, 0, -dx0 * (1 - t), 0, 1, -dy0 * (1 - t)),
                             resample=Image.BILINEAR)
    return new


def 进法_闪白(old, new, k, p):
    pre = p.get('前', 3)
    ko = p.get('旧', [0.15, 0.5, 1.0])   # k=-pre..-1
    kn = p.get('新', [0.9, 0.35, 0.12])  # k=0..
    if k < 0:
        return burn(old, ko[k + pre])
    if k < len(kn):
        return burn(new, kn[k])
    return new


def 进法_对焦模糊(old, new, k, p):
    n = p['帧']
    new = settle_zoom(new, k, p)
    if k >= n:
        return new
    r = p.get('最大模糊', 28) * (1 - k / n) ** p.get('幂', 1.5)
    im = blur(new, r)
    mb = p.get('拖影', [])
    if k < len(mb):
        im = motion_blur(im, mb[k], p.get('角度', 0))
    return im


def 进法_光斑扫过(old, new, k, p):
    """第二条第 49 屏：一道斜的亮光从一边扫到另一边，光过去的地方换成新画面。"""
    n = p.get('帧', 6)
    t = (k + 1) / n
    if t >= 1 + p.get('宽', 0.35):
        return new
    m = wipe_mask(W // 4, H // 4, min(t, 1), p.get('从', '左'), p.get('斜', 30), p.get('虚边', 30) / 4)
    mm = Image.fromarray((m * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
    out = Image.composite(new, old, mm)
    # 光带：在交界那一圈加亮
    band = np.asarray(mm, np.float32) / 255
    glow = np.clip(1 - np.abs(band - 0.5) * 2, 0, 1) ** 0.6 * p.get('亮', 0.9) * max(0.0, 1 - max(t - 1, 0) / p.get('宽', 0.35))
    arr = np.asarray(out, np.float32)
    col = np.array(p.get('色', [255, 255, 255]), np.float32)  # 原片的光斑偏暖黄
    arr[..., :3] = arr[..., :3] + (col - arr[..., :3]) * glow[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')


def 进法_卷开(old, new, k, p):
    """第二条第 62 屏：像翻书页一样从左边卷开。新画面从左往右露出，卷起来的纸背是一条渐变的浅色带。"""
    n = p.get('帧', 13)
    t = ease(prog(k + 1, 0, n), p.get('缓动', '先慢后快再慢'))
    if t >= 1:
        return new
    edge = int(W * t)
    out = old.copy() if old is not None else Image.new('RGBA', (W, H), (0, 0, 0, 255))
    if edge > 0:
        out.alpha_composite(new.crop((0, 0, edge, H)), (0, 0))
    bw = int(p.get('纸背宽', 160) * math.sin(math.pi * t))  # 卷到一半时纸背最宽
    if bw > 2:
        x0 = min(edge, W - 1)
        g = np.linspace(1, 0.35, bw)[None, :, None] * np.array(p.get('纸背色', [235, 235, 235]))
        strip = np.repeat(g, H, axis=0).astype(np.uint8)
        sh = Image.fromarray(strip, 'RGB').convert('RGBA')
        out.alpha_composite(sh.crop((0, 0, max(min(bw, W - x0), 1), H)), (x0, 0))
    return out


def zoom_blur(im, s, r):
    """以画面中心放大 s 倍、再高斯模糊 r（1080 坐标）。"""
    if s != 1.0:
        cw, ch = W / s, H / s
        im = im.crop(((W - cw) / 2, (H - ch) / 2, (W + cw) / 2, (H + ch) / 2)).resize((W, H), Image.BICUBIC)
    return blur(im, r)


def 进法_逐帧合成(old, new, k, p):
    """拟合过渡.py 量出来的每帧一组数：画面 = 新占×新(放大,模糊) + 旧占×旧(放大,模糊) + 加亮。
    行里有「移」的是横推：新画面左边挪到 x=移，旧画面贴在它左边。逐帧第一行是换屏前第「前」帧。"""
    rows = p['逐帧']
    i = k + p.get('前', 0)
    if i < 0 or i >= len(rows):
        return new
    r = rows[i]
    sn, rn, an = r['新']
    so, ro, ao = r['旧']
    if '移' in r:  # 横推：新画面左边在 x=移，旧画面紧贴在它左边（x=移-W），两层各自乘自己的占
        dx = int(round(r['移']))
        canvas = np.zeros((H, W, 3), np.float32)
        nw = np.asarray(zoom_blur(new.convert('RGB'), sn, rn), np.float32) * an
        x0, x1 = max(dx, 0), min(dx + W, W)
        if x1 > x0:
            canvas[:, x0:x1] = nw[:, x0 - dx:x1 - dx]
        if old is not None:
            ow = np.asarray(zoom_blur(old.convert('RGB'), so, ro), np.float32) * ao
            ox = dx - W
            x0, x1 = max(ox, 0), min(ox + W, W)
            if x1 > x0:
                canvas[:, x0:x1] = ow[:, x0 - ox:x1 - ox]
        acc = canvas
    else:
        acc = np.asarray(zoom_blur(new.convert('RGB'), sn, rn), np.float32) * an
        if old is not None and abs(ao) > 1e-3:
            acc += np.asarray(zoom_blur(old.convert('RGB'), so, ro), np.float32) * ao
    acc += r.get('加', 0)
    return Image.fromarray(np.clip(acc, 0, 255).astype(np.uint8)).convert('RGBA')


TRANS = {'逐帧合成': 进法_逐帧合成, '直接切': 进法_直接切, '光斑扫过': 进法_光斑扫过, '卷开': 进法_卷开, '发白模糊': 进法_发白模糊, '叠化': 进法_叠化, '横推': 进法_横推,
         '甩动模糊': 进法_甩动模糊, '闪白': 进法_闪白, '对焦模糊': 进法_对焦模糊, '切黑滑入': 进法_直接切}


# ---------------------------------------------------------------- 全局轨道：小人、字幕


def draw_pip(frame, data, f, orig, under=False):
    for seg in data.get('小人', []):
        if bool(seg.get('在字下面')) != under:
            continue
        if seg['起'] <= f < seg['止']:
            x0, y0, x1, y1 = seg['框']
            n = f
            src = orig.get(n)
            patch = Image.fromarray(src[y0:y1, x0:x1]).convert('RGBA')
            a = 1.0
            if '淡出' in seg:
                a = 1 - prog(f, seg['淡出'][0], seg['淡出'][1])
            if a > 0.01:
                frame.alpha_composite(mul_alpha(patch, a), (x0, y0))


def draw_subs(frame, data, f):
    for s in data.get('字幕', []):
        if s['起'] <= f < s['止']:
            st = dict(data['样式']['字幕'])
            st.update(s.get('样式', {}))
            F = font(st['字体'], st['字号'], st.get('字体序号', 0))
            lay = Image.new('RGBA', (W, 160), (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            tw = d.textlength(s['字'], font=F)
            cx = s.get('x', W / 2)
            d.text((cx - tw / 2, 80), s['字'], font=F, fill=tuple(st.get('颜色', [255, 255, 255])) + (255,), anchor='lm',
                   stroke_width=st.get('描边', 3), stroke_fill=(20, 20, 20, 255))
            frame.alpha_composite(lay, (0, int(s['y'] - 80)))


# ---------------------------------------------------------------- 一帧


def screen_of(data, f):
    for i, s in enumerate(data['屏']):
        if s['起'] <= f < s['止']:
            return i
    return len(data['屏']) - 1


def render_frame(base, data, f, orig):
    scrs = data['屏']
    style = data['样式']['大字']
    i = screen_of(data, f)
    cur = scrs[i]
    # 下一屏的进法如果要提前几帧开始（甩动、闪白），这几帧算下一屏的换屏
    if i + 1 < len(scrs):
        nx = scrs[i + 1]
        pre = nx.get('进法', {}).get('参数', {}).get('前', 0)
        if pre and f >= nx['起'] - pre:
            i, cur = i + 1, nx
    k = f - cur['起']
    tr = cur.get('进法', {'法': '直接切'})
    fn = TRANS[tr['法']]
    p = tr.get('参数', {})
    new_ph, offs, cam = photo_layer(base, cur, max(f, cur['起']), orig)
    old_ph = None
    in_trans = i > 0 and tr['法'] not in ('直接切', '切黑滑入') and (k < p.get('后', 12))
    if in_trans:
        prev = scrs[i - 1]
        old_ph, _, _ = photo_layer(base, prev, min(f, prev['止'] - 1), orig)
        if p.get('从底色'):  # 先切到黑（或白）再进，[r,g,b]
            old_ph = Image.new('RGBA', (W, H), tuple(p['从底色']) + (255,))
        ph = fn(old_ph, new_ph, k, p)
    elif tr['法'] in ('发白模糊', '逐帧合成') and k < p.get('后', 12):
        ph = fn(None, new_ph, k, p)
    else:
        ph = new_ph
    frame = ph.copy()
    # 屏内叠的东西：换屏的前几帧，旧屏的大字和标注可以留几帧
    draw_pip(frame, data, f, orig, under=True)  # 上图下人那种，博主在大字下面
    keep_old = tr.get('旧字留到', None)
    if k < 0 or (keep_old is not None and k < keep_old):
        prev = scrs[i - 1]
        _, poffs, pcam = photo_layer(base, prev, min(f, prev['止'] - 1), orig)
        frame.alpha_composite(overlay_layer(base, prev, min(f, prev['止'] - 1), poffs, pcam, style))
    if k >= 0:
        frame.alpha_composite(overlay_layer(base, cur, f, offs, cam, style))
    draw_pip(frame, data, f, orig)
    draw_subs(frame, data, f)
    return frame.convert('RGB')


# ---------------------------------------------------------------- 命令


def seg_path(data, sid):
    return os.path.join(data['视频']['临时目录'], '分屏', f'屏{sid:02d}.mp4')


def render_screen(base, data, scr):
    a, b = scr['起'], scr['止']
    os.makedirs(os.path.dirname(seg_path(data, scr['号'])), exist_ok=True)
    orig = Orig(data['视频']['原片'], max(a - 12, 0))
    out = seg_path(data, scr['号'])
    pr = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                           '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'fast',
                           '-crf', '16',
                           '-vf', 'scale=flags=accurate_rnd+full_chroma_int:in_range=pc:out_range=tv:out_color_matrix=bt709',
                           '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                           '-color_range', 'tv', out], stdin=subprocess.PIPE)
    for f in range(a, b):
        fr = render_frame(base, data, f, orig)
        pr.stdin.write(fr.tobytes())
        orig.prune(f - 14)
    pr.stdin.close(); pr.wait(); orig.close()
    _ann_cache.clear()
    print(f"屏 {scr['号']}：第 {a} 到 {b - 1} 帧 → {out}")


def cmd_render(data, base, which=None):
    for scr in data['屏']:
        if which and scr['号'] not in which:
            continue
        render_screen(base, data, scr)


def cmd_concat(data, base, upto=None, outname=None):
    tmp = data['视频']['临时目录']
    lst = os.path.join(tmp, '分屏', '清单.txt')
    scrs = [s for s in data['屏'] if upto is None or s['号'] <= upto]
    with open(lst, 'w') as fh:
        for scr in scrs:
            fh.write(f"file '{seg_path(data, scr['号'])}'\n")
    v = data['视频']
    n = scrs[-1]['止'] - v['起']
    out = os.path.join(base, outname or v['成片'])
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst,
                    '-ss', f"{v['起'] / FPS:.4f}", '-t', f'{n / FPS:.4f}', '-i', v['原片'],
                    '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-t', f'{n / FPS:.4f}', out], check=True)
    print('成片', out)


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    data = json.load(open(path))
    global OS
    OS = data['视频'].get('原片倍数', 2)
    base = os.path.dirname(os.path.abspath(path))
    if cmd == '取素材':
        cmd_take_assets(data, base, sys.argv[3].split(',') if len(sys.argv) > 3 else None)
    elif cmd == '渲染':
        which = [int(x) for x in sys.argv[3].split(',')] if len(sys.argv) > 3 else None
        cmd_render(data, base, which)
    elif cmd == '合成':  # 合成 json [到第几屏 输出文件名]
        cmd_concat(data, base, int(sys.argv[3]) if len(sys.argv) > 3 else None, sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == '单帧':
        f = int(sys.argv[3])
        orig = Orig(data['视频']['原片'], max(f - 12, 0))
        render_frame(base, data, f, orig).save(sys.argv[4])
        orig.close()


if __name__ == '__main__':
    main()
