# -*- coding: utf-8 -*-
"""Character-box screenshot recognition.

Pipeline: detect square portrait cells -> per-cell icon match against the local
unit-icon library -> return top-k candidates.  The UI always lets the user
confirm/correct, because frames, skins and new units can reduce confidence.
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ICON_DIR = os.path.join(DATA, "icons")
CUSTOM_DIR = os.path.join(DATA, "icons_custom")
CUSTOM_INDEX = os.path.join(DATA, "icon_index_custom.json")
GW_DIR = os.path.join(DATA, "icons_gw")
GW_INDEX = os.path.join(DATA, "icon_index_gw.json")
ICON_INDEX = os.path.join(DATA, "icon_index_all.json")


class IconLibrary:
    def __init__(self, index_path: str = ICON_INDEX, icon_dir: str = ICON_DIR,
                 custom_index: Optional[str] = CUSTOM_INDEX,
                 custom_dir: Optional[str] = CUSTOM_DIR):
        self.entries: List[dict] = []
        self.extra = []
        if custom_index and os.path.exists(custom_index):
            try:
                with open(custom_index, encoding="utf-8") as f:
                    self.extra = json.load(f)
            except Exception:  # noqa: BLE001
                self.extra = []
        self._gray: List[np.ndarray] = []
        self._scaled: List[List[np.ndarray]] = []
        self._hist: List[np.ndarray] = []
        self._loaded = False
        self.index_path = index_path
        self.icon_dir = icon_dir

    def load(self):
        if self._loaded:
            return self
        if not os.path.exists(self.index_path):
            self._loaded = True
            return self
        with open(self.index_path, encoding="utf-8") as f:
            entries = json.load(f)
        gw_entries = []
        if os.path.exists(GW_INDEX):
            try:
                with open(GW_INDEX, encoding="utf-8") as f:
                    gw_entries = json.load(f)
            except Exception:  # noqa: BLE001
                gw_entries = []
        entries = entries + gw_entries + self.extra
        for e in entries:
            if e.get("source") == "custom":
                base = CUSTOM_DIR
            elif e.get("source") == "gw":
                base = GW_DIR
            else:
                base = self.icon_dir
            path = os.path.join(base, e["file"])
            if not os.path.exists(path):
                continue
            img = self._load_icon(path)
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            self.entries.append(e)
            self._gray.append(gray)
            self._scaled.append([cv2.resize(gray, (int(128 * sc), int(128 * sc)),
                                            interpolation=cv2.INTER_AREA)
                                 for sc in (0.78, 0.86, 0.94, 1.0)])
            self._hist.append(self._histogram(img))
        self._loaded = True
        self._index_groups()
        return self

    def _index_groups(self):
        self.groups = {}
        for i, e in enumerate(self.entries):
            self.groups.setdefault(e["name"], []).append(i)
        # primary = first (base library first, then GW, then custom)
        self.primary = [idxs[0] for idxs in self.groups.values()]
        self.names = list(self.groups)
        self.hist_matrix = np.stack([self._hist[i] for i in self.primary]).reshape(len(self.primary), -1)

    @staticmethod
    def _load_icon(path: str) -> Optional[np.ndarray]:
        try:
            im = Image.open(path).convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im).convert("RGB").resize((128, 128), Image.BILINEAR)
            return np.array(im)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _histogram(img: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [18, 6], [0, 180, 0, 256])
        cv2.normalize(h, h, 1, 0, cv2.NORM_L1)
        return h

    @property
    def size(self) -> int:
        return len(self.entries)


_LIBRARY: Optional[IconLibrary] = None
_LIB_LOCK = threading.Lock()


def library() -> IconLibrary:
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY
    with _LIB_LOCK:
        if _LIBRARY is None:
            _LIBRARY = IconLibrary().load()
    return _LIBRARY


def decode_image(data: str) -> Optional[np.ndarray]:
    """Accept a data URL or raw base64 string."""
    try:
        if "," in data and data.strip().lower().startswith("data:"):
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.array(im)
    except Exception:  # noqa: BLE001
        return None


def detect_cells(img: np.ndarray, max_cells: int = 12) -> List[Tuple[int, int, int, int]]:
    """Find square-ish portrait cells on a light background."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    # non-white / coloured content
    mask = cv2.inRange(hsv, (0, 25, 0), (180, 255, 252))
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask[:3, :] = 0
    mask[-3:, :] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    min_area = max(400, int(h * w * 0.004))
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw * ch < min_area:
            continue
        if not (0.62 <= cw / max(1, ch) <= 1.55):
            continue
        boxes.append((x, y, cw, ch))
    # de-duplicate overlapping boxes (keep larger)
    boxes.sort(key=lambda b: -b[2] * b[3])
    kept = []
    for b in boxes:
        if any(_overlap(b, k) > 0.35 for k in kept):
            continue
        kept.append(b)
    kept.sort(key=lambda b: (b[1] // max(1, h // 3), b[0]))
    return kept[:max_cells]


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / min(aw * ah, bw * bh)


def _cell_features(cell: np.ndarray):
    """Crop the inner portrait (drop level/star overlays at the corners)."""
    h, w = cell.shape[:2]
    inner = cell[int(h * 0.10):int(h * 0.94), int(w * 0.08):int(w * 0.94)]
    inner = cv2.resize(inner, (128, 128), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(inner, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(inner, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 6], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1, 0, cv2.NORM_L1)
    return gray, hist


def match_cell(cell: np.ndarray, lib: IconLibrary, topk: int = 3) -> List[dict]:
    gray, hist = _cell_features(cell)
    # stage 1: cheap single-scale template pass over primary icons
    scored = []
    for ci, i in enumerate(lib.primary):
        best = 0.0
        for tpl in lib._scaled[i][-2:]:
            if tpl.shape[0] > gray.shape[0]:
                continue
            r = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED).max()
            best = max(best, float(r))
        scored.append((best, lib.entries[i]["name"]))
    scored.sort(reverse=True)
    top_names = []
    for _, n in scored:
        if n not in top_names:
            top_names.append(n)
        if len(top_names) >= 50:
            break
    # stage 2: refine with all rarity variants / icon sources
    final = {}
    for name in top_names:
        for i in lib.groups[name]:
            best = 0.0
            for tpl in lib._scaled[i]:
                if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
                    continue
                r = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED).max()
                best = max(best, float(r))
            hscore = float(cv2.compareHist(hist, lib._hist[i], cv2.HISTCMP_INTERSECT))
            s = 0.7 * best + 0.3 * hscore
            if name not in final or s > final[name][0]:
                final[name] = (s, best, hscore, lib.entries[i].get("region", "cn"))
    out = []
    for name, (s, tm, hs, region) in sorted(final.items(), key=lambda kv: -kv[1][0]):
        out.append({"name": name, "region": region,
                    "score": round(max(0.0, min(1.0, s)), 3),
                    "template": round(tm, 3), "color": round(hs, 3)})
        if len(out) >= topk:
            break
    return out


def recognize(img: np.ndarray, topk: int = 3) -> dict:
    lib = library()
    if not lib.size:
        return {"ok": False, "error": "图标库为空，请先运行 scripts/scrape_icons.py"}
    cells = detect_cells(img)
    results = []
    for (x, y, w, h) in cells:
        crop = img[y:y + h, x:x + w]
        if crop.size == 0:
            continue
        cands = match_cell(crop, lib, topk=topk)
        margin = (cands[0]["score"] - cands[1]["score"]) if len(cands) > 1 else (cands[0]["score"] if cands else 0.0)
        results.append({"bbox": [int(x), int(y), int(w), int(h)],
                        "candidates": cands,
                        "confidence": cands[0]["score"] if cands else 0.0,
                        "margin": round(margin, 3)})
    return {"ok": True, "cells": results, "count": len(results),
            "library": len({e["name"] for e in lib.entries}), "icons": lib.size}
