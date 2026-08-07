# -*- coding: utf-8 -*-
"""
Berserker logo generator v6 — renders from assets/log.svg.

v6 change: the source of truth is now the hand-authored vector icon
``berserker/assets/log.svg`` (dark indigo rounded tile + orange-gold rune ᛒ +
cyan sparks). This script parses that SVG (linear/radial gradients, polygon
paths, feGaussianBlur glow) with PIL + numpy — no external SVG library — and
emits the raster/ICO assets consumed by the app:

  assets/logo.png            512x512  display icon
  assets/logo-{16,32,48,64,128,256}.png
  assets/logo.ico            multi-size ICO (PNG-compressed frames)

Run:  python generate_logo.py [out_dir]
"""
import io
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SVG_SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'berserker', 'assets', 'log.svg')
SIZE = 1000  # render canvas (log.svg viewBox)

_PATH_TOK = re.compile(r'[MLZzmlz]|-?\d+(?:\.\d+)?')


# ---------------------------------------------------------------------------
# SVG parsing
# ---------------------------------------------------------------------------

def parse_color(s, default=(0, 0, 0, 255)):
    s = (s or '').strip()
    if not s or s == 'none':
        return None
    if s.startswith('#'):
        h = s[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return default


def parse_stops(el):
    stops = []
    for stop in el:
        if stop.tag.rsplit('}', 1)[-1] != 'stop':
            continue
        off = stop.get('offset', '0').strip()
        if off.endswith('%'):
            off = float(off.rstrip('%')) / 100.0
        else:
            off = float(off)  # SVG default unit is 0..1, NOT 0..100
        c = parse_color(stop.get('stop-color'), (0, 0, 0, 255))
        op = stop.get('stop-opacity')
        alpha = int(float(op) * 255) if op else 255
        stops.append((off, (c[0], c[1], c[2], alpha)))
    return stops


def parse_path_d(d):
    """Tokenize SVG path data (absolute M/L/Z, straight-line segments)."""
    toks = _PATH_TOK.findall(d or '')
    pts = []
    cmd = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in 'MLZz':
            cmd = t
            i += 1
            continue
        if cmd in ('M', 'L') and i + 1 < len(toks):
            pts.append((float(toks[i]), float(toks[i + 1])))
            i += 2
        else:
            break
    return pts


def url_id(ref):
    """Resolve 'url(#id)' -> 'id' (prefix-aware, unlike str.lstrip)."""
    if ref and ref.startswith('url('):
        inner = ref[4:].rstrip(')')
        if inner.startswith('#'):
            inner = inner[1:]
        return inner
    return ref


# ---------------------------------------------------------------------------
# Gradient rasterisation
# ---------------------------------------------------------------------------

def linear_map(size, p0, p1, stops):
    h, w = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        denom = 1.0
    t = ((xx - p0[0]) * dx + (yy - p0[1]) * dy) / denom
    offs = np.array([s[0] for s in stops])
    cols = np.array([[s[1][0], s[1][1], s[1][2], s[1][3]] for s in stops], dtype=np.float64)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    for ch in range(4):
        out[:, :, ch] = np.interp(t, offs, cols[:, ch]).astype(np.uint8)
    return out


def radial_map(size, center, radius, stops):
    h, w = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    d = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / max(radius, 1e-6)
    offs = np.array([s[0] for s in stops])
    cols = np.array([[s[1][0], s[1][1], s[1][2], s[1][3]] for s in stops], dtype=np.float64)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    for ch in range(4):
        out[:, :, ch] = np.interp(d, offs, cols[:, ch]).astype(np.uint8)
    return out


def apply_alpha(img, opacity):
    a = img.split()[3].point(lambda v: int(v * opacity))
    img.putalpha(a)
    return img


# ---------------------------------------------------------------------------
# Icon assembly
# ---------------------------------------------------------------------------

def render_icon(svg_path, size=SIZE):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = {'s': 'http://www.w3.org/2000/svg'}
    defs = root.find('s:defs', ns)

    gradients = {}
    filters = {}
    for g in defs:
        tag = g.tag.rsplit('}', 1)[-1]
        gid = g.get('id')
        if tag == 'linearGradient':
            gradients[gid] = {
                'type': 'linear',
                'x1': float(g.get('x1', '0')), 'y1': float(g.get('y1', '0')),
                'x2': float(g.get('x2', '1')), 'y2': float(g.get('y2', '1')),
                'stops': parse_stops(g),
            }
        elif tag == 'radialGradient':
            gradients[gid] = {
                'type': 'radial',
                'cx': float(g.get('cx', '0.5')), 'cy': float(g.get('cy', '0.5')),
                'r': float(g.get('r', '0.5')),
                'stops': parse_stops(g),
            }
        elif tag == 'filter':
            blur = g.find('.//s:feGaussianBlur', ns)
            if blur is not None:
                filters[gid] = float(blur.get('stdDeviation', '0'))

    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))

    def render_element(el):
        tag = el.tag.rsplit('}', 1)[-1]
        fill = el.get('fill')
        opacity = el.get('opacity')
        fill_opacity = el.get('fill-opacity')
        eff_opacity = float(opacity) if opacity else (float(fill_opacity) if fill_opacity else 1.0)
        ffilter = el.get('filter')
        blur_std = filters.get(url_id(ffilter)) if ffilter else None

        layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)

        if tag == 'rect':
            x = float(el.get('x', '0'))
            y = float(el.get('y', '0'))
            w = float(el.get('width', '0'))
            h = float(el.get('height', '0'))
            rx = float(el.get('rx', '0'))
            if fill and fill.startswith('url('):
                g = gradients[url_id(fill)]
                if g['type'] == 'linear':
                    p0 = (x + g['x1'] * w, y + g['y1'] * h)
                    p1 = (x + g['x2'] * w, y + g['y2'] * h)
                    grad = Image.fromarray(linear_map((size, size), p0, p1, g['stops']))
                else:
                    c = (x + g['cx'] * w, y + g['cy'] * h)
                    r = g['r'] * max(w, h)
                    grad = Image.fromarray(radial_map((size, size), c, r, g['stops']))
                mask = Image.new('L', (size, size), 0)
                if rx:
                    ImageDraw.Draw(mask).rounded_rectangle([x, y, x + w, y + h], radius=rx, fill=255)
                else:
                    ImageDraw.Draw(mask).rectangle([x, y, x + w, y + h], fill=255)
                layer = Image.composite(grad, layer, mask)
            else:
                col = parse_color(fill)
                if col:
                    if rx:
                        ld.rounded_rectangle([x, y, x + w, y + h], radius=rx, fill=col)
                    else:
                        ld.rectangle([x, y, x + w, y + h], fill=col)

        elif tag == 'ellipse':
            cx = float(el.get('cx', '0'))
            cy = float(el.get('cy', '0'))
            rx = float(el.get('rx', '0'))
            ry = float(el.get('ry', '0'))
            if fill and fill.startswith('url('):
                g = gradients[url_id(fill)]
                if g['type'] == 'radial':
                    c = (cx + (g['cx'] - 0.5) * 2 * rx, cy + (g['cy'] - 0.5) * 2 * ry)
                    r = g['r'] * 2 * max(rx, ry)
                    grad = Image.fromarray(radial_map((size, size), c, r, g['stops']))
                else:
                    grad = Image.fromarray(linear_map(
                        (size, size),
                        (cx - rx + g['x1'] * 2 * rx, cy - ry + g['y1'] * 2 * ry),
                        (cx - rx + g['x2'] * 2 * rx, cy - ry + g['y2'] * 2 * ry),
                        g['stops']))
                mask = Image.new('L', (size, size), 0)
                ImageDraw.Draw(mask).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
                layer = Image.composite(grad, layer, mask)
            else:
                col = parse_color(fill)
                if col:
                    ld.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=col)

        elif tag in ('path', 'polygon'):
            dattr = el.get('d') if tag == 'path' else el.get('points')
            pts = parse_path_d(dattr)
            if not pts:
                return
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            if fill and fill.startswith('url('):
                g = gradients[url_id(fill)]
                bw = bbox[2] - bbox[0]
                bh = bbox[3] - bbox[1]
                if g['type'] == 'linear':
                    p0 = (bbox[0] + g['x1'] * bw, bbox[1] + g['y1'] * bh)
                    p1 = (bbox[0] + g['x2'] * bw, bbox[1] + g['y2'] * bh)
                    grad = Image.fromarray(linear_map((size, size), p0, p1, g['stops']))
                else:
                    c = (bbox[0] + g['cx'] * bw, bbox[1] + g['cy'] * bh)
                    r = g['r'] * max(bw, bh)
                    grad = Image.fromarray(radial_map((size, size), c, r, g['stops']))
                mask = Image.new('L', (size, size), 0)
                ImageDraw.Draw(mask).polygon(pts, fill=255)
                layer = Image.composite(grad, layer, mask)
            else:
                col = parse_color(fill)
                if col:
                    ld.polygon(pts, fill=col)

        if eff_opacity != 1.0:
            layer = apply_alpha(layer, eff_opacity)
        if blur_std and blur_std > 0:
            blurred = layer.filter(ImageFilter.GaussianBlur(blur_std))
            layer = Image.alpha_composite(blurred, layer)
        canvas.alpha_composite(layer)

    for el in root.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag in ('rect', 'ellipse', 'path', 'polygon'):
            render_element(el)

    return canvas


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def build_ico(frames, path):
    entries, blobs = [], []
    offset = 6 + 16 * len(frames)
    for im in frames:
        im = im.convert('RGBA')
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        blob = buf.getvalue()
        w, h = im.size
        entries.append(struct.pack('<BBBBHHII', w % 256, h % 256, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    with open(path, 'wb') as f:
        f.write(struct.pack('<HHH', 0, 1, len(frames)))
        for e in entries:
            f.write(e)
        for b in blobs:
            f.write(b)
    print('ico ->', path)


def save_all(svg_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    big = render_icon(svg_path)
    icon = big.resize((512, 512), Image.LANCZOS)
    icon.save(os.path.join(out_dir, 'logo.png'), 'PNG')
    print('png ->', os.path.join(out_dir, 'logo.png'))
    for size in (16, 32, 48, 64, 128, 256):
        icon.resize((size, size), Image.LANCZOS).save(
            os.path.join(out_dir, 'logo-%d.png' % size), 'PNG')
    print('sizes -> logo-{16..256}.png')
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    build_ico([icon.resize(s, Image.LANCZOS) for s in sizes],
              os.path.join(out_dir, 'logo.ico'))
    print('assets written ->', out_dir)


if __name__ == '__main__':
    out_dir = sys.argv[1] if len(sys.argv) > 1 else r'E:\0-works\coding\berserker-main\berserker\assets'
    save_all(SVG_SOURCE, out_dir)
