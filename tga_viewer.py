"""TGA 스프라이트 뷰어
- 모드 1 (기본): 폴더 내 스프라이트 그룹별 첫 이미지 썸네일 목록
- 모드 2 (--filter): 특정 이름 필터로 전체 프레임 보기
"""

import sys
import os
import re
import struct
import numpy as np
from pathlib import Path
from collections import OrderedDict

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk


def load_tga_raw(path: str) -> np.ndarray:
    """TGA 수동 파싱 (PIL 깨짐 방지) → RGBA numpy 반환"""
    with open(path, 'rb') as f:
        header = f.read(18)
        id_len = header[0]
        img_type = header[2]
        w = struct.unpack('<H', header[12:14])[0]
        h = struct.unpack('<H', header[14:16])[0]
        bpp = header[16]
        desc = header[17]

        if id_len > 0:
            f.read(id_len)

        channels = bpp // 8
        data = f.read(w * h * channels)

    arr = np.frombuffer(data, dtype=np.uint8).reshape((h, w, channels))

    # 상하 반전 체크 (origin bit)
    origin_top = (desc >> 5) & 1
    if not origin_top:
        arr = arr[::-1].copy()

    # BGRA → RGBA
    if channels == 4:
        rgba = arr.copy()
        rgba[:, :, 0] = arr[:, :, 2]  # R
        rgba[:, :, 2] = arr[:, :, 0]  # B
        return rgba
    elif channels == 3:
        rgb = arr.copy()
        rgb[:, :, 0] = arr[:, :, 2]
        rgb[:, :, 2] = arr[:, :, 0]
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = 255
        return rgba
    return arr


def alpha_composite_black(img_rgba: np.ndarray) -> np.ndarray:
    """RGBA → 검정 배경 합성 → RGB"""
    alpha = img_rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = img_rgba[:, :, :3].astype(np.float32)
    return (rgb * alpha).astype(np.uint8)


def get_sprite_groups(folder: str) -> OrderedDict:
    """폴더 내 TGA를 그룹별로 분류. {그룹명: [파일경로,...]}"""
    files = sorted(Path(folder).glob("*.tga"),
                   key=lambda f: natural_sort_key(f.name))
    groups = OrderedDict()
    for f in files:
        # 그룹명: 마지막 _숫자 제거
        name = f.stem
        match = re.match(r'^(.+?)_(\d+)$', name)
        group = match.group(1) if match else name
        if group not in groups:
            groups[group] = []
        groups[group].append(str(f))
    return groups


def natural_sort_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


class TGAViewer:
    def __init__(self, folder: str, name_filter: str = None):
        self.root = tk.Tk()
        self.folder = folder
        self.name_filter = name_filter

        self.root.configure(bg="#1e1e1e")
        self._photo_refs = []

        if name_filter:
            # 모드 2: 특정 그룹 전체 프레임
            self._show_frames(name_filter)
        else:
            # 모드 1: 그룹별 썸네일 목록
            self._show_groups()

    def _show_groups(self):
        """그룹별 첫 이미지 + 이름 썸네일 목록"""
        groups = get_sprite_groups(self.folder)
        self.root.title(f"스프라이트 목록 — {len(groups)}개 그룹 ({self.folder})")
        self.root.geometry("1000x700")

        # 스크롤
        container = tk.Frame(self.root, bg="#1e1e1e")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        canvas = tk.Canvas(container, bg="#1e1e1e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        self.scroll_frame = tk.Frame(canvas, bg="#1e1e1e")
        self.scroll_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        cols = 6
        for i, (group_name, file_list) in enumerate(groups.items()):
            row, col = i // cols, i % cols
            self._add_group_cell(row, col, group_name, file_list)

    def _add_group_cell(self, row: int, col: int, group_name: str, file_list: list):
        """그룹 셀: 첫 이미지 썸네일 + 그룹명 + 프레임 수"""
        frame = tk.Frame(self.scroll_frame, bg="#2d2d2d", bd=1, relief=tk.RIDGE)
        frame.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

        # 첫 이미지 로드
        try:
            img_rgba = load_tga_raw(file_list[0])
            display = alpha_composite_black(img_rgba)
            h, w = display.shape[:2]
            scale = min(120 / max(w, 1), 120 / max(h, 1), 1.0)
            thumb = Image.fromarray(display).resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            self._photo_refs.append(photo)
            lbl_img = tk.Label(frame, image=photo, bg="#2d2d2d")
        except Exception:
            lbl_img = tk.Label(frame, text="?", bg="#2d2d2d", fg="#888",
                               width=15, height=7)

        lbl_img.pack(padx=4, pady=4)

        # 그룹명
        tk.Label(frame, text=group_name, bg="#2d2d2d", fg="#fff",
                 font=("맑은 고딕", 8), wraplength=140).pack()

        # 프레임 수
        tk.Label(frame, text=f"{len(file_list)} frames", bg="#2d2d2d", fg="#888",
                 font=("맑은 고딕", 7)).pack()

        # 클릭 → 해당 그룹 프레임 보기
        frame.bind("<Button-1>", lambda e, g=group_name: self._open_group(g))
        lbl_img.bind("<Button-1>", lambda e, g=group_name: self._open_group(g))
        frame.config(cursor="hand2")
        lbl_img.config(cursor="hand2")

    def _open_group(self, group_name: str):
        """그룹 클릭 → 새 창에서 전체 프레임 보기"""
        sub = tk.Toplevel(self.root)
        sub.title(f"{group_name} — 프레임 목록")
        sub.geometry("1100x700")
        sub.configure(bg="#1e1e1e")

        container = tk.Frame(sub, bg="#1e1e1e")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        canvas = tk.Canvas(container, bg="#1e1e1e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1e1e1e")
        scroll_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        groups = get_sprite_groups(self.folder)
        file_list = groups.get(group_name, [])

        cols = 8
        for i, fpath in enumerate(file_list):
            row, col = i // cols, i % cols
            cell = tk.Frame(scroll_frame, bg="#2d2d2d", bd=1, relief=tk.RIDGE)
            cell.grid(row=row, column=col, padx=3, pady=3)

            try:
                img_rgba = load_tga_raw(fpath)
                display = alpha_composite_black(img_rgba)
                h, w = display.shape[:2]
                scale = min(100 / max(w, 1), 100 / max(h, 1), 1.0)
                thumb = Image.fromarray(display).resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                photo = ImageTk.PhotoImage(thumb)
                self._photo_refs.append(photo)
                tk.Label(cell, image=photo, bg="#2d2d2d").pack(padx=2, pady=2)
            except Exception:
                tk.Label(cell, text="?", bg="#2d2d2d", fg="#888").pack(padx=2, pady=2)

            name = Path(fpath).stem
            num = name.rsplit("_", 1)[-1] if "_" in name else name
            tk.Label(cell, text=num, bg="#2d2d2d", fg="#aaa",
                     font=("맑은 고딕", 7)).pack()

    def _show_frames(self, name_filter: str):
        """특정 필터로 전체 프레임 보기"""
        self.root.title(f"TGA Viewer — {name_filter}")
        self.root.geometry("1100x700")

        groups = get_sprite_groups(self.folder)
        # 필터에 매칭되는 그룹의 파일들
        files = []
        for g, flist in groups.items():
            if name_filter in g:
                files.extend(flist)

        if not files:
            tk.Label(self.root, text=f"'{name_filter}' 매칭 파일 없음",
                     bg="#1e1e1e", fg="#fff", font=("맑은 고딕", 14)).pack(pady=50)
            return

        tk.Label(self.root, text=f"{name_filter} — {len(files)}개",
                 bg="#1e1e1e", fg="#fff", font=("맑은 고딕", 14)).pack(pady=5)

        container = tk.Frame(self.root, bg="#1e1e1e")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        canvas = tk.Canvas(container, bg="#1e1e1e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1e1e1e")
        scroll_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        cols = 8
        for i, fpath in enumerate(files):
            row, col = i // cols, i % cols
            cell = tk.Frame(scroll_frame, bg="#2d2d2d", bd=1, relief=tk.RIDGE)
            cell.grid(row=row, column=col, padx=3, pady=3)
            try:
                img_rgba = load_tga_raw(fpath)
                display = alpha_composite_black(img_rgba)
                h, w = display.shape[:2]
                scale = min(100 / max(w, 1), 100 / max(h, 1), 1.0)
                thumb = Image.fromarray(display).resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                photo = ImageTk.PhotoImage(thumb)
                self._photo_refs.append(photo)
                tk.Label(cell, image=photo, bg="#2d2d2d").pack(padx=2, pady=2)
            except Exception:
                tk.Label(cell, text="?", bg="#2d2d2d", fg="#888").pack()
            tk.Label(cell, text=Path(fpath).stem.rsplit("_",1)[-1],
                     bg="#2d2d2d", fg="#aaa", font=("맑은 고딕", 7)).pack()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "E:/Project/smart_clicker/TGA_file"
    name_filter = sys.argv[2] if len(sys.argv) > 2 else None
    viewer = TGAViewer(folder, name_filter)
    viewer.run()
