"""도트 영상 변환기 - 윈도우 GUI (tkinter)."""
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

import pixelate_core as core

PREVIEW_W, PREVIEW_H = 360, 420


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("도트 영상 변환기")
        self.geometry("1000x640")
        self.minsize(900, 600)

        self.previewer = core.Previewer()
        self.worker = None
        self.cancel_evt = threading.Event()
        self._after_id = None

        self.v_src = tk.StringVar()
        self.v_dst = tk.StringVar()
        self.v_grid = tk.IntVar(value=192)
        self.v_colors = tk.IntVar(value=48)
        self.v_palette = tk.StringVar(value=core.AUTO_PALETTE)
        self.v_dither = tk.StringVar(value="4x4 규칙 디더")
        self.v_dstr = tk.DoubleVar(value=5)
        self.v_sat = tk.DoubleVar(value=1.1)
        self.v_con = tk.DoubleVar(value=1.05)
        self.v_scale = tk.IntVar(value=0)
        self.v_audio = tk.BooleanVar(value=True)
        self.v_pos = tk.DoubleVar(value=0.3)

        self._build()

    # ------------------------------------------------------------ UI
    def _slider(self, parent, label, var, lo, hi, row, res=1, fmt="{:.0f}"):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        val = ttk.Label(parent, width=5, anchor="e")
        val.grid(row=row, column=2, sticky="e")

        def on(_=None):
            v = var.get()
            val.config(text=fmt.format(v))
            self._schedule_preview()

        s = ttk.Scale(parent, from_=lo, to=hi, variable=var, command=on)
        s.grid(row=row, column=1, sticky="ew", padx=6)
        val.config(text=fmt.format(var.get()))
        return s

    def _build(self):
        left = ttk.Frame(self, padding=12)
        left.pack(side="left", fill="y")
        right = ttk.Frame(self, padding=12)
        right.pack(side="right", fill="both", expand=True)

        # 파일
        f = ttk.LabelFrame(left, text="파일", padding=8)
        f.pack(fill="x")
        ttk.Entry(f, textvariable=self.v_src, width=34).grid(row=0, column=0, sticky="ew")
        ttk.Button(f, text="영상 선택", command=self.pick_src).grid(row=0, column=1, padx=4)
        ttk.Entry(f, textvariable=self.v_dst, width=34).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(f, text="저장 위치", command=self.pick_dst).grid(row=1, column=1, padx=4)

        # 설정
        s = ttk.LabelFrame(left, text="도트 설정", padding=8)
        s.pack(fill="x", pady=10)
        s.columnconfigure(1, weight=1)
        self._slider(s, "도트 해상도 (가로 개수)", self.v_grid, 48, 480, 0)
        self._slider(s, "색 개수", self.v_colors, 2, 128, 1)

        ttk.Label(s, text="팔레트").grid(row=2, column=0, sticky="w", pady=3)
        cb = ttk.Combobox(s, textvariable=self.v_palette, state="readonly",
                          values=[core.AUTO_PALETTE, *core.PRESETS.keys()])
        cb.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._schedule_preview())

        ttk.Label(s, text="디더링").grid(row=3, column=0, sticky="w", pady=3)
        cb2 = ttk.Combobox(s, textvariable=self.v_dither, state="readonly", values=core.DITHER_MODES)
        cb2.grid(row=3, column=1, columnspan=2, sticky="ew", padx=6)
        cb2.bind("<<ComboboxSelected>>", lambda e: self._schedule_preview())

        self._slider(s, "디더링 강도", self.v_dstr, 0, 40, 4)
        self._slider(s, "채도", self.v_sat, 0.5, 2.0, 5, fmt="{:.2f}")
        self._slider(s, "대비", self.v_con, 0.7, 1.5, 6, fmt="{:.2f}")

        ttk.Label(s, text="출력 배율 (0=자동)").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Spinbox(s, from_=0, to=20, textvariable=self.v_scale, width=6,
                    command=self._schedule_preview).grid(row=7, column=1, sticky="w", padx=6)
        ttk.Checkbutton(s, text="원본 오디오 유지", variable=self.v_audio).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=3)

        # 실행
        self.btn = ttk.Button(left, text="변환 시작", command=self.start)
        self.btn.pack(fill="x", ipady=6)
        self.bar = ttk.Progressbar(left, maximum=1.0)
        self.bar.pack(fill="x", pady=8)
        self.status = ttk.Label(left, text="영상을 선택하세요.", wraplength=330)
        self.status.pack(anchor="w")

        # 미리보기
        ttk.Label(right, text="미리보기  (왼쪽: 원본 / 오른쪽: 도트)").pack(anchor="w")
        self.canvas = tk.Canvas(right, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=6)
        self.canvas.bind("<Configure>", lambda e: self._schedule_preview())
        sl = ttk.Scale(right, from_=0, to=1, variable=self.v_pos, command=lambda _=None: self._schedule_preview())
        sl.pack(fill="x")
        ttk.Label(right, text="↑ 영상 위치 이동").pack(anchor="e")

    # ------------------------------------------------------------ 동작
    def pick_src(self):
        p = filedialog.askopenfilename(
            title="영상 선택",
            filetypes=[("영상", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"), ("모든 파일", "*.*")])
        if p:
            self.v_src.set(p)
            base, _ = os.path.splitext(p)
            self.v_dst.set(base + "_dot.mp4")
            self.status.config(text="미리보기에서 설정을 조절한 뒤 '변환 시작'을 누르세요.")
            self._schedule_preview()

    def pick_dst(self):
        p = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if p:
            self.v_dst.set(p)

    def cfg(self) -> core.Config:
        return core.Config(
            src=self.v_src.get(), dst=self.v_dst.get(),
            grid_w=int(self.v_grid.get()), colors=int(self.v_colors.get()),
            palette=self.v_palette.get(), dither=self.v_dither.get(),
            dither_strength=float(self.v_dstr.get()),
            saturation=float(self.v_sat.get()), contrast=float(self.v_con.get()),
            scale=int(self.v_scale.get() or 0), keep_audio=bool(self.v_audio.get()))

    def _schedule_preview(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(250, self._preview)

    def _preview(self):
        self._after_id = None
        src = self.v_src.get()
        if not src or not os.path.exists(src):
            return
        try:
            orig, dot = self.previewer.render(self.cfg(), float(self.v_pos.get()))
        except Exception as e:  # noqa: BLE001
            self.status.config(text=f"미리보기 오류: {e}")
            return
        cw = max(self.canvas.winfo_width(), 200)
        ch = max(self.canvas.winfo_height(), 200)
        half = cw // 2 - 6
        self._imgs = []
        self.canvas.delete("all")
        for i, (img, resample) in enumerate(((orig, Image.LANCZOS), (dot, Image.NEAREST))):
            im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            r = min(half / im.width, ch / im.height)
            im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), resample)
            tk_im = ImageTk.PhotoImage(im)
            self._imgs.append(tk_im)
            self.canvas.create_image(i * (half + 12) + half // 2, ch // 2, image=tk_im)

    def start(self):
        if self.worker and self.worker.is_alive():
            self.cancel_evt.set()
            self.status.config(text="취소하는 중...")
            return
        c = self.cfg()
        if not c.src or not os.path.exists(c.src):
            messagebox.showwarning("알림", "영상을 먼저 선택하세요.")
            return
        if not c.dst:
            messagebox.showwarning("알림", "저장 위치를 지정하세요.")
            return
        self.cancel_evt.clear()
        self.btn.config(text="취소")
        self.bar["value"] = 0

        def prog(p, m):
            self.after(0, lambda: (self.bar.config(value=p), self.status.config(text=m)))

        def run():
            try:
                core.process_video(c, prog, self.cancel_evt)
                self.after(0, lambda: messagebox.showinfo("완료", f"저장되었습니다:\n{c.dst}"))
            except InterruptedError:
                self.after(0, lambda: self.status.config(text="취소되었습니다."))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self.after(0, lambda: (self.status.config(text="오류 발생"), messagebox.showerror("오류", msg)))
            finally:
                self.after(0, lambda: self.btn.config(text="변환 시작"))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()


if __name__ == "__main__":
    App().mainloop()
