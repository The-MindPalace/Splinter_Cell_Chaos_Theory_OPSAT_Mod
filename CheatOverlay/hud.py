"""OPSAT panel renderer - V1.2 HUD spec, round 3 ("Opsat Panel Redesign.pdf").

paint(panel, fns) draws the whole panel for the current tab and state and returns (RGBA image, entry),
where entry describes the DVORAK input box when it is typing (screen rect + Tk styling) or is None.

All layout numbers are design pixels for the 560-wide panel at 1080p; Canvas multiplies them by the
panel scale. Drawing happens on an RGB image that starts as the backdrop's colours, with Pillow's
'RGBA' draw mode doing real alpha blending; a coverage mask collects opaque marks (text, solid
accents, dots) so the final alpha is max(backdrop, marks) and text stays solid over bright scenes.
"""
import math
import os
import re
import time
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from layered import fallback, has_glyph, runs, wrap

FONTS = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')

# Colour tokens (spec 07). Each colour has one job: green = live, blue = objectives / Fisher,
# amber and red = threat only. During an alarm the accent swaps from GREEN to RED.
INK, BRIGHT, SOFT, MUTE = '#eef4ef', '#dbe4dd', '#b9c7bd', '#8a9b90'
GREEN, BLUE, AMBER, RED = '#8ff0a4', '#86cdfa', '#ffb347', '#ff5a4f'
BASE, ON_ACC, WHITE, BLACK = '#04110c', '#03120a', '#ffffff', '#000000'

WIDTH, H_RADAR, H_FULL = 560, 616, 770      # panel sizes (spec 01)
X0, X1 = 28, 508                            # content edges: 28 from the rail; text stays where the backdrop is dense
FOOT = 38                                   # pinned footer
SS = 2                                      # radar supersampling


def rgb(c):
    c = c.lstrip('#')
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def rgba(c, a=1.0):
    return rgb(c) + (max(0, min(255, int(round(a * 255)))),)


def mix(a, b, t):
    return '#%02x%02x%02x' % tuple(int(round(x + (y - x) * t)) for x, y in zip(rgb(a), rgb(b)))


# --- fonts -------------------------------------------------------------------------------------
_SEGOE = {400: 'segoeui.ttf', 600: 'seguisb.ttf', 700: 'segoeuib.ttf'}
_BAHN = {400: 'SemiCondensed', 600: 'SemiBold SemiCondensed', 700: 'Bold SemiCondensed'}


@lru_cache(maxsize=160)
def font(kind, size, weight=400):
    """kind 'B' Bahnschrift SemiCondensed, 'S' Segoe UI, 'C' Consolas; size in screen pixels."""
    size = max(6.0, round(size * 2) / 2)
    if kind == 'B':
        f = ImageFont.truetype(os.path.join(FONTS, 'bahnschrift.ttf'), size)
        try:
            f.set_variation_by_name(_BAHN.get(weight, 'SemiCondensed'))
        except (OSError, ValueError):
            pass
        return f
    if kind == 'C':
        return ImageFont.truetype(os.path.join(FONTS, 'consolab.ttf' if weight >= 700 else 'consola.ttf'), size)
    return ImageFont.truetype(os.path.join(FONTS, _SEGOE.get(weight, 'segoeui.ttf')), size)


def _segments(text, f, track):
    if track:  # letter-spaced labels go glyph by glyph
        return [(ch, f if has_glyph(f, ch) else fallback(f)) for ch in text]
    return runs(text, f)


@lru_cache(maxsize=8192)
def tlen(text, f, track=0.0):
    w = sum(g.getlength(seg) for seg, g in _segments(text, f, track))
    return w + (track * f.size * (len(text) - 1) if track and text else 0)


@lru_cache(maxsize=2048)
def _wrap(text, f, width):
    """Measuring is most of a paint; the same strings wrap the same way frame after frame."""
    return tuple(wrap(text, f, width))


# --- cached raster pieces ----------------------------------------------------------------------
def _stops(x, xs, ys):
    """Piecewise interpolation with a smoothstep in each segment (the eased backdrop fade)."""
    xs, ys = np.asarray(xs, np.float32), np.asarray(ys, np.float32)
    i = np.clip(np.searchsorted(xs, x, side='right') - 1, 0, len(xs) - 2)
    t = np.clip((x - xs[i]) / np.maximum(xs[i + 1] - xs[i], 1e-6), 0, 1)
    return ys[i] + (ys[i + 1] - ys[i]) * (t * t * (3 - 2 * t))


@lru_cache(maxsize=16)
def backdrop(W, H, s, accent):
    """Base fade, top fade, rail-side edge glow and floor glow (spec 08), as an RGBA image."""
    xs = (np.arange(W, dtype=np.float32) + 0.5) / s
    ys = (np.arange(H, dtype=np.float32) + 0.5) / s
    # Spec stops shifted right by ~16 px: its own rule is "text stays where alpha >= .82", but content runs
    # to x 508 (distance, threat cells, tabs), where the spec's curve is already down to ~.68.
    ax = _stops(xs, [0, 224, 404, 498, 526, 546, 560], [.96, .93, .89, .82, .64, .36, 0])
    ay = np.interp(ys, [0, 10, 22, 36], [0, .35, .8, 1]).astype(np.float32)
    A = ay[:, None] * ax[None, :]
    C = np.empty((H, W, 3), np.float32)
    C[:] = rgb(BASE)
    acc = np.array(rgb(accent), np.float32)
    glow = np.interp(xs, [0, 24, 140], [.11, .045, 0])[None, :] * ay[:, None]
    hd = H / s
    floor = (np.clip((ys / hd - .65) / .35, 0, 1) * .055)[:, None] * np.clip((.95 - xs / WIDTH) / .35, 0, 1)[None, :]
    for g in (glow, floor):  # accent light composited over the base
        A2 = g + A * (1 - g)
        C = (acc * g[..., None] + C * (A * (1 - g))[..., None]) / np.maximum(A2, 1e-6)[..., None]
        A = A2
    out = np.empty((H, W, 4), np.uint8)
    out[..., :3] = np.clip(C + .5, 0, 255)
    out[..., 3] = np.clip(A * 255 + .5, 0, 255)
    return Image.fromarray(out)


@lru_cache(maxsize=96)
def _plate_mask(w, h, a0, a1, cut):
    """Alpha mask of a plate: vertical gradient, top-right corner cut at 45 degrees (anti-aliased)."""
    a = np.repeat(np.linspace(a0, a1, max(1, h), dtype=np.float32)[:, None], max(1, w), axis=1)
    if cut > 0:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        a *= np.clip((w - .5 - xx) + (yy + .5) - cut + .5, 0, 1)
    return Image.fromarray(np.clip(a * 255 + .5, 0, 255).astype(np.uint8))


@lru_cache(maxsize=32)
def _rule_mask(w, h, a0, a1):
    a = np.repeat(np.linspace(a0, a1, max(1, w), dtype=np.float32)[None, :], max(1, h), axis=0)
    return Image.fromarray(np.clip(a * 255 + .5, 0, 255).astype(np.uint8))


# --- canvas --------------------------------------------------------------------------------------
class Canvas:
    """Design-pixel drawing on an RGB colour image plus an L coverage mask."""

    def __init__(self, colour, mask, s, alpha=None):
        self.rgb, self.mask, self.s, self.alpha = colour, mask, s, alpha
        self.d = ImageDraw.Draw(self.rgb, 'RGBA')  # 'RGBA' mode: real blending onto the RGB image
        self.m = ImageDraw.Draw(self.mask)
        self.dry = False                          # measure-only passes (INTEL fitting)

    @classmethod
    def panel(cls, W, H, s, accent):
        bd = backdrop(W, H, s, accent)
        return cls(bd.convert('RGB'), Image.new('L', (W, H), 0), s, bd.getchannel('A'))

    def result(self):
        out = self.rgb.convert('RGBA')
        out.putalpha(ImageChops.lighter(self.alpha, self.mask))
        return out

    # measuring
    def f(self, kind, size, weight=400):
        return font(kind, size * self.s, weight)

    def w(self, text, f, track=0.0):
        return tlen(text, f, track) / self.s

    def fit(self, text, f, width, track=0.0):
        width *= self.s
        if tlen(text, f, track) <= width:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if tlen(text[:mid].rstrip() + '\u2026', f, track) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip(' ,.;:-\u00b7') + '\u2026'

    def wrap(self, text, f, width, max_lines=0):
        lines = list(_wrap(text, f, round(width * self.s, 1)))
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [self.fit(' '.join(lines[max_lines - 1:]), f, width)]
        return lines

    # drawing (all coordinates in design px)
    def text(self, x, base, text, f, fill, track=0.0, align='l', shadow=True, halo=False):
        """Text on a baseline. Returns its width in design px."""
        if not text:
            return 0.0
        s = self.s
        w = tlen(text, f, track)
        if self.dry:
            return w / s
        X = x * s - (w if align == 'r' else w / 2 if align == 'm' else 0)
        B = base * s
        col = rgba(fill) if isinstance(fill, str) else fill
        gap = track * f.size
        for seg, g in _segments(text, f, track):
            if halo:  # radar labels: dark halo instead of a drop shadow
                self.d.text((X, B), seg, font=g, fill=col, anchor='ls', stroke_width=max(1, round(1.75 * s)),
                            stroke_fill=(2, 8, 6, 190))
            else:
                if shadow:  # spec: 0 1 2 px black 0.65 on everything not sitting on a solid accent
                    self.d.text((X, B + s), seg, font=g, fill=(0, 0, 0, 166), anchor='ls')
                self.d.text((X, B), seg, font=g, fill=col, anchor='ls')
            self.m.text((X, B), seg, font=g, fill=col[3], anchor='ls')
            X += g.getlength(seg) + gap
        return w / s

    def _box(self, x0, y0, x1, y1):
        s = self.s
        return [round(x0 * s), round(y0 * s), round(x1 * s) - 1, round(y1 * s) - 1]

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1.0, radius=0.0):
        if self.dry:
            return
        box, lw = self._box(x0, y0, x1, y1), max(1, round(width * self.s))
        if radius:
            self.d.rounded_rectangle(box, radius=radius * self.s, fill=fill, outline=outline, width=lw if outline else 0)
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=lw if outline else 0)
        if fill and fill[3] >= 128:
            self.m.rectangle(box, fill=fill[3])

    def plate(self, x0, y0, x1, y1, colour, a0, a1, cut=10.0, mark=None, under=0.0):
        """Spec plate: rectangle, top-right corner cut at 45 deg, vertical gradient, no outline;
        optional 32 x 2 section-colour mark on the top-left edge. `under` lays an opaque-ish BASE
        underlay first (same shape) for plates that sit outside the dense backdrop."""
        if self.dry:
            return
        s = self.s
        X, Y = round(x0 * s), round(y0 * s)
        w, h = round(x1 * s) - X, round(y1 * s) - Y
        if w > 0 and h > 0:
            box = (X, Y, X + w, Y + h)
            if under:
                m = _plate_mask(w, h, under, under, round(cut * s))
                self.rgb.paste(rgb(BASE), box, m)
                self.mask.paste(ImageChops.lighter(self.mask.crop(box), m), box[:2])
            self.rgb.paste(rgb(colour), box, _plate_mask(w, h, a0, a1, round(cut * s)))
        if mark:
            self.rect(x0, y0 - 2, x0 + 32, y0, fill=rgba(mark))

    def rule(self, x0, x1, y, a0=.16, a1=.06, lead=0, lead_colour=None):
        """1 px hairline fading left to right; the first `lead` px in the section colour."""
        if self.dry:
            return
        s = self.s
        X, Y, w, h = round(x0 * s), round(y * s), round((x1 - x0) * s), max(1, round(s))
        if w > 0:
            self.rgb.paste(rgb(WHITE), (X, Y, X + w, Y + h), _rule_mask(w, h, a0, a1))
        if lead and lead_colour:
            self.rect(x0, y, x0 + lead, y + max(1, round(s)) / s, fill=rgba(lead_colour))

    def line(self, pts, fill, width=1.0):
        if not self.dry:
            self.d.line([(x * self.s, y * self.s) for x, y in pts], fill=fill, width=max(1, round(width * self.s)))

    def circle(self, cx, cy, r, fill=None, outline=None, width=1.0):
        if self.dry:
            return
        s = self.s
        box = [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s]
        self.d.ellipse(box, fill=fill, outline=outline, width=max(1, round(width * s)) if outline else 0)
        if fill and fill[3] >= 160:
            self.m.ellipse(box, fill=fill[3])

    def pie(self, cx, cy, r, a0, a1, fill=None, outline=None, width=1.0):
        """Wedge between compass angles a0..a1 (0 = up, clockwise)."""
        if not self.dry and r > 0:
            s = self.s
            box = [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s]
            self.d.pieslice(box, a0 - 90, a1 - 90, fill=fill, outline=outline, width=max(1, round(width * s)))

    def arc(self, cx, cy, r, a0, a1, fill, width=1.0):
        if not self.dry:
            s = self.s
            box = [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s]
            self.d.arc(box, a0 - 90, a1 - 90, fill=fill, width=max(1, round(width * s)))

    def polygon(self, pts, fill=None, outline=None, width=1.0):
        if self.dry:
            return
        P = [(x * self.s, y * self.s) for x, y in pts]
        self.d.polygon(P, fill=fill, outline=outline, width=max(1, round(width * self.s)) if outline else 1)
        if fill and fill[3] >= 160:
            self.m.polygon(P, fill=fill[3])

    def sub(self, x0, y0, x1, y1, ss):
        """A supersampled canvas over a region (design px relative to its own origin)."""
        s = self.s
        box = (round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s))
        size = ((box[2] - box[0]) * ss, (box[3] - box[1]) * ss)
        c = Canvas(self.rgb.crop(box).resize(size, Image.BILINEAR), Image.new('L', size, 0), s * ss)
        c.box, c.parent = box, self
        return c

    def commit(self):
        """Downsample a sub canvas back into its parent."""
        p, box = self.parent, self.box
        size = (box[2] - box[0], box[3] - box[1])
        p.rgb.paste(self.rgb.resize(size, Image.LANCZOS), box[:2])
        region = p.mask.crop(box)
        p.mask.paste(ImageChops.lighter(region, self.mask.resize(size, Image.LANCZOS)), box[:2])


# --- shared pieces -------------------------------------------------------------------------------
def rail(c, H, acc):
    c.rect(0, 24, 2, H, fill=rgba(acc, .35))
    c.rect(0, 24, 4, 58, fill=rgba(acc))          # lit caps
    c.rect(0, H - 38, 4, H, fill=rgba(acc))


def header(c, v, acc):
    if v.alarm:  # 20 px banner fills the top padding, so nothing below moves
        # The top padding is where the backdrop fades out, so the banner brings its own dark underlay:
        # the most urgent line in the panel must not wash out over sky or snow.
        c.plate(X0, 3, X1, 23, RED, .30, .18, cut=7, under=.9)
        fb = c.f('B', 12.5, 700)
        c.text(X0 + 10, 17.5, '\u25a0  ALARM RAISED', fb, RED, track=.14)
        c.text(X1 - 12, 17.5, v.alarm_note, c.f('B', 12.5, 600), INK, align='r')
    x = X0 + c.text(X0, 50, 'OPSAT', c.f('B', 26, 700), INK, track=.14) + 4
    fv = c.f('B', 12, 700)
    c.text(x, 35, 'V1.2', fv, INK)
    x += c.w('V1.2', fv) + 14
    fp = c.f('B', 12.5, 700)
    for label, on in (('GOD', v.god), ('INVIS', v.invis)):
        tw = c.w(label, fp, .10)
        c.rect(x, 30, x + tw + 14, 50, fill=rgba(GREEN) if on else None,
               outline=None if on else rgba(WHITE, .2), width=1, radius=2)
        c.text(x + 7, 44.5, label, fp, ON_ACC if on else MUTE, track=.10, shadow=not on)
        x += tw + 20
    ft = c.f('B', 15, 700)
    widths = [c.w(t, ft, .10) + 28 for t in v.tabs]
    fx0 = X1 - sum(widths) - 6
    c.rect(fx0, 25, X1, 55, fill=rgba(BLACK, .22), outline=rgba(WHITE, .14), width=1, radius=3)
    x = fx0 + 3
    for i, (t, tw) in enumerate(zip(v.tabs, widths)):
        on = i == v.tab
        if on:
            c.rect(x, 28, x + tw, 52, fill=rgba(acc), radius=2)
        c.text(x + tw / 2, 45.5, t, ft, ON_ACC if on else MUTE, track=.10, align='m', shadow=not on)
        x += tw
    c.rule(X0, X1, 66, lead=44, lead_colour=acc)


def footer(c, H, v):
    """Key hints for what works right now: the arrows do nothing while typing, so the input gets its own."""
    if v.typing and v.key_mode:
        keys = (('CTRL+V', 'paste'), ('\u21b5', 'save'), ('ESC', 'skip'))
    elif v.typing:
        keys = (('\u21b5', 'send'), ('ESC', 'cancel'))
    else:
        keys = (('\u25b2\u25bc', 'open/close'), ('\u25c0\u25b6', 'tab'), ('INS', 'ask DVORAK'))
    base, fk, fl = H - 14, c.f('B', 13, 700), c.f('B', 14, 600)
    x = X0
    for i, (key, label) in enumerate(keys):
        kw = c.w(key, fk) + 14
        c.rect(x, H - 29, x + kw, H - 9, fill=rgba(WHITE, .10), radius=2)
        c.text(x + kw / 2, base, key, fk, INK, align='m', shadow=False)
        x += kw + 7
        x += c.text(x, base, label, fl, MUTE)
        if i < len(keys) - 1:
            x += 7 + c.text(x + 7, base, '\u00b7', fl, MUTE) + 7


def distance_block(c, v, xr, y, h):
    """Distance (largest figure in the panel) with bearing under it; returns its width."""
    if not v.has_dist:
        return 0
    fb, fu, fs = c.f('B', 38, 700), c.f('B', 19, 700), c.f('B', 14, 600)
    nb = y + h / 2 + 8
    if v.here_flag:
        w = c.text(xr, nb, 'HERE', fb, BLUE, track=.05, align='r')
        return max(w, c.text(xr, nb + 17, 'in this area', fs, SOFT, align='r'))
    uw = c.text(xr, nb, 'm', fu, BLUE, align='r')
    nw = c.text(xr - uw - 1, nb, '%.0f' % v.dist, fb, BLUE, align='r')
    return max(uw + nw + 1, c.text(xr, nb + 17, v.bearing, fs, SOFT, align='r'))


def next_plate(c, v, y, max_lines, route=False):
    """NEXT plate: objective title left (S 17 one line on RADAR, S 16 up to 2 lines on INTEL), distance right.
    With `route`, a line under the title names the rooms to go through (the 3D map's room graph), because the
    straight-line distance and bearing say nothing about which door or floor."""
    dry, c.dry = c.dry, True
    dw = distance_block(c, v, X1 - 16, y, 64)
    c.dry = dry
    width = (X1 - 16) - (X0 + 16) - (dw + 18 if dw else 0)
    title = v.next_title or 'Waiting for mission telemetry'
    if max_lines > 1:
        ft = c.f('S', 16, 600)
        lines = c.wrap(title, ft, width, max_lines)
    else:  # one line: step down to S 15 before cutting the title (the noun is usually at the end)
        for size in (17, 16, 15):
            ft = c.f('S', size, 600)
            if c.w(title, ft) <= width:
                break
        lines = [c.fit(title, ft, width)]
    via = v.route if route and v.route else None
    h = max(64, 48 + (len(lines) - 1) * 22 + (22 if via else 0) + 16)
    c.plate(X0, y, X1, y + h, BLUE, .14, .08, cut=10, mark=BLUE)
    c.text(X0 + 16, y + 24, 'NEXT', c.f('B', 13.5, 700), BLUE, track=.16)
    for i, line in enumerate(lines):
        c.text(X0 + 16, y + 48 + i * 22, line, ft, INK if v.next_title else MUTE)
    if via:
        fr = c.f('S', 14.5)
        rw = c.text(X0 + 16, y + 48 + len(lines) * 22, '\u2192', c.f('S', 14.5, 600), BLUE)
        c.text(X0 + 22 + rw, y + 48 + len(lines) * 22, c.fit(' \u203a '.join(via), fr, width - rw - 6), fr, SOFT)
    distance_block(c, v, X1 - 16, y, h)
    return y + h


# --- RADAR -------------------------------------------------------------------------------------
def threats(v, fns):
    intel = v.intel
    sam = intel['sam']
    alert = susp = facing_me = 0
    for g in intel.get('guards', []):
        mood = fns.guard_mood(g)[0]
        if mood in ('DEAD', 'OUT'):
            continue
        alert += mood == 'ALERT'
        susp += mood == 'SUSPICIOUS'
        dist, _, dz = fns.relative(sam, g['loc'])
        facing_me += bool(abs(dz) < 2.5 and dist < 25 and fns.facing(g['loc'], g['yaw'], g['cone'], g['range'], sam[0]))
    cams = 0
    for snr in intel.get('sensors', []):
        if snr['state'] not in OFF_STATES and fns.relative(sam, snr['loc'])[0] <= fns.range_m:
            cams += 1
    return [('ALARMS', intel.get('alarm') or 0, RED), ('ALERT', alert, RED), ('SUSPICIOUS', susp, AMBER),
            ('FACING YOU', facing_me, AMBER), ('CAMERAS', cams, None)]


OFF_STATES = ('s_Deactivated', 's_Malfunctioning', 's_Destructed', 's_Off', 's_Idle')
MOOD = {'CALM': GREEN, 'SUSPICIOUS': AMBER, 'ALERT': RED}


def radar_tab(c, v, acc, H, fns):
    next_plate(c, v, 80, 1)
    if v.here:
        fl = c.f('B', 13.5, 700)
        lw = c.text(X0, 172, 'HERE', fl, acc, track=.16)
        fs = c.f('S', 16, 600)
        c.text(X0 + lw + 10, 172, c.fit(v.here['next'], fs, X1 - X0 - lw - 10), fs, INK)
    if not v.intel:
        c.text(X0, 230, 'Radar comes online in a mission.', c.f('S', 16), MUTE)
        return
    radar(c, v, acc, fns, X0, 186, 360, 380)
    cells, gap = v.threats, 5
    x0, x1, y0 = X0 + 372, X1, 186
    ch = (380 - 4 * gap) / 5
    fn, fl = c.f('B', 34, 700), c.f('B', 13, 700)
    for i, (label, n, col) in enumerate(cells):
        y = y0 + i * (ch + gap)
        if n and col:
            c.plate(x0, y, x1, y + ch, col, .22, .12, cut=0)
        else:
            c.plate(x0, y, x1, y + ch, WHITE, .085 if n else .055, .05 if n else .03, cut=0)
        fc = col if n and col else (INK if n else MUTE)
        c.text(x0 + 12, y + 40, str(n), fn, fc)
        c.text(x0 + 12, y + ch - 12, c.fit(label, fl, x1 - x0 - 18, .08), fl, fc, track=.08)


def radar(c, v, acc, fns, bx, by, bw, bh):
    r = c.sub(bx, by, bx + bw, by + bh, SS)
    cx, cy, R = bw / 2, bh / 2, 164.0
    k = R / fns.range_m
    sam = v.intel['sam']
    r.circle(cx, cy, R, fill=rgba(BLACK, .30))
    r.pie(cx, cy, R, -45, 45, fill=rgba(WHITE, .045))                     # Sam's view wedge, pointing up
    r.line([(cx - R, cy), (cx + R, cy)], rgba(WHITE, .06))
    r.line([(cx, cy - R), (cx, cy + R)], rgba(WHITE, .06))
    r.circle(cx, cy, R / 2, outline=rgba(WHITE, .12), width=1)
    r.circle(cx, cy, R, outline=rgba(WHITE, .14), width=1)
    for i in range(36):                                                    # ticks, longer every 30 degrees
        a = math.radians(i * 10)
        L = 8 if i % 3 == 0 else 4
        r.line([(cx + math.sin(a) * (R - L), cy - math.cos(a) * (R - L)),
                (cx + math.sin(a) * R, cy - math.cos(a) * R)], rgba(WHITE, .28 if i % 3 == 0 else .16), 1)
    fr = r.f('B', 13, 600)
    r.text(cx, cy - R + 24, '20 m', fr, MUTE, align='m')
    r.text(cx + 12, cy - R / 2 + 14, '10', fr, MUTE, align='m')
    for mid in (45, 135, 225, 315):                                        # bezel: four 78 deg accent arcs
        r.arc(cx, cy, R + 4, mid - 39, mid + 39, rgba(acc, .9), 2.5)
    r.polygon([(cx, cy - R + 1), (cx - 6.5, cy - R - 10), (cx + 6.5, cy - R - 10)], fill=rgba(acc))  # 12 = forward

    def to_screen(loc):
        dist, bearing, dz = fns.relative(sam, loc)
        b = math.radians(bearing)
        return cx + math.sin(b) * dist * k, cy - math.cos(b) * dist * k, dist, bearing, dz

    def cone(x, y, yaw, deg, length_m, col, fa=.2, ea=.8):
        heading = (yaw - sam[1]) / 65536 * 360
        r.pie(x, y, length_m * k, heading - deg / 2, heading + deg / 2, fill=rgba(col, fa), outline=rgba(col, ea), width=1.5)

    for snr in v.intel.get('sensors', []):
        x, y, dist, _, dz = to_screen(snr['loc'])
        if dist > fns.range_m or snr['state'] in OFF_STATES:
            continue
        camera = 'camera' in (snr.get('name') or '').lower()
        col = RED if snr['state'] == 's_Alert' else INK
        deg = snr['cone'] if 5 < (snr['cone'] or 0) < 180 else (50 if camera else 40)
        cone(x, y, snr['yaw'], deg, min(7.0, (snr['range'] or 700) / fns.uu_per_m), col, .12, .5)
        q = 4.5
        if camera:
            r.rect(x - q, y - q, x + q, y + q, fill=rgba(col))
        else:
            r.rect(x - q, y - q, x + q, y + q, outline=rgba(col), width=1.5)
    for g in v.intel.get('guards', []):
        x, y, dist, _, dz = to_screen(g['loc'])
        if dist > fns.range_m:
            continue
        mood = fns.guard_mood(g)[0]
        if mood in ('DEAD', 'OUT'):
            r.circle(x, y, 3.5, fill=rgba(WHITE, .25))
            continue
        col = MOOD.get(mood, GREEN)
        level = abs(dz) < 2.5
        deg = g['cone'] if 5 < (g.get('cone') or 0) < 180 else 60
        cone(x, y, g['yaw'], deg, 7.0, col, .2 if level else .1, .8 if level else .4)
        if mood == 'ALERT':
            r.circle(x, y, 10.5, outline=rgba(RED), width=2)
        if level:
            r.circle(x, y, 6, fill=rgba(col))
        else:  # another floor: hollow
            r.circle(x, y, 6, outline=rgba(col), width=2)
    fl = r.f('B', 15, 600)
    inside, rim = [], []
    for ob in v.markers:
        if v.in_area(ob):  # exact spot unknown and Sam is in its area: no diamond
            continue
        x, y, dist, bearing, dz = to_screen(ob['loc'])
        is_next = v.nxt is not None and ob['label'] == v.nxt['label']
        mark = dict(x=x, y=y, dist=dist, bearing=bearing, next=is_next, name=ob.get('base', ob['label']),
                    more=ob.get('extra', 0))
        (inside if dist <= fns.range_m else rim).append(mark)
    # Next objective first, then nearest: it keeps its label when space runs out.
    key = lambda m: (not m['next'], m['dist'])
    taken = [(cx - 14, cy - 16, cx + 14, cy + 16),                       # Sam's arrow
             (cx - 20, cy - R + 10, cx + 20, cy - R + 28)]                # '20 m'

    def place(text, spots):
        """First label spot whose box hits nothing already drawn: (x, baseline, align) or None."""
        w = r.w(text, fl)
        for x, base, align in spots:
            x0 = x - w if align == 'r' else x - w / 2 if align == 'm' else x
            box = (x0 - 2, base - 13, x0 + w + 2, base + 4)
            if all(box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3] for b in taken):
                taken.append(box)
                return x, base, align
        return None

    for m in sorted(inside, key=key):
        x, y, d = m['x'], m['y'], 8
        r.polygon([(x, y - d), (x + d, y), (x, y + d), (x - d, y)], fill=rgba(BLUE) if m['next'] else None,
                  outline=rgba(BLUE), width=2)
        if m['next']:
            o = d + 5
            r.polygon([(x, y - o), (x + o, y), (x, y + o), (x - o, y)], outline=rgba(BLUE, .8), width=1.5)
        taken.append((x - d - 5, y - d - 5, x + d + 5, y + d + 5))
    for m in sorted(inside, key=key):
        x, y = m['x'], m['y']
        text = m['name'] + ('  +%d' % m['more'] if m['more'] else '')
        side = [(x - 16, y + 5, 'r'), (x + 16, y + 5, 'l')]
        spot = place(text, (side if x > cx + R * .3 else side[::-1]) + [(x, y - 14, 'm'), (x, y + 25, 'm')])
        if spot:
            r.text(spot[0], spot[1], text, fl, BLUE, align=spot[2], halo=True)
    # Off-range: one pointer per direction. Objectives within 16 deg of each other share it ("Server +2 65m").
    pointers = []
    for m in sorted(rim, key=key):
        for p in pointers:
            if abs((m['bearing'] - p['bearing'] + 180) % 360 - 180) < 16:
                p['more'] += 1 + m['more']
                break
        else:
            pointers.append(dict(m))
    for p in pointers:
        b = math.radians(p['bearing'])
        sb, cb = math.sin(b), -math.cos(b)
        ex, ey = cx + sb * (R - 2), cy + cb * (R - 2)
        px_, py_ = -cb, sb
        r.polygon([(ex + sb * 9, ey + cb * 9), (ex - sb * 3 + px_ * 7, ey - cb * 3 + py_ * 7),
                   (ex - sb * 3 - px_ * 7, ey - cb * 3 - py_ * 7)], fill=rgba(BLUE) if p['next'] else rgba(BLUE, .65))
    for p in pointers:
        b = math.radians(p['bearing'])
        sb, cb = math.sin(b), -math.cos(b)
        align = 'r' if sb > .35 else 'l' if sb < -.35 else 'm'
        text = '%s%s %.0fm' % (p['name'], '  +%d' % p['more'] if p['more'] else '', p['dist'])
        spots = [(cx + sb * (R - k), cy + cb * (R - k) + 5, align) for k in (22, 40, 58)]
        spot = place(text, spots)
        if spot:
            r.text(spot[0], spot[1], text, fl, BLUE, align=spot[2], halo=True)
    r.polygon([(cx, cy - 12), (cx + 9, cy + 11), (cx, cy + 5), (cx - 9, cy + 11)], fill=rgba(INK),
              outline=(0, 0, 0, 200), width=1.5)                                # Sam, centre
    r.commit()


# --- INTEL -------------------------------------------------------------------------------------
class Ops:
    """Deferred drawing, so a plate can be sized from its contents and drawn underneath them."""

    def __init__(self, c):
        self.c, self.ops = c, []

    def __getattr__(self, name):
        return lambda *a, **k: self.ops.append((name, a, k))

    def flush(self):
        for name, a, k in self.ops:
            getattr(self.c, name)(*a, **k)


def intel_tab(c, v, acc, H):
    if not v.mission:
        c.text(X0, 120, 'Intel comes online in a mission.', c.f('S', 16), MUTE)
        return
    bottom = H - FOOT - 6
    # (FROM HERE steps, objective routes, lines per objective): FROM HERE is what to do where Sam stands, so
    # the objective's own routes and the checklist give way before any of its steps do.
    plans = [(3, 3, 2), (3, 2, 2), (3, 1, 2), (3, 1, 1), (3, 0, 1), (2, 0, 1), (1, 0, 1), (0, 0, 1)]
    for plan in plans:
        c.dry = True
        end = intel_blocks(c, v, acc, *plan)
        c.dry = False
        if end <= bottom:
            break
    intel_blocks(c, v, acc, *plan, limit=bottom)


def intel_blocks(c, v, acc, steps, routes, obj_lines, limit=1e9):
    y = 78
    fa = c.f('B', 38, 700)                                                 # 1 area name + primaries
    c.text(X0, y + 36, c.fit((v.room or 'UNKNOWN AREA').upper(), fa, 290, .05), fa, INK, track=.05)
    if v.total:
        fc, fl = c.f('B', 22, 700), c.f('B', 13.5, 700)
        cw = c.text(X1, y + 26, '%d/%d' % (v.done, v.total), fc, INK, align='r')
        c.text(X1 - cw - 8, y + 26, 'PRIMARIES', fl, MUTE, track=.16, align='r')
        n = min(v.total, 8)
        seg = min(30.0, (120 - (n - 1) * 4) / n)
        x = X1 - n * seg - (n - 1) * 4
        for i in range(n):
            c.rect(x, y + 34, x + seg, y + 37, fill=rgba(acc) if i < v.done else rgba(WHITE, .2))
            x += seg + 4
    y = next_plate(c, v, y + 52, 2, route=True) + 14                       # 2 NEXT (+ rooms to go through)
    if v.here:                                                             # 3 FROM HERE plate
        o, inner = Ops(c), X1 - X0 - 32
        fl, fg, fs, fn = c.f('B', 13.5, 700), c.f('S', 19, 600), c.f('S', 16), c.f('B', 13, 700)
        o.text(X0 + 16, y + 26, 'FROM HERE  \u00b7  ' + (v.room or '').upper(), fl, acc, track=.16)
        b = y + 54
        goal = c.wrap(v.here['next'], fg, inner, 2)
        for i, line in enumerate(goal):
            o.text(X0 + 16, b + i * 25, line, fg, INK)
        b += (len(goal) - 1) * 25 + 30
        for i, way in enumerate(v.here['ways'][:steps]):
            ls = c.wrap(way, fs, inner - 30, 4)  # never cut an instruction mid-sentence (2 of 470 need a 4th line)
            o.rect(X0 + 16, b - 15, X0 + 36, b + 5, outline=rgba(acc, .8), width=1)
            o.text(X0 + 26, b, str(i + 1), fn, acc, align='m')
            for j, line in enumerate(ls):
                o.text(X0 + 46, b + j * 22, line, fs, INK)
            b += len(ls) * 22 + 6
        bottom_y = b - 22 - 6 + 18 if steps and v.here['ways'] else b - 30 + 18
        c.plate(X0, y, X1, bottom_y, WHITE, .08, .045, cut=10, mark=acc)
        o.flush()
        y = bottom_y + 16
    if v.note:                                                             # 4 FOR THE OBJECTIVE (no plate)
        fl, fw, fr, fn = c.f('B', 13.5, 700), c.f('S', 15), c.f('S', 15), c.f('B', 14, 700)
        c.text(X0, y + 14, 'FOR THE OBJECTIVE', fl, BLUE, track=.16)
        b = y + 38
        where = c.wrap(v.note['where'], fw, X1 - X0, 2)
        for i, line in enumerate(where):
            c.text(X0, b + i * 21, line, fw, BRIGHT)
        b += (len(where) - 1) * 21 + 27
        for i, way in enumerate(v.note['ways'][:routes]):
            ls = c.wrap(way, fr, X1 - X0 - 24, 4)
            c.text(X0 + 4, b, str(i + 1), fn, BLUE)
            for j, line in enumerate(ls):
                c.text(X0 + 24, b + j * 21, line, fr, SOFT)
            b += len(ls) * 21 + 6
        y = b - 21 - 6 + 18 if routes and v.note['ways'] else b - 27 + 14
    fl = c.f('B', 13.5, 700)                                               # 5 OBJECTIVES
    lw = c.text(X0, y + 16, 'OBJECTIVES', fl, MUTE, track=.16)
    chip_x0 = X1
    if v.fail:
        fs = c.f('S', 15)
        fw = c.w('FAILS IF', fl, .14)
        rule = c.fit(v.fail, fs, 210)
        chip_x0 = X1 - (fw + 8 + c.w(rule, fs) + 24)
        c.plate(chip_x0, y, X1, y + 25, RED, .24, .13, cut=7)
        c.text(chip_x0 + 10, y + 17, 'FAILS IF', fl, RED, track=.14)
        c.text(chip_x0 + 18 + fw, y + 17, rule, fs, INK)
    c.rect(X0 + lw + 12, y + 11, chip_x0 - 12, y + 12, fill=rgba(WHITE, .10))
    b = y + 46
    fo = c.f('S', 16)
    for title, status, _ in v.must:
        ls = c.wrap(title, fo, X1 - X0 - 24, obj_lines)
        if b + (len(ls) - 1) * 22 > limit:
            break
        if status == 1:
            c.circle(X0 + 5, b - 6, 5.5, fill=rgba(acc))
        else:
            c.circle(X0 + 5, b - 6, 5, outline=rgba(SOFT if status == 0 else '#556052'), width=1.5)
        for j, line in enumerate(ls):
            c.text(X0 + 24, b + j * 22, line, fo, MUTE if status else INK)
        b += len(ls) * 22 + 4
    if v.extra and b <= limit:
        fb, fs = c.f('S', 15, 600), c.f('S', 15)
        lead = '+%d optional:' % len(v.extra)
        w = c.text(X0 + 24, b, lead, fb, SOFT)
        c.text(X0 + 30 + w, b, c.fit('; '.join(t.rstrip('.') for t, _, _ in v.extra), fs, X1 - X0 - 30 - w), fs, SOFT)
        b += 22
    return b - 16


# --- DVORAK ------------------------------------------------------------------------------------
def dvorak_tab(c, v, acc, H):
    fl = c.f('B', 13.5, 700)
    if v.online:
        c.circle(X0 + 4, 86, 7, fill=rgba(GREEN, .22))
        c.circle(X0 + 4, 86, 4, fill=rgba(GREEN))
    else:
        c.circle(X0 + 4, 86, 4, outline=rgba(MUTE), width=1.5)
    c.text(X0 + 16, 91, 'ONLINE' if v.online else 'OFFLINE', fl, GREEN if v.online else MUTE, track=.16)
    c.text(X0, 128, 'DVORAK', c.f('B', 34, 700), INK, track=.08)
    if v.stage:
        c.text(X1, 103, 'RAPPORT', fl, MUTE, track=.16, align='r')
        c.text(X1, 128, v.stage, c.f('B', 20, 600), INK, align='r')
    iy1 = H - FOOT - 8
    iy0 = iy1 - 48
    entry = input_box(c, v, acc, iy0, iy1)
    thread(c, v, 146, iy0 - 14)
    return entry


def _thread_blocks(c, v):
    """Chronological render blocks: (height, draw(y)) - events, one-line history, latest Q, reply plate."""
    chat = v.chat
    last_d = max((i for i, (w, _) in enumerate(chat) if w == 'dvorak'), default=-1)
    fishers = [i for i, (w, _) in enumerate(chat) if w == 'fisher']
    if fishers and fishers[-1] > last_d:   # asked, no reply yet: that question is the latest
        last_q = fishers[-1]
    else:                                   # the question the latest reply answers
        last_q = max((i for i in fishers if i < last_d), default=-1)
    fl, fh, fq, fr, fsys = c.f('B', 13.5, 700), c.f('S', 15), c.f('S', 16.5), c.f('S', 19), c.f('S', 14)
    blocks = []

    def event(text, col):
        def draw(y):
            w = c.text(X0, y + 19, '\u25c7', fl, col)
            tw = c.text(X0 + w + 7, y + 19, c.fit(text.upper(), fl, X1 - X0 - 60, .14), fl, col, track=.14)
            c.rect(X0 + w + tw + 19, y + 14, X1, y + 15, fill=rgba(col if col != MUTE else WHITE, .12))
        blocks.append((30, draw))

    def sysline(text, col):
        ls = c.wrap(text, fsys, X1 - X0 - 18, 2)

        def draw(y):
            c.text(X0, y + 19, '\u25c7', fl, col)
            for j, line in enumerate(ls):
                c.text(X0 + 18, y + 19 + j * 20, line, fsys, col)
        blocks.append((30 + (len(ls) - 1) * 20, draw))

    def row(who, text):
        col = BLUE if who == 'fisher' else GREEN
        label = 'FISHER' if who == 'fisher' else 'DVORAK'

        def draw(y):
            c.text(X0, y + 20, label, fl, col, track=.14)
            c.text(X0 + 88, y + 20, c.fit(' '.join(text.split()), fh, X1 - X0 - 88), fh, SOFT)
        blocks.append((32, draw))

    def question(text):
        ls = c.wrap(text, fq, X1 - X0, 4)

        def draw(y):
            c.text(X0, y + 16, 'FISHER', fl, BLUE, track=.16)
            for j, line in enumerate(ls):
                c.text(X0, y + 42 + j * 23, line, fq, BRIGHT)
        blocks.append((42 + (len(ls) - 1) * 23 + 14, draw))

    def reply(text, busy):
        body = text if text else ('thinking' + '.' * (int(_now() * 3) % 4))
        if busy and text:
            body += ' \u258c'
        ls = c.wrap(body, fr, X1 - X0 - 48, 0)

        def draw(y, ls=ls, h=None):
            h = 62 + (len(ls) - 1) * 28 + 22
            c.plate(X0, y, X1, y + h, GREEN, .10, .05, cut=12, mark=GREEN)
            c.text(X0 + 24, y + 28, 'DVORAK', fl, GREEN, track=.16)
            for j, line in enumerate(ls):
                c.text(X0 + 24, y + 62 + j * 28, line, fr, INK if text else MUTE)
        blocks.append((62 + (len(ls) - 1) * 28 + 22, draw, ls))

    def intro(title, col, paras):
        """Empty thread: what DVORAK is for, or how to bring the link up (the spec leaves this blank)."""
        lines = []
        for text, colour in paras:
            lines += [(line, colour) for line in c.wrap(text, fh, X1 - X0 - 32, 0)]
            lines.append(None)
        lines.pop()
        h = 50 + sum(21 if ln else 8 for ln in lines) + 14

        def draw(y):
            c.plate(X0, y, X1, y + h, WHITE, .07, .035, cut=10, mark=col)
            c.text(X0 + 16, y + 26, title, fl, col, track=.16)
            b = y + 52
            for ln in lines:
                if ln:
                    c.text(X0 + 16, b, ln[0], fh, ln[1])
                b += 21 if ln else 8
        blocks.append((h, draw))

    if not chat and not v.online:
        intro('BRING DVORAK ONLINE', AMBER, [
            ('1  Get an API key at console.anthropic.com (starts with sk-ant-).', SOFT),
            ('2  Press INS, paste it with Ctrl+V, press ENTER.', SOFT),
            ('DVORAK only calls the API when you ask. Radar and intel never need it.', MUTE)])
    elif not chat:
        intro('ASK DVORAK', GREEN, [
            ('Sees the live telemetry and the field notes for every objective. Try:', SOFT),
            ('\u201cHow do I get past the guards in this room?\u201d', BRIGHT),
            ('\u201cWhat\u2019s the door code here?\u201d', BRIGHT),
            ('\u201cQuietest way to the next objective?\u201d', BRIGHT)])
    for i, (who, text) in enumerate(chat):
        if who == 'fisher':
            question(text) if i == last_q else row(who, text)
        elif who == 'dvorak':
            reply(clean(text), v.busy and i == len(chat) - 1) if i == last_d else row(who, clean(text))
        elif who == 'event':
            event(text, MUTE)
        else:
            warn = any(w in text.lower() for w in ('error', 'offline', 'does not work', 'rejected', 'no connection'))
            sysline(text, AMBER if warn else MUTE)
    if not v.online and chat:  # an empty thread shows the setup steps instead
        event('LINK DOWN \u00b7 ENTER AN API KEY', AMBER)
    return blocks


def thread(c, v, top, bottom):
    """Bottom-anchored: the newest block sits just above the input; older ones clip off the top."""
    placed, y = [], bottom
    for blk in reversed(_thread_blocks(c, v)):
        h, draw = blk[0], blk[1]
        if y - h < top:
            if len(blk) == 3 and not placed:  # an over-long latest reply: keep the lines that fit
                n = max(1, int((y - top - 84) // 28) + 1)
                ls = blk[2][:n]
                if len(ls) < len(blk[2]):
                    ls[-1] = c.fit(ls[-1] + ' \u2026', c.f('S', 19), X1 - X0 - 48)
                h = 62 + (len(ls) - 1) * 28 + 22
                placed.append((y - h, lambda yy, d=draw, l=ls: d(yy, l)))
            break
        y -= h
        placed.append((y, draw))
        y -= 6
    for yy, draw in reversed(placed):
        draw(yy)


def input_box(c, v, acc, y0, y1):
    fk = c.f('B', 13.5, 700)
    if not v.typing:  # idle: neutral plate, "Ask DVORAK · press [INS]"
        c.plate(X0, y0, X1, y1, WHITE, .08, .045, cut=10)
        fs = c.f('S', 16)
        w = c.text(X0 + 20, y0 + 30, 'Ask DVORAK', fs, INK)
        c.text(X0 + 20 + w, y0 + 30, ' \u00b7 press', fs, MUTE)
        fc = c.f('B', 13, 700)
        kw = c.w('INS', fc) + 14
        c.rect(X1 - 16 - kw, y0 + 13, X1 - 16, y0 + 35, fill=rgba(WHITE, .12), radius=2)
        c.text(X1 - 16 - kw / 2, y0 + 29, 'INS', fc, INK, align='m', shadow=False)
        return None
    key = v.key_mode
    col = AMBER if key else acc
    c.plate(X0, y0, X1, y1, col, .12, .07, cut=10)
    bw = 92 if key else 58
    c.rect(X0, y0, X0 + bw, y1, fill=rgba(col))
    c.text(X0 + bw / 2, y0 + 29, 'API KEY' if key else 'ASK', fk, ON_ACC, track=.14, align='m', shadow=False)
    sw = c.text(X1 - 16, y0 + 29, '\u21b5 SAVE' if key else '\u21b5 SEND', fk, col, track=.10, align='r')
    ex0, ex1 = X0 + bw + 14, X1 - 16 - sw - 14
    s = c.s
    rect = (round(ex0 * s), round((y0 + 10) * s), round((ex1 - ex0) * s), round(28 * s))
    return SimpleNamespace(rect=rect, bg=mix(BASE, col, .1), fg=INK, caret=col, masked=key,
                           font=('Consolas' if key else 'Segoe UI', -round(16 * s)))


# --- entry point -------------------------------------------------------------------------------
_now = time.monotonic


def clean(text):
    """Strip markdown and stray telemetry tags from a model reply."""
    text = re.sub(r'</?telemetry>', '', text or '')
    text = re.sub(r'(\*\*|__|`)', '', text)
    text = re.sub(r'^\s*#+\s*', '', text, flags=re.M)
    return re.sub(r'\n{2,}', '\n', text).strip()


def view(panel, fns):
    """Everything the painter needs, read once from the panel."""
    intel = panel.intel or {}
    v = SimpleNamespace(tabs=panel.TABS, tab=panel.tab, god=panel.cheats[0], invis=panel.cheats[1],
                        intel=intel if panel.intel else None, mission=panel.mission_id,
                        room=(panel.room or '').replace('_', ' '), chat=panel.chat, online=panel.dvorak_online,
                        busy=panel.dvorak_busy, typing=panel.typing, key_mode=panel.key_mode,
                        stage=(panel.bond_info[0].title() if panel.bond_info else ''), in_area=panel.in_area)
    rows = panel.objective_rows() if panel.mission_id else []
    v.must = [r for r in rows if r[2] in (0, 3)]
    v.extra = [r for r in rows if r[2] not in (0, 3) and r[1] == 0]
    v.done, v.total = sum(1 for r in v.must if r[1] == 1), len(v.must)
    v.nxt = panel.next_objective() if (panel.intel and panel.objs) else None
    v.has_dist = v.here_flag = False
    if v.nxt:
        v.next_title = panel.objective_title(v.nxt, 200)
        v.here_flag = panel.in_area(v.nxt)
        v.dist, bearing, dz = fns.relative(intel['sam'], v.nxt['loc'])
        clock = int(round(bearing / 30)) % 12 or 12
        v.bearing = "%d o'clock" % clock + ('' if abs(dz) < 2.5 else ' \u00b7 %.0fm %s' % (abs(dz), 'up' if dz > 0 else 'down'))
        v.has_dist = True
    else:
        pending = [r[0] for r in v.must if r[1] == 0]
        v.next_title = ('Get to extraction' if v.must and not pending else pending[0] if pending else '')
    v.route = None
    if v.nxt and not v.here_flag:
        path = panel.route_to(v.nxt) or []
        if len(path) > 1:
            rooms = [p.replace('_', ' ') for p in path[1:]]
            v.route = rooms[:3] + (['…'] if len(rooms) > 3 else [])
    v.here = panel.here_note() if panel.mission_id else None
    v.note = panel.note_for(v.nxt) if (panel.mission_id and (v.nxt or v.must)) else None
    fail = panel.rules.get((panel.mission_id or '').lower(), {}).get('fail', [])
    v.fail = fail[0] if fail else None
    v.markers = panel.visible_markers() if panel.intel else []
    v.threats = threats(v, fns) if v.intel else []
    v.alarm = bool(intel.get('alarm'))
    hunting = sum(n for label, n, _ in v.threats if label == 'ALERT')
    v.alarm_note = ('%d guard%s hunting \u00b7 stay dark' % (hunting, '' if hunting == 1 else 's')) if hunting else 'stay dark'
    return v


def paint(panel, fns, height=None):
    """The panel for its current tab and state, at `height` screen px (default: its current height)."""
    s = panel.scale
    W, Hpx = panel.size[0], height or panel.size[1]
    H = Hpx / s
    v = view(panel, fns)
    acc = RED if v.alarm else GREEN
    c = Canvas.panel(W, Hpx, s, acc)
    rail(c, H, acc)
    header(c, v, acc)
    tab, entry = panel.TABS[panel.tab], None
    if tab == 'RADAR':
        radar_tab(c, v, acc, H, fns)
    elif tab == 'INTEL':
        intel_tab(c, v, acc, H)
    else:
        entry = dvorak_tab(c, v, acc, H)
    footer(c, H, v)
    return c.result(), entry


def fold(img, h, s):
    """The panel at an in-between height while it glides to a new tab's size, made from one paint at the
    final size: header pinned to the top, body pinned to the bottom (so nothing moves on screen but the
    top edge), the gap filled with plain backdrop. Cheap enough for every frame."""
    W, H = img.size
    if h == H:
        return img
    b = min(H, round(72 * s))                    # header band: banner, wordmark, tabs, rule
    out = Image.new('RGBA', (W, h))
    out.paste(img.crop((0, 0, W, b)), (0, 0))
    if h < H:
        out.paste(img.crop((0, H - (h - b), W, H)), (0, b))
    else:
        row = round(75 * s)                      # a content-free row: backdrop and rail only
        out.paste(img.crop((0, row, W, row + 1)).resize((W, h - H)), (0, b))
        out.paste(img.crop((0, b, W, H)), (0, b + h - H))
    return out


def strip(rows, s):
    """Closed-panel HUD (bottom-left): cheats, the worst live threat, the 'press up' notice - in the panel's
    language: dark chamfered plate, lit edge in the row's colour, Bahnschrift caps."""
    if not rows:
        return None
    fb = font('B', 15 * s, 700)
    widths = [tlen(t, fb, .08) / s + 40 for t, _ in rows]
    W, rh, gap = max(widths), 32, 6
    H = len(rows) * rh + (len(rows) - 1) * gap
    size = (round(W * s), round(H * s))
    c = Canvas(Image.new('RGB', size, rgb(BASE)), Image.new('L', size, 0), s, Image.new('L', size, 0))
    for i, ((text, col), w) in enumerate(zip(rows, widths)):
        y = i * (rh + gap)
        c.plate(0, y, w, y + rh, col, .16, .08, cut=8, under=.9)
        c.rect(0, y, 3, y + rh, fill=rgba(col))
        c.text(16, y + 21.5, text, fb, col, track=.08)
    return c.result()
