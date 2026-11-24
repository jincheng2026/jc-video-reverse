# -*- coding: utf-8 -*-
"""几个脚本共用的小工具：读视频信息、按帧读亮度、往图上写字。"""
import json, subprocess, sys

SCRIPT_INTERFACE = "internal-module"  # 只给本文件夹里的其他脚本 import，不单独跑
import numpy as np

import os
_FONTS = [os.environ.get('VIDEO_REVERSE_FONT', ''),
          '/System/Library/Fonts/STHeiti Medium.ttc', '/System/Library/Fonts/PingFang.ttc',          # macOS
          'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',                                # Windows
          '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']                                           # Linux
FONT = next((p for p in _FONTS if p and os.path.exists(p)), _FONTS[1])   # 图上写中文用的字体；找不到时设环境变量 VIDEO_REVERSE_FONT


def info(path):
    """返回 宽、高、帧率（小数）、帧数（估的，按时长乘帧率）、时长秒、有没有声音。"""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
                       stdout=subprocess.PIPE, check=True)
    d = json.loads(r.stdout)
    v = next(s for s in d['streams'] if s['codec_type'] == 'video')
    a, b = v['r_frame_rate'].split('/')
    fps = float(a) / float(b)
    dur = float(v.get('duration') or d['format']['duration'])
    w, h = int(v['width']), int(v['height'])
    rot = 0
    for sd in v.get('side_data_list', []):
        rot = int(sd.get('rotation', rot) or rot)
    if abs(rot) in (90, 270):
        w, h = h, w
    n = int(v.get('nb_frames') or round(dur * fps))
    return dict(w=w, h=h, fps=fps, n=n, dur=dur, audio=any(s['codec_type'] == 'audio' for s in d['streams']),
                avg=v.get('avg_frame_rate'), r=v.get('r_frame_rate'))


def luma_frames(path, sw, sh, skip=0, limit=None):
    """一帧一帧吐亮度（0 到 255 的小数数组，高 sh 宽 sw）。
    只缩小、不做颜色换算，直接取视频里存的亮度层再拉伸。这样这台电脑和另一台电脑量出来逐字节一样
    （两台的 ffmpeg 版本不同，让 ffmpeg 自己转灰度会差 2.3 级，2026-11-23 踩过）。
    skip：先丢掉前面多少帧。从头解码再丢，慢一点，但帧号一定对。"""
    cmd = ['ffmpeg', '-loglevel', 'error', '-i', path, '-map', '0:v:0', '-fps_mode', 'passthrough',
           '-vf', f'scale={sw}:{sh}:flags=area', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=sw * sh * 12)  # 读够了会提前关掉，ffmpeg 的抱怨不用看
    n = sw * sh * 3 // 2
    i = 0
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                return
            if i >= skip:
                y = np.frombuffer(buf, np.uint8, count=sw * sh).reshape(sh, sw).astype(np.float32)
                yield np.clip((y - 16.0) * (255.0 / 219.0), 0, 255)
                if limit is not None and i - skip + 1 >= limit:
                    return
            i += 1
    finally:
        p.stdout.close()
        p.kill()


def rgb_frames_at(path, start, count, w, h, fps, crop=None):
    """从第 start 帧起连着取 count 帧的彩色小图，只解这一小段，轻。crop 是 (x0,y0,x1,y1)，0 到 1 的比例。
    取法：先粗跳到 3 秒前，保留片子原来的时间，再按时间挑「第 start 帧及以后」。
    不要改回「-ss (start-0.5)/fps」那种写法：2026-11-23 实测它在长片子上会早一帧、在短片段上又不早，
    上下两排各取各的就会错开一帧，看着像重做片「早出了一帧」。"""
    vf = [f"select='gte(t,{(start - 0.5) / fps:.6f})'"]
    if crop:
        x0, y0, x1, y1 = crop
        vf.append(f'crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}')
    vf.append(f'scale={w}:{h}:flags=area')
    cmd = ['ffmpeg', '-loglevel', 'error', '-ss', f'{max(start / fps - 3, 0):.4f}', '-copyts', '-i', path, '-map', '0:v:0',
           '-vf', ','.join(vf), '-frames:v', str(count), '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    raw = subprocess.run(cmd, stdout=subprocess.PIPE).stdout
    return np.frombuffer(raw, np.uint8)[: (len(raw) // (w * h * 3)) * w * h * 3].reshape(-1, h, w, 3)


def font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT, size)
    except Exception:
        if not getattr(font, 'warned', False):
            print('没找到中文字体，图上的中文会变成方框。设环境变量 VIDEO_REVERSE_FONT 指向一个中文字体文件。', file=sys.stderr)
            font.warned = True
        return ImageFont.load_default()


def segs(mask, gap=2):
    """把连续为真的帧并成一段段，中间隔不超过 gap 帧的并到一起。返回 [(起, 止不含)]。"""
    out, s, last = [], None, None
    for i, m in enumerate(mask):
        if m:
            if s is None:
                s = i
            elif i - last > gap + 1:
                out.append((s, last + 1)); s = i
            last = i
    if s is not None:
        out.append((s, last + 1))
    return out


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)
