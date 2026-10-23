import os, re, time, math, threading, subprocess
import tkinter as tk
from tkinter import ttk, filedialog
import cv2, numpy as np
from PIL import Image, ImageTk
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
EXT = "*.mp4 *.mkv *.avi *.mov *.webm *.ts *.m2ts *.flv *.wmv *.3gp *.ogv *.mpg *.mpeg *.mxf *.m4v"
MODES = {"Mouvement (flux optique)": "mc", "Fondu enchaîné": "blend", "Désactivée": "off"}
FITS = {"Ajuster (bandes noires)": "contain", "Remplir (recadrer)": "cover",
        "Étirer": "fill", "Ratio original (sans bandes)": "orig"}
VF = {"mc": "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
      "blend": "minterpolate=fps=60:mi_mode=blend", "off": "fps=60"}
PW = 960  # largeur max de l'aperçu
fmt = lambda t: f"{int(t)//60}:{int(t)%60:02d}"


class App:
    def __init__(s):
        r = s.root = tk.Tk()
        r.title("Lecteur 60 i/s"); r.geometry("1150x780"); r.minsize(800, 560)
        s.files, s.done = [], {}
        s.idx, s.cap, s.playing, s.busy, s.cancel, s.proc = -1, None, False, False, False, None
        s.prev = s.cur = s.flow = s.grid = s.photo = s.after_id = None
        s.fps, s.n, s.k, s.tprev, s.tcur, s.now, s.base, s.clock0 = 25, 1, 0, 0., 0., 0., 0., 0.
        s.drag, s.csize = False, (2, 2)
        s.modev = tk.StringVar(value=list(MODES)[0]); s.fitv = tk.StringVar(value=list(FITS)[0])
        s.brv = tk.StringVar(value="16"); s.autov = tk.BooleanVar(value=True)

        left = ttk.Frame(r); left.pack(side="left", fill="y", padx=(8, 4), pady=8)
        ttk.Button(left, text="Ouvrir des fichiers", command=s.open_files).pack(fill="x")
        s.lb = tk.Listbox(left, width=36, activestyle="none", exportselection=False)
        s.lb.pack(fill="y", expand=True, pady=6)
        s.lb.bind("<Double-Button-1>", lambda e: s.lb.curselection() and s.play_index(s.lb.curselection()[0]))
        row = ttk.Frame(left); row.pack(fill="x")
        ttk.Button(row, text="Retirer", command=s.remove).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Vider", command=s.clear).pack(side="left", expand=True, fill="x")

        right = ttk.Frame(r); right.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=8)
        s.box = tk.Frame(right, bg="black"); s.box.pack(fill="both", expand=True)
        s.canvas = tk.Canvas(s.box, bg="black", highlightthickness=0)
        s.item = s.canvas.create_image(0, 0, anchor="nw")
        s.box.bind("<Configure>", lambda e: s.layout())
        s.canvas.bind("<Button-1>", lambda e: s.toggle())
        s.canvas.bind("<Double-Button-1>", lambda e: s.fullscreen())
        r.bind("<f>", lambda e: s.fullscreen()); r.bind("<space>", lambda e: s.toggle())
        r.bind("<Escape>", lambda e: r.attributes("-fullscreen", False))

        b1 = ttk.Frame(right); b1.pack(fill="x", pady=(8, 0))
        ttk.Button(b1, text="⏮", width=3, command=lambda: s.go(-1)).pack(side="left")
        s.btn_play = ttk.Button(b1, text="Lecture", width=9, command=s.toggle); s.btn_play.pack(side="left", padx=4)
        ttk.Button(b1, text="⏭", width=3, command=lambda: s.go(1)).pack(side="left")
        s.seek_w = ttk.Scale(b1, from_=0, to=1000); s.seek_w.pack(side="left", fill="x", expand=True, padx=8)
        s.seek_w.bind("<ButtonPress-1>", lambda e: setattr(s, "drag", True))
        s.seek_w.bind("<ButtonRelease-1>", lambda e: s.seek(s.seek_w.get() / 1000))
        s.tl = ttk.Label(b1, text="0:00 / 0:00", width=12); s.tl.pack(side="left")

        b2 = ttk.Frame(right); b2.pack(fill="x", pady=6)
        ttk.Label(b2, text="Interpolation").pack(side="left")
        ttk.Combobox(b2, textvariable=s.modev, values=list(MODES), state="readonly", width=22).pack(side="left", padx=(4, 12))
        ttk.Label(b2, text="Affichage").pack(side="left")
        c = ttk.Combobox(b2, textvariable=s.fitv, values=list(FITS), state="readonly", width=26)
        c.pack(side="left", padx=(4, 12)); c.bind("<<ComboboxSelected>>", lambda e: s.layout())
        ttk.Checkbutton(b2, text="Enchaîner", variable=s.autov).pack(side="left")

        b3 = ttk.Frame(right); b3.pack(fill="x")
        ttk.Label(b3, text="Débit (Mb/s)").pack(side="left")
        ttk.Combobox(b3, textvariable=s.brv, values=["8", "16", "30"], state="readonly", width=4).pack(side="left", padx=(4, 12))
        s.btn_exp = ttk.Button(b3, text="Exporter toute la liste en 60 i/s", command=s.export_all); s.btn_exp.pack(side="left")
        s.btn_cancel = ttk.Button(b3, text="Annuler", command=s.cancel_export, state="disabled"); s.btn_cancel.pack(side="left", padx=6)
        s.prog = ttk.Progressbar(right, maximum=100); s.prog.pack(fill="x", pady=6)
        s.st = ttk.Label(right, text="Ouvre un ou plusieurs fichiers vidéo. Double-clic sur la vidéo : plein écran."); s.st.pack(fill="x")
        r.protocol("WM_DELETE_WINDOW", s.close)

    ui = lambda s, fn: s.root.after(0, fn)
    status = lambda s, t: s.st.config(text=t)

    # ---------- liste ----------
    def refresh(s):
        s.lb.delete(0, "end")
        for i, f in enumerate(s.files):
            s.lb.insert("end", ("▶ " if i == s.idx else "   ") + s.done.get(f, "") + " " + os.path.basename(f))

    def open_files(s):
        fl = filedialog.askopenfilenames(title="Ouvrir des vidéos", filetypes=[("Vidéos", EXT), ("Tous", "*.*")])
        add = [f for f in fl if f not in s.files]
        s.files += add; s.refresh()
        if add and s.idx < 0 and not s.busy: s.open_at(len(s.files) - len(add))

    def remove(s):
        sel = s.lb.curselection()
        if not sel or s.busy: return
        i = sel[0]
        if i == s.idx: s.release()
        elif i < s.idx: s.idx -= 1
        s.done.pop(s.files.pop(i), None); s.refresh()

    def clear(s):
        if s.busy: return
        s.release(); s.files.clear(); s.done.clear(); s.refresh()

    def release(s):
        s.pause()
        if s.cap: s.cap.release()
        s.cap, s.idx, s.prev, s.cur = None, -1, None, None
        s.canvas.itemconfig(s.item, image="")

    # ---------- lecture ----------
    def small(s, f):
        h, w = f.shape[:2]
        return cv2.resize(f, (PW, max(2, int(h * PW / w)))) if w > PW else f

    def open_at(s, i):
        if not 0 <= i < len(s.files): return False
        s.pause()
        if s.cap: s.cap.release()
        cap = cv2.VideoCapture(s.files[i]); ok, f = cap.read()
        if not ok:
            cap.release(); s.cap = None; s.done[s.files[i]] = "✗"; s.refresh()
            s.status("Illisible : " + os.path.basename(s.files[i])); return False
        s.cap, s.idx = cap, i
        s.fps = cap.get(cv2.CAP_PROP_FPS) or 25
        if not 1 <= s.fps <= 240: s.fps = 25
        s.n = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        s.prev = s.cur = s.small(f); s.flow = None
        s.k, s.tprev, s.tcur, s.now, s.base = 0, 0., 0., 0., 0.
        h, w = f.shape[:2]
        s.refresh(); s.layout()
        s.status(f"{os.path.basename(s.files[i])} — {w}×{h}, source {s.fps:.2f} i/s")
        return True

    def play_index(s, i):
        if s.open_at(i): s.play()

    def go(s, d):
        was = s.playing
        if s.open_at(s.idx + d) and was: s.play()

    def toggle(s):
        s.pause() if s.playing else s.play()

    def play(s):
        if not s.cap or s.playing: return
        if s.now >= (s.n - 1) / s.fps: s.open_at(s.idx)
        s.playing, s.base, s.clock0 = True, s.now, time.perf_counter()
        s.btn_play.config(text="Pause"); s.tick()

    def pause(s):
        s.playing = False
        if s.after_id: s.root.after_cancel(s.after_id); s.after_id = None
        s.btn_play.config(text="Lecture")

    def tick(s):
        if not s.playing: return
        t0 = time.perf_counter()
        now = s.base + (t0 - s.clock0)
        while s.tcur <= now:
            ok, f = s.cap.read()
            if not ok: return s.ended()
            s.k += 1
            s.prev, s.cur = s.cur, s.small(f)
            s.tprev, s.tcur, s.flow = s.tcur, s.k / s.fps, None
        s.now = now
        a = (now - s.tprev) / max(1e-6, s.tcur - s.tprev)
        mode = MODES[s.modev.get()]
        img = s.prev if mode == "off" else cv2.addWeighted(s.prev, 1 - a, s.cur, a, 0) if mode == "blend" else s.interp(a)
        s.show(img)
        if not s.drag:
            s.seek_w.set(now * s.fps / s.n * 1000)
        s.tl.config(text=f"{fmt(now)} / {fmt(s.n / s.fps)}")
        s.after_id = s.root.after(max(1, int(16 - (time.perf_counter() - t0) * 1000)), s.tick)

    def ended(s):
        s.pause()
        if s.autov.get():
            i = s.idx + 1
            while i < len(s.files) and not s.open_at(i): i += 1
            if i < len(s.files): s.play()

    def seek(s, f):
        s.drag = False
        if not s.cap: return
        k = int(f * (s.n - 1)); s.cap.set(cv2.CAP_PROP_POS_FRAMES, k)
        ok, fr = s.cap.read()
        if not ok: return
        s.prev = s.cur = s.small(fr); s.flow = None
        s.k, s.tprev, s.tcur, s.now, s.base = k, k / s.fps, k / s.fps, k / s.fps, k / s.fps
        s.clock0 = time.perf_counter(); s.show(s.cur)

    # ---------- interpolation par flux optique ----------
    def calc_flow(s):
        h, w = s.cur.shape[:2]; sw = 192; sh = max(8, int(h * sw / w))
        g = lambda im: cv2.cvtColor(cv2.resize(im, (sw, sh)), cv2.COLOR_BGR2GRAY)
        a, b = g(s.prev), g(s.cur)
        if np.mean(cv2.absdiff(a, b)) > 42: return False  # changement de plan
        f = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        f = cv2.resize(f, (w, h)); f[..., 0] *= w / sw; f[..., 1] *= h / sh
        return f

    def interp(s, a):
        if s.flow is None: s.flow = s.calc_flow()
        if s.flow is False: return s.prev if a < .5 else s.cur
        h, w = s.cur.shape[:2]
        if s.grid is None or s.grid[0].shape != (h, w):
            s.grid = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        gx, gy = s.grid; fx, fy = s.flow[..., 0], s.flow[..., 1]
        rm = lambda im, mx, my: cv2.remap(im, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        p = rm(s.prev, gx - a * fx, gy - a * fy)
        c = rm(s.cur, gx + (1 - a) * fx, gy + (1 - a) * fy)
        return cv2.addWeighted(p, 1 - a, c, a, 0)

    # ---------- affichage ----------
    def layout(s):
        bw, bh = max(2, s.box.winfo_width()), max(2, s.box.winfo_height())
        cw, ch = bw, bh
        if FITS[s.fitv.get()] == "orig" and s.cur is not None:
            h, w = s.cur.shape[:2]; k = min(bw / w, bh / h); cw, ch = max(2, int(w * k)), max(2, int(h * k))
        s.csize = (cw, ch)
        s.canvas.place(relx=.5, rely=.5, anchor="center", width=cw, height=ch)
        if s.cur is not None and not s.playing: s.show(s.cur)

    def fit(s, img, cw, ch):
        m = FITS[s.fitv.get()]; h, w = img.shape[:2]
        if m == "fill": return cv2.resize(img, (cw, ch))
        k = max(cw / w, ch / h) if m == "cover" else min(cw / w, ch / h)
        nw, nh = max(1, math.ceil(w * k)), max(1, math.ceil(h * k))
        r = cv2.resize(img, (nw, nh))
        if m == "cover":
            x, y = (nw - cw) // 2, (nh - ch) // 2
            return cv2.resize(r[y:y + ch, x:x + cw], (cw, ch))
        out = np.zeros((ch, cw, 3), np.uint8); nw, nh = min(nw, cw), min(nh, ch)
        x, y = (cw - nw) // 2, (ch - nh) // 2
        out[y:y + nh, x:x + nw] = r[:nh, :nw]
        return out

    def show(s, img):
        cw, ch = s.csize
        s.photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(s.fit(img, cw, ch), cv2.COLOR_BGR2RGB)))
        s.canvas.itemconfig(s.item, image=s.photo)

    def fullscreen(s):
        s.root.attributes("-fullscreen", not s.root.attributes("-fullscreen"))

    # ---------- export (ffmpeg) ----------
    def export_all(s):
        if s.busy or not s.files: return
        s.pause(); s.busy, s.cancel = True, False
        s.btn_exp.config(state="disabled"); s.btn_cancel.config(state="normal")
        threading.Thread(target=s.worker, args=(list(s.files), MODES[s.modev.get()], s.brv.get()), daemon=True).start()

    def cancel_export(s):
        s.cancel = True
        if s.proc: s.proc.terminate()

    def setdone(s, f, m): s.done[f] = m; s.refresh()

    def worker(s, files, mode, br):
        ok = 0
        for i, src in enumerate(files):
            if s.cancel: break
            base, n = os.path.splitext(src)[0] + "_60fps", 1; out = base + ".mp4"
            while os.path.exists(out): n += 1; out = f"{base}_{n}.mp4"
            cap = cv2.VideoCapture(src); fps = cap.get(cv2.CAP_PROP_FPS) or 25
            dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps; cap.release()
            s.ui(lambda i=i, src=src: (s.status(f"Export {i + 1}/{len(files)} : {os.path.basename(src)}"),
                                       s.prog.config(value=0), s.setdone(src, "…")))
            cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                   "-vf", VF[mode] + ",scale=trunc(iw/2)*2:trunc(ih/2)*2", "-r", "60",
                   "-c:v", "libx264", "-preset", "medium", "-b:v", br + "M", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-progress", "pipe:1", "-nostats", out]
            err = ""
            try:
                s.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                          encoding="utf-8", errors="replace",
                                          creationflags=0x08000000 if os.name == "nt" else 0)
                for line in s.proc.stdout:
                    line = line.strip()
                    if line.startswith(("out_time_us=", "out_time_ms=")) and dur > 0:
                        try: v = int(line.split("=")[1]) / 1e6
                        except ValueError: continue
                        s.ui(lambda p=min(100, v / dur * 100): s.prog.config(value=p))
                    elif line and not re.match(r"^\w+=", line): err = line
                s.proc.wait(); good = s.proc.returncode == 0 and not s.cancel
            except Exception as e:
                good, err = False, str(e)
            if good: ok += 1
            else:
                try: os.remove(out)
                except OSError: pass
            s.ui(lambda src=src, g=good: s.setdone(src, "✓" if g else "✗"))
            if err and not good and not s.cancel: s.ui(lambda err=err: s.status("Erreur ffmpeg : " + err))
        msg = f"{'Interrompu' if s.cancel else 'Terminé'} : {ok}/{len(files)} exporté(s) à côté des originaux (_60fps.mp4)."
        def fin():
            s.busy = False; s.proc = None; s.btn_exp.config(state="normal"); s.btn_cancel.config(state="disabled")
            s.prog.config(value=100 if ok and not s.cancel else 0); s.status(msg)
        s.ui(fin)

    def close(s):
        s.cancel = True
        if s.proc: s.proc.terminate()
        s.root.destroy()


if __name__ == "__main__":
    App().root.mainloop()
