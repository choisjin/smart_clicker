"""
Monster Detector - 게임 스크린샷에서 몬스터 템플릿 매칭 & 바운딩 박스
사용법: python monster_detector.py
필요 패키지: pip install opencv-python numpy Pillow
"""

import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import cv2
import numpy as np
from PIL import Image, ImageTk
import os
import json
from pathlib import Path


class MonsterDetector:
    def __init__(self, root):
        self.root = root
        self.root.title("Monster Detector - 템플릿 매칭")
        self.root.geometry("1400x900")
        self.root.configure(bg="#1e1e1e")

        # State
        self.screenshot_path = None
        self.screenshot_cv = None
        self.screenshot_display = None
        self.result_cv = None
        self.templates = []  # list of {"name", "path", "image", "color"}
        self.threshold = 0.75
        self.use_multiscale = False
        self.scale_range = (0.5, 1.5)
        self.scale_steps = 10
        self.detections = []

        # Colors for different templates
        self.box_colors = [
            (0, 255, 0),    # Green
            (255, 0, 0),    # Blue (BGR)
            (0, 0, 255),    # Red
            (255, 255, 0),  # Cyan
            (0, 255, 255),  # Yellow
            (255, 0, 255),  # Magenta
            (128, 255, 0),  # Lime
            (255, 128, 0),  # Light Blue
            (0, 128, 255),  # Orange
            (255, 0, 128),  # Pink
        ]

        self._build_ui()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background="#1e1e1e")
        style.configure("Dark.TLabel", background="#1e1e1e", foreground="#e0e0e0",
                         font=("맑은 고딕", 10))
        style.configure("Dark.TButton", font=("맑은 고딕", 10))
        style.configure("Header.TLabel", background="#1e1e1e", foreground="#ffffff",
                         font=("맑은 고딕", 14, "bold"))
        style.configure("Sub.TLabel", background="#1e1e1e", foreground="#aaaaaa",
                         font=("맑은 고딕", 9))
        style.configure("Dark.TLabelframe", background="#1e1e1e", foreground="#e0e0e0",
                         font=("맑은 고딕", 10))
        style.configure("Dark.TLabelframe.Label", background="#1e1e1e", foreground="#e0e0e0")
        style.configure("Dark.TCheckbutton", background="#1e1e1e", foreground="#e0e0e0",
                         font=("맑은 고딕", 10))

        # Main layout: left panel (controls) + right panel (image)
        main_frame = ttk.Frame(self.root, style="Dark.TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ---- Left Panel (Controls) ----
        left_panel = ttk.Frame(main_frame, style="Dark.TFrame", width=360)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        left_panel.pack_propagate(False)

        ttk.Label(left_panel, text="🎮 Monster Detector", style="Header.TLabel").pack(
            pady=(10, 2))
        ttk.Label(left_panel, text="템플릿 매칭으로 몬스터 탐지", style="Sub.TLabel").pack(
            pady=(0, 10))

        # Screenshot section
        screenshot_frame = ttk.LabelFrame(left_panel, text=" 📷 스크린샷 ",
                                           style="Dark.TLabelframe")
        screenshot_frame.pack(fill=tk.X, padx=10, pady=5)

        self.screenshot_label = ttk.Label(screenshot_frame, text="선택된 파일 없음",
                                           style="Sub.TLabel", wraplength=300)
        self.screenshot_label.pack(padx=10, pady=(5, 2))

        btn_frame_ss = ttk.Frame(screenshot_frame, style="Dark.TFrame")
        btn_frame_ss.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(btn_frame_ss, text="스크린샷 불러오기",
                   command=self._load_screenshot).pack(fill=tk.X)

        # Template section
        template_frame = ttk.LabelFrame(left_panel, text=" 👾 몬스터 템플릿 ",
                                         style="Dark.TLabelframe")
        template_frame.pack(fill=tk.X, padx=10, pady=5)

        btn_frame_tp = ttk.Frame(template_frame, style="Dark.TFrame")
        btn_frame_tp.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(btn_frame_tp, text="템플릿 추가 (파일)",
                   command=self._add_template_files).pack(fill=tk.X, pady=1)
        ttk.Button(btn_frame_tp, text="템플릿 추가 (폴더)",
                   command=self._add_template_folder).pack(fill=tk.X, pady=1)
        ttk.Button(btn_frame_tp, text="템플릿 전체 삭제",
                   command=self._clear_templates).pack(fill=tk.X, pady=1)

        # Template listbox
        list_frame = ttk.Frame(template_frame, style="Dark.TFrame")
        list_frame.pack(fill=tk.BOTH, padx=10, pady=(0, 5), expand=True)

        self.template_listbox = tk.Listbox(list_frame, height=6, bg="#2d2d2d",
                                            fg="#e0e0e0", selectbackground="#4a4a4a",
                                            font=("맑은 고딕", 9), relief=tk.FLAT)
        self.template_listbox.pack(fill=tk.BOTH, expand=True)

        # Settings section
        settings_frame = ttk.LabelFrame(left_panel, text=" ⚙️ 설정 ",
                                         style="Dark.TLabelframe")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)

        # Threshold
        threshold_frame = ttk.Frame(settings_frame, style="Dark.TFrame")
        threshold_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(threshold_frame, text="매칭 임계값:", style="Dark.TLabel").pack(
            side=tk.LEFT)
        self.threshold_var = tk.DoubleVar(value=0.75)
        self.threshold_label = ttk.Label(threshold_frame, text="0.75",
                                          style="Dark.TLabel", width=5)
        self.threshold_label.pack(side=tk.RIGHT)
        threshold_scale = ttk.Scale(threshold_frame, from_=0.3, to=0.99,
                                     variable=self.threshold_var, orient=tk.HORIZONTAL,
                                     command=self._on_threshold_change)
        threshold_scale.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)

        # Multi-scale option
        self.multiscale_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings_frame, text="멀티스케일 매칭 (느리지만 정확도 ↑)",
                         variable=self.multiscale_var,
                         style="Dark.TCheckbutton").pack(padx=10, pady=2, anchor=tk.W)

        # Scale range
        scale_frame = ttk.Frame(settings_frame, style="Dark.TFrame")
        scale_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        ttk.Label(scale_frame, text="스케일 범위:", style="Dark.TLabel").pack(side=tk.LEFT)
        self.scale_min_var = tk.DoubleVar(value=0.5)
        self.scale_max_var = tk.DoubleVar(value=1.5)
        ttk.Entry(scale_frame, textvariable=self.scale_min_var, width=5,
                  font=("맑은 고딕", 9)).pack(side=tk.LEFT, padx=2)
        ttk.Label(scale_frame, text="~", style="Dark.TLabel").pack(side=tk.LEFT)
        ttk.Entry(scale_frame, textvariable=self.scale_max_var, width=5,
                  font=("맑은 고딕", 9)).pack(side=tk.LEFT, padx=2)

        # NMS threshold
        nms_frame = ttk.Frame(settings_frame, style="Dark.TFrame")
        nms_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        ttk.Label(nms_frame, text="NMS IoU 임계값:", style="Dark.TLabel").pack(side=tk.LEFT)
        self.nms_var = tk.DoubleVar(value=0.3)
        ttk.Entry(nms_frame, textvariable=self.nms_var, width=5,
                  font=("맑은 고딕", 9)).pack(side=tk.LEFT, padx=5)

        # Method selection
        method_frame = ttk.Frame(settings_frame, style="Dark.TFrame")
        method_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        ttk.Label(method_frame, text="매칭 방식:", style="Dark.TLabel").pack(side=tk.LEFT)
        self.method_var = tk.StringVar(value="TM_CCOEFF_NORMED")
        method_combo = ttk.Combobox(method_frame, textvariable=self.method_var,
                                     values=["TM_CCOEFF_NORMED", "TM_CCORR_NORMED",
                                             "TM_SQDIFF_NORMED"],
                                     state="readonly", width=20, font=("맑은 고딕", 9))
        method_combo.pack(side=tk.LEFT, padx=5)

        # Run button
        ttk.Button(left_panel, text="🔍 탐지 실행",
                   command=self._run_detection).pack(fill=tk.X, padx=10, pady=10)

        # Detection results
        result_frame = ttk.LabelFrame(left_panel, text=" 📊 탐지 결과 ",
                                       style="Dark.TLabelframe")
        result_frame.pack(fill=tk.BOTH, padx=10, pady=5, expand=True)

        self.result_text = tk.Text(result_frame, height=6, bg="#2d2d2d", fg="#e0e0e0",
                                    font=("Consolas", 9), relief=tk.FLAT, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Save button
        ttk.Button(left_panel, text="💾 결과 이미지 저장",
                   command=self._save_result).pack(fill=tk.X, padx=10, pady=(0, 10))

        # ---- Right Panel (Image Display) ----
        right_panel = ttk.Frame(main_frame, style="Dark.TFrame")
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(right_panel, bg="#2d2d2d", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Scrollbars
        h_scroll = ttk.Scrollbar(right_panel, orient=tk.HORIZONTAL,
                                  command=self.canvas.xview)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        v_scroll = ttk.Scrollbar(right_panel, orient=tk.VERTICAL,
                                  command=self.canvas.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        # Zoom controls
        zoom_frame = ttk.Frame(right_panel, style="Dark.TFrame")
        zoom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=2)
        ttk.Button(zoom_frame, text="−", width=3,
                   command=lambda: self._zoom(-0.1)).pack(side=tk.LEFT, padx=2)
        self.zoom_label = ttk.Label(zoom_frame, text="100%", style="Dark.TLabel")
        self.zoom_label.pack(side=tk.LEFT, padx=5)
        ttk.Button(zoom_frame, text="+", width=3,
                   command=lambda: self._zoom(0.1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(zoom_frame, text="맞추기", width=6,
                   command=self._zoom_fit).pack(side=tk.LEFT, padx=5)

        self.zoom_level = 1.0

    # ---- Screenshot ----
    def _load_screenshot(self):
        path = filedialog.askopenfilename(
            title="스크린샷 선택",
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.tga *.tif"),
                       ("모든 파일", "*.*")])
        if not path:
            return
        self.screenshot_path = path
        self.screenshot_cv = cv2.imread(path, cv2.IMREAD_COLOR)
        if self.screenshot_cv is None:
            messagebox.showerror("오류", "이미지를 불러올 수 없습니다.")
            return
        self.screenshot_label.config(
            text=f"{os.path.basename(path)}\n({self.screenshot_cv.shape[1]}x{self.screenshot_cv.shape[0]})")
        self.result_cv = self.screenshot_cv.copy()
        self._display_image(self.result_cv)

    # ---- Templates ----
    def _add_template_files(self):
        paths = filedialog.askopenfilenames(
            title="템플릿 이미지 선택",
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.tga *.tif"),
                       ("모든 파일", "*.*")])
        for p in paths:
            self._add_single_template(p)

    def _add_template_folder(self):
        folder = filedialog.askdirectory(title="템플릿 폴더 선택")
        if not folder:
            return
        exts = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif"}
        for f in sorted(Path(folder).iterdir()):
            if f.suffix.lower() in exts:
                self._add_single_template(str(f))

    def _add_single_template(self, path):
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            return
        name = os.path.basename(path)
        color = self.box_colors[len(self.templates) % len(self.box_colors)]
        self.templates.append({"name": name, "path": path, "image": img, "color": color})
        color_hex = f"#{color[2]:02x}{color[1]:02x}{color[0]:02x}"
        self.template_listbox.insert(tk.END, f"■ {name} ({img.shape[1]}x{img.shape[0]})")
        self.template_listbox.itemconfig(tk.END, fg=color_hex)

    def _clear_templates(self):
        self.templates.clear()
        self.template_listbox.delete(0, tk.END)

    # ---- Settings ----
    def _on_threshold_change(self, val):
        v = float(val)
        self.threshold_label.config(text=f"{v:.2f}")

    # ---- Detection ----
    def _run_detection(self):
        if self.screenshot_cv is None:
            messagebox.showwarning("경고", "스크린샷을 먼저 불러오세요.")
            return
        if not self.templates:
            messagebox.showwarning("경고", "템플릿을 추가하세요.")
            return

        self.threshold = self.threshold_var.get()
        method_name = self.method_var.get()
        method = getattr(cv2, method_name)
        is_sqdiff = "SQDIFF" in method_name

        self.result_cv = self.screenshot_cv.copy()
        self.detections.clear()
        self.result_text.delete("1.0", tk.END)

        screenshot_gray = cv2.cvtColor(self.screenshot_cv, cv2.COLOR_BGR2GRAY)
        ss_h, ss_w = screenshot_gray.shape[:2]

        for tmpl in self.templates:
            tmpl_img = tmpl["image"]
            # Handle alpha channel - use it as mask
            mask = None
            if len(tmpl_img.shape) == 3 and tmpl_img.shape[2] == 4:
                alpha = tmpl_img[:, :, 3]
                # Only use mask if there are transparent pixels
                if np.any(alpha < 255):
                    mask = alpha
                tmpl_bgr = tmpl_img[:, :, :3]
            else:
                tmpl_bgr = tmpl_img

            tmpl_gray = cv2.cvtColor(tmpl_bgr, cv2.COLOR_BGR2GRAY)
            t_h, t_w = tmpl_gray.shape[:2]

            boxes = []
            scores = []

            if self.multiscale_var.get():
                scale_min = self.scale_min_var.get()
                scale_max = self.scale_max_var.get()
                steps = self.scale_steps
                scales = np.linspace(scale_min, scale_max, steps)
            else:
                scales = [1.0]

            for scale in scales:
                new_w = int(t_w * scale)
                new_h = int(t_h * scale)
                if new_w < 5 or new_h < 5 or new_w > ss_w or new_h > ss_h:
                    continue

                resized_tmpl = cv2.resize(tmpl_gray, (new_w, new_h),
                                           interpolation=cv2.INTER_AREA)
                resized_mask = None
                if mask is not None:
                    resized_mask = cv2.resize(mask, (new_w, new_h),
                                              interpolation=cv2.INTER_AREA)

                try:
                    if resized_mask is not None and method_name == "TM_CCOEFF_NORMED":
                        result = cv2.matchTemplate(screenshot_gray, resized_tmpl,
                                                    method, mask=resized_mask)
                    else:
                        result = cv2.matchTemplate(screenshot_gray, resized_tmpl, method)
                except cv2.error:
                    continue

                if is_sqdiff:
                    locs = np.where(result <= (1 - self.threshold))
                else:
                    locs = np.where(result >= self.threshold)

                for pt in zip(*locs[::-1]):
                    score = float(result[pt[1], pt[0]])
                    if is_sqdiff:
                        score = 1 - score
                    boxes.append([pt[0], pt[1], pt[0] + new_w, pt[1] + new_h])
                    scores.append(score)

            # NMS
            if boxes:
                boxes_arr = np.array(boxes)
                scores_arr = np.array(scores)
                keep = self._nms(boxes_arr, scores_arr, self.nms_var.get())

                color = tmpl["color"]
                for idx in keep:
                    x1, y1, x2, y2 = boxes_arr[idx]
                    score = scores_arr[idx]
                    self.detections.append({
                        "name": tmpl["name"],
                        "box": [int(x1), int(y1), int(x2), int(y2)],
                        "score": float(score),
                        "color": color
                    })

                    cv2.rectangle(self.result_cv, (int(x1), int(y1)),
                                  (int(x2), int(y2)), color, 2)

                    label = f"{tmpl['name']} {score:.2f}"
                    (tw, th), baseline = cv2.getTextSize(
                        label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    cv2.rectangle(self.result_cv,
                                  (int(x1), int(y1) - th - baseline - 4),
                                  (int(x1) + tw + 4, int(y1)), color, -1)
                    cv2.putText(self.result_cv, label,
                                (int(x1) + 2, int(y1) - baseline - 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Update results text
        self.result_text.insert(tk.END, f"총 탐지 수: {len(self.detections)}\n\n")
        counts = {}
        for d in self.detections:
            counts[d["name"]] = counts.get(d["name"], 0) + 1
        for name, count in counts.items():
            self.result_text.insert(tk.END, f"  {name}: {count}개\n")

        if not self.detections:
            self.result_text.insert(tk.END, "탐지된 몬스터가 없습니다.\n"
                                             "임계값을 낮추거나 멀티스케일을 켜보세요.")

        self._display_image(self.result_cv)

    def _nms(self, boxes, scores, iou_threshold):
        """Non-Maximum Suppression"""
        if len(boxes) == 0:
            return []

        x1 = boxes[:, 0].astype(float)
        y1 = boxes[:, 1].astype(float)
        x2 = boxes[:, 2].astype(float)
        y2 = boxes[:, 3].astype(float)

        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]

        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)

            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0, xx2 - xx1)
            h = np.maximum(0, yy2 - yy1)
            inter = w * h

            iou = inter / (areas[i] + areas[order[1:]] - inter)
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]

        return keep

    # ---- Display ----
    def _display_image(self, cv_img):
        if cv_img is None:
            return
        h, w = cv_img.shape[:2]
        new_w = int(w * self.zoom_level)
        new_h = int(h * self.zoom_level)
        resized = cv2.resize(cv_img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        self.screenshot_display = ImageTk.PhotoImage(pil_img)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.screenshot_display)
        self.canvas.configure(scrollregion=(0, 0, new_w, new_h))

    def _zoom(self, delta):
        self.zoom_level = max(0.1, min(5.0, self.zoom_level + delta))
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")
        img = self.result_cv if self.result_cv is not None else self.screenshot_cv
        if img is not None:
            self._display_image(img)

    def _zoom_fit(self):
        if self.result_cv is None and self.screenshot_cv is None:
            return
        img = self.result_cv if self.result_cv is not None else self.screenshot_cv
        h, w = img.shape[:2]
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        if canvas_w < 10 or canvas_h < 10:
            return
        self.zoom_level = min(canvas_w / w, canvas_h / h)
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")
        self._display_image(img)

    # ---- Save ----
    def _save_result(self):
        if self.result_cv is None:
            messagebox.showwarning("경고", "탐지 결과가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            title="결과 이미지 저장",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")])
        if path:
            cv2.imwrite(path, self.result_cv)

            # Also save detection data as JSON
            json_path = path.rsplit(".", 1)[0] + "_detections.json"
            json_data = []
            for d in self.detections:
                json_data.append({
                    "name": d["name"],
                    "box": d["box"],
                    "score": round(d["score"], 4)
                })
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)

            messagebox.showinfo("저장 완료",
                                f"이미지: {path}\n탐지 데이터: {json_path}")


if __name__ == "__main__":
    root = tk.Tk()
    app = MonsterDetector(root)
    root.mainloop()