"""
EXE Icon Extractor & Injector
- Extrai ícones de EXE/DLL (RT_GROUP_ICON + RT_ICON + PNG raw)
- Injeta/substitui ícones num EXE a partir de um ficheiro .ico
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading, os, io, glob, struct
from pathlib import Path
from PIL import Image, ImageDraw
import pefile

try:
    import icoextract as _icoextract
    HAS_ICOEXTRACT = True
except ImportError:
    HAS_ICOEXTRACT = False

RT_ICON       = 3
RT_GROUP_ICON = 14

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG        = "#0e0e14"
PANEL     = "#16161f"
CARD      = "#1c1c28"
BORDER    = "#2a2a3d"
ACCENT    = "#5b6af5"
ACCENT2   = "#8b5cf6"
TEXT      = "#e8e8f0"
MUTED     = "#6b6b8a"
SUCCESS   = "#22c55e"
ERROR_COL = "#ef4444"
WARNING   = "#f59e0b"


# ═══════════════════════════════════════════════════════════════════════════════
#  Utilitários ICO / DIB
# ═══════════════════════════════════════════════════════════════════════════════

def _rgba_to_dib(img: Image.Image) -> bytes:
    img = img.convert("RGBA")
    w, h = img.size
    bih = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = img.tobytes("raw", "BGRA")
    row = w * 4
    pixels_bu = b"".join(reversed([pixels[i * row:(i + 1) * row] for i in range(h)]))
    mask = bytes(((w + 31) // 32) * 4 * h)
    return bih + pixels_bu + mask


def _dib_size(data: bytes):
    """Devolve (w, h) de um bloco DIB, ou None se inválido."""
    if len(data) < 40:
        return None
    bi = struct.unpack_from("<I", data, 0)[0]
    if bi not in (40, 108, 124):
        return None
    w = struct.unpack_from("<i", data, 4)[0]
    h = abs(struct.unpack_from("<i", data, 8)[0]) // 2
    if w <= 0 or h <= 0 or w > 1024 or h > 1024:
        return None
    return (w, h)


def _dib_to_rgba(data: bytes, w: int, h: int):
    try:
        wrap = (struct.pack("<HHH", 0, 1, 1) +
                struct.pack("<BBBBHHII", w if w < 256 else 0, h if h < 256 else 0,
                            0, 0, 1, 32, len(data), 22) + data)
        img = Image.open(io.BytesIO(wrap))
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def _dib_to_rgba_generic(data: bytes):
    sz = _dib_size(data)
    if sz is None:
        return None
    return _dib_to_rgba(data, *sz)


def build_ico_bytes(images: list) -> bytes:
    """Monta ICO multi-frame. Frames >=256px guardados como PNG."""
    frames = []
    for img in sorted(images, key=lambda i: i.size[0]):
        img = img.convert("RGBA")
        w, h = img.size
        if w >= 256:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            frames.append((w, h, buf.getvalue(), "png"))
        else:
            frames.append((w, h, _rgba_to_dib(img), "dib"))

    if not frames:
        return b""
    n = len(frames)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + n * 16
    dirs = b""
    for w, h, data, fmt in frames:
        dirs += struct.pack("<BBBBHHII",
                            w if w < 256 else 0, h if h < 256 else 0, 0, 0,
                            0 if fmt == "png" else 1,
                            0 if fmt == "png" else 32,
                            len(data), offset)
        offset += len(data)
    return header + dirs + b"".join(d for _, _, d, _ in frames)


def ico_to_dibs(ico_bytes: bytes) -> dict:
    """Converte ICO → {(w,h): dib_bytes}. Frames PNG convertidos para DIB."""
    result = {}
    if len(ico_bytes) < 6:
        return result
    _, _, count = struct.unpack_from("<HHH", ico_bytes, 0)
    for i in range(count):
        off = 6 + i * 16
        bw, bh, _, _, _, _, size, data_off = struct.unpack_from("<BBBBHHII", ico_bytes, off)
        w = bw if bw > 0 else 256
        h = bh if bh > 0 else 256
        frame = ico_bytes[data_off:data_off + size]
        if frame[:8] == b"\x89PNG\r\n\x1a\n":
            try:
                img = Image.open(io.BytesIO(frame)).convert("RGBA")
                w, h = img.size
                frame = _rgba_to_dib(img)
            except Exception:
                continue
        result[(w, h)] = frame
    return result


def ico_preview_image(ico_bytes: bytes) -> "Image.Image | None":
    """Devolve o maior frame de um ICO como PIL Image RGBA."""
    dibs = ico_to_dibs(ico_bytes)
    if not dibs:
        return None
    largest_key = max(dibs.keys(), key=lambda s: s[0])
    w, h = largest_key
    return _dib_to_rgba(dibs[largest_key], w, h)


# ═══════════════════════════════════════════════════════════════════════════════
#  Extração
# ═══════════════════════════════════════════════════════════════════════════════

def _read_all_ico_frames(ico_bytes: bytes) -> list:
    imgs = []
    _, _, count = struct.unpack_from("<HHH", ico_bytes, 0)
    for i in range(count):
        off = 6 + i * 16
        bw, bh, _, _, _, _, size, data_off = struct.unpack_from("<BBBBHHII", ico_bytes, off)
        w = bw if bw > 0 else 256
        frame = ico_bytes[data_off:data_off + size]
        if frame[:8] == b"\x89PNG\r\n\x1a\n":
            try:
                imgs.append(Image.open(io.BytesIO(frame)).convert("RGBA"))
            except Exception:
                pass
        else:
            wrap = (struct.pack("<HHH", 0, 1, 1) +
                    struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32, size, 22) + frame)
            try:
                img = Image.open(io.BytesIO(wrap))
                img.load()
                imgs.append(img.convert("RGBA"))
            except Exception:
                pass
    return imgs


def _collect_rt_icons(pe, res_by_type):
    out, seen = [], set()
    icon_res = res_by_type.get(RT_ICON)
    if not icon_res:
        return out
    for entry in icon_res.directory.entries:
        try:
            lang = entry.directory.entries[0]
            data = pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                img = Image.open(io.BytesIO(data)).convert("RGBA")
            else:
                img = _dib_to_rgba_generic(data)
            if img and img.size[0] >= 8 and img.size not in seen:
                seen.add(img.size)
                out.append((entry.id, img))
        except Exception:
            pass
    return out


def extract_icon_groups(filepath: str) -> tuple[list[dict], str | None]:
    results = []
    try:
        pe = pefile.PE(filepath, fast_load=True)
        pe.parse_data_directories(pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"])
    except pefile.PEFormatError as e:
        return [], f"Não é um PE válido: {e}"
    except Exception as e:
        return [], f"Erro: {e}"

    if not hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        pe.close()
        return [], "Sem secção de recursos"

    res_by_type = {e.id: e for e in pe.DIRECTORY_ENTRY_RESOURCE.entries}

    if HAS_ICOEXTRACT and RT_GROUP_ICON in res_by_type and RT_ICON in res_by_type:
        try:
            ext = _icoextract.IconExtractor(filename=filepath)
            for gidx, (res_id, _) in enumerate(ext.list_group_icons()):
                try:
                    buf = ext.get_icon(num=gidx)
                    raw = buf.read()
                    frames = _read_all_ico_frames(raw)
                    if frames:
                        results.append(dict(source="RT_GROUP_ICON", res_id=res_id,
                                            group_idx=gidx, frames=frames,
                                            ico_bytes=build_ico_bytes(frames)))
                except Exception:
                    pass
        except Exception:
            pass

    if not results and RT_ICON in res_by_type:
        pairs = _collect_rt_icons(pe, res_by_type)
        if pairs:
            frames = [img for _, img in pairs]
            results.append(dict(source="RT_ICON", res_id=0, group_idx=0,
                                frames=frames, ico_bytes=build_ico_bytes(frames)))

    if not results and RT_ICON in res_by_type and RT_GROUP_ICON in res_by_type:
        pairs = _collect_rt_icons(pe, res_by_type)
        if pairs:
            frames = [img for _, img in pairs]
            results.append(dict(source="RT_ICON (fallback)", res_id=0, group_idx=0,
                                frames=frames, ico_bytes=build_ico_bytes(frames)))

    pe.close()
    if not results:
        return [], "Nenhum ícone encontrado"
    return results, None


# ═══════════════════════════════════════════════════════════════════════════════
#  Injeção — via LIEF (reconstrói a secção .rsrc sem limite de tamanho)
# ═══════════════════════════════════════════════════════════════════════════════

import lief as _lief


def _largest_rgba(new_dibs: dict) -> "Image.Image | None":
    """Devolve o maior frame do dicionário como PIL RGBA (para redimensionar)."""
    if not new_dibs:
        return None
    lk = max(new_dibs.keys(), key=lambda s: s[0])
    lw, lh = lk
    dib = new_dibs[lk]
    # Wrap num ICO mínimo para o Pillow ler
    wrap = (struct.pack("<HHH", 0, 1, 1) +
            struct.pack("<BBBBHHII", lw if lw < 256 else 0, lh if lh < 256 else 0,
                        0, 0, 1, 32, len(dib), 22) + dib)
    try:
        img = Image.open(io.BytesIO(wrap))
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def inject_icon(exe_path: str, ico_bytes: bytes, output_path: str) -> tuple[bool, str, dict]:
    """
    Substitui os ícones de um EXE usando LIEF.
    A secção .rsrc é completamente reconstruída — sem limites de tamanho.
    Frames do ICO sem correspondência de tamanho são redimensionados automaticamente.
    """
    # ── Parse com LIEF ────────────────────────────────────────────────────────
    pe = _lief.parse(exe_path)
    if pe is None:
        return False, "Não é um PE válido", {}

    if not pe.has_resources:
        return False, "EXE sem secção de recursos", {}

    # ── Converte ICO em dicionário de DIBs ────────────────────────────────────
    new_dibs = ico_to_dibs(ico_bytes)
    if not new_dibs:
        return False, "ICO inválido ou sem frames", {}

    src_rgba = _largest_rgba(new_dibs)   # imagem fonte para redimensionar

    # ── Localiza o nó RT_ICON (id=3) na árvore de recursos ───────────────────
    root = pe.resources
    rt_icon_node = None
    for child in root.childs:
        if child.id == RT_ICON:
            rt_icon_node = child
            break

    if rt_icon_node is None:
        return False, "EXE sem recursos RT_ICON", {}

    replaced = resized = skipped = 0

    # ── Itera id_node → lang_node (nó folha com .content) ────────────────────
    for id_node in rt_icon_node.childs:
        for lang_node in id_node.childs:
            if not lang_node.is_data:
                continue
            orig_data = bytes(lang_node.content)

            # Detecta o formato e dimensões do frame original
            is_png_frame = orig_data[:8] == b"\x89PNG\r\n\x1a\n"

            if is_png_frame:
                # Frame PNG raw (256px+) — lê dimensões do header PNG
                try:
                    orig_img = Image.open(io.BytesIO(orig_data))
                    ow, oh = orig_img.size
                except Exception:
                    skipped += 1
                    continue
            else:
                sz = _dib_size(orig_data)
                if sz is None:
                    skipped += 1
                    continue
                ow, oh = sz

            # Procura frame correspondente no ICO novo
            new_dib = new_dibs.get((ow, oh))

            if new_dib is None:
                # Sem frame exacto — redimensiona a partir do maior
                if src_rgba is None:
                    skipped += 1
                    continue
                try:
                    resized_img = src_rgba.resize((ow, oh), Image.LANCZOS)
                    new_dib = _rgba_to_dib(resized_img)
                    resized += 1
                except Exception:
                    skipped += 1
                    continue

            # Para frames que eram PNG no original, guarda como PNG no output
            if is_png_frame:
                try:
                    # Converte o DIB de volta para PNG para manter o formato original
                    wrap = (struct.pack("<HHH", 0, 1, 1) +
                            struct.pack("<BBBBHHII",
                                        ow if ow < 256 else 0, oh if oh < 256 else 0,
                                        0, 0, 1, 32, len(new_dib), 22) + new_dib)
                    tmp_img = Image.open(io.BytesIO(wrap)).convert("RGBA")
                    buf = io.BytesIO()
                    tmp_img.save(buf, format="PNG")
                    new_content = buf.getvalue()
                except Exception:
                    new_content = new_dib  # fallback para DIB se PNG falhar
            else:
                new_content = new_dib

            # Substitui o conteúdo — LIEF reconstrói a secção com o tamanho certo
            lang_node.content = bytes(new_content)
            replaced += 1

    if replaced == 0:
        return False, f"Nenhum frame substituído (ignorados={skipped})", {}

    # ── Reconstrói .rsrc e escreve o PE ──────────────────────────────────────
    try:
        config = _lief.PE.Builder.config_t()
        config.resources = True
        builder = _lief.PE.Builder(pe, config)
        builder.build()
        builder.write(output_path)
    except Exception as e:
        return False, f"Erro ao escrever EXE: {e}", {}

    stats = dict(replaced=replaced, resized=resized, skipped=skipped)
    parts = [f"{replaced} frame(s) substituído(s)"]
    if resized:
        parts.append(f"{resized} redimensionado(s)")
    if skipped:
        parts.append(f"{skipped} ignorado(s)")
    return True, "  ·  ".join(parts), stats


# ═══════════════════════════════════════════════════════════════════════════════
#  GUI helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _checkerboard(size: int, cell: int = 8) -> Image.Image:
    bg = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(bg)
    c1, c2 = (45, 45, 60, 255), (30, 30, 42, 255)
    for y in range(0, size, cell):
        for x in range(0, size, cell):
            draw.rectangle([x, y, x + cell - 1, y + cell - 1],
                           fill=c1 if (x // cell + y // cell) % 2 == 0 else c2)
    return bg


def _make_ctk_img(pil_img: Image.Image, size: int = 72) -> ctk.CTkImage:
    thumb = pil_img.copy()
    thumb.thumbnail((size, size), Image.LANCZOS)
    bg = _checkerboard(size)
    ox = (size - thumb.size[0]) // 2
    oy = (size - thumb.size[1]) // 2
    if thumb.mode == "RGBA":
        bg.paste(thumb, (ox, oy), mask=thumb.split()[3])
    else:
        bg.paste(thumb, (ox, oy))
    return ctk.CTkImage(light_image=bg, dark_image=bg, size=(size, size))


# ═══════════════════════════════════════════════════════════════════════════════
#  Cartão de grupo de ícones (separador Extrair)
# ═══════════════════════════════════════════════════════════════════════════════

class IconGroupCard(ctk.CTkFrame):
    def __init__(self, master, group: dict, **kw):
        super().__init__(master, fg_color=CARD, corner_radius=12,
                         border_width=1, border_color=BORDER, **kw)
        self._g = group
        self._build()

    def _build(self):
        frames = self._g["frames"]
        best = max(frames, key=lambda i: i.size[0])
        self._ctk_img = _make_ctk_img(best)
        self._lbl = ctk.CTkLabel(self, image=self._ctk_img, text="")
        self._lbl.pack(pady=(12, 4))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=6, pady=(0, 4))
        for img in sorted(frames, key=lambda i: i.size[0]):
            sz = img.size[0]
            ctk.CTkButton(row, text=str(sz), width=32, height=22, corner_radius=6,
                          fg_color=BORDER, hover_color=ACCENT, font=("Consolas", 9),
                          command=lambda i=img: self._set_preview(i)
                          ).pack(side="left", padx=2)

        sizes_str = "  ".join(f"{i.size[0]}px" for i in sorted(frames, key=lambda i: i.size[0]))
        ctk.CTkLabel(self, text=sizes_str, font=("Consolas", 9),
                     text_color=MUTED, wraplength=180).pack(padx=6)
        ctk.CTkLabel(self, text=f"{self._g['source']}  ·  ID #{self._g['res_id']}",
                     font=("Consolas", 8), text_color=MUTED).pack(pady=(2, 8))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=10, pady=(0, 12), fill="x")
        ctk.CTkButton(btn_row, text="💾 ICO", height=30, corner_radius=8,
                      fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Segoe UI", 11, "bold"),
                      command=self._save_ico).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(btn_row, text="🖼 PNG", height=30, corner_radius=8,
                      fg_color=CARD, hover_color=BORDER,
                      border_width=1, border_color=BORDER,
                      font=("Segoe UI", 11),
                      command=self._save_png).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _set_preview(self, img):
        self._ctk_img = _make_ctk_img(img)
        self._lbl.configure(image=self._ctk_img)

    def _save_ico(self):
        frames = self._g["frames"]
        sizes = sorted(set(f.size[0] for f in frames))
        default = f"icon_grp{self._g['group_idx']}_id{self._g['res_id']}_{'x'.join(str(s) for s in sizes)}.ico"
        dest = filedialog.asksaveasfilename(defaultextension=".ico",
                                            filetypes=[("ICO", "*.ico")], initialfile=default)
        if not dest:
            return
        with open(dest, "wb") as f:
            f.write(self._g["ico_bytes"])
        messagebox.showinfo("Guardado", f"ICO guardado ({', '.join(str(s)+'×'+str(s) for s in sizes)}):\n{dest}")

    def _save_png(self):
        frames = self._g["frames"]
        if len(frames) == 1:
            img = frames[0]
            dest = filedialog.asksaveasfilename(defaultextension=".png",
                                                filetypes=[("PNG", "*.png")],
                                                initialfile=f"icon_{img.size[0]}x{img.size[0]}.png")
            if dest:
                img.save(dest, format="PNG")
                messagebox.showinfo("Guardado", f"PNG guardado:\n{dest}")
        else:
            folder = filedialog.askdirectory(title="Pasta para os PNGs")
            if not folder:
                return
            for img in frames:
                w = img.size[0]
                img.save(os.path.join(folder,
                    f"icon_grp{self._g['group_idx']}_id{self._g['res_id']}_{w}x{w}.png"), "PNG")
            messagebox.showinfo("Guardado", f"{len(frames)} PNG(s) guardados em:\n{folder}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Separador — Injetar ICO
# ═══════════════════════════════════════════════════════════════════════════════

class InjectTab(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._exe_path  = None
        self._ico_bytes = None
        self._build()

    def _build(self):
        # ── Coluna esquerda: EXE ──────────────────────────────────────────────
        left = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=0)

        ctk.CTkLabel(left, text="① EXE de origem",
                     font=("Segoe UI", 13, "bold"), text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(16, 8))

        self._exe_lbl = ctk.CTkLabel(left, text="Nenhum ficheiro seleccionado",
                                      font=("Consolas", 11), text_color=MUTED,
                                      wraplength=320, justify="left")
        self._exe_lbl.pack(anchor="w", padx=20)

        ctk.CTkButton(left, text="📂 Selecionar EXE", width=160, height=34,
                      corner_radius=8, fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Segoe UI", 12),
                      command=self._pick_exe).pack(anchor="w", padx=20, pady=(10, 0))

        # Preview ícone actual
        ctk.CTkLabel(left, text="Ícone actual:", font=("Segoe UI", 11),
                     text_color=MUTED).pack(anchor="w", padx=20, pady=(16, 4))
        self._orig_img_lbl = ctk.CTkLabel(left, text="—", font=("Segoe UI", 24))
        self._orig_img_lbl.pack(anchor="w", padx=24)

        # Info ícones existentes
        self._orig_info = ctk.CTkLabel(left, text="", font=("Consolas", 10),
                                        text_color=MUTED, justify="left")
        self._orig_info.pack(anchor="w", padx=20, pady=(8, 0))

        # ── Coluna central: ICO ───────────────────────────────────────────────
        mid = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        mid.pack(side="left", fill="both", expand=True, padx=8, pady=0)

        ctk.CTkLabel(mid, text="② ICO novo",
                     font=("Segoe UI", 13, "bold"), text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(16, 8))

        self._ico_lbl = ctk.CTkLabel(mid, text="Nenhum ICO seleccionado",
                                      font=("Consolas", 11), text_color=MUTED,
                                      wraplength=260, justify="left")
        self._ico_lbl.pack(anchor="w", padx=20)

        ctk.CTkButton(mid, text="🎨 Selecionar ICO", width=160, height=34,
                      corner_radius=8, fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Segoe UI", 12),
                      command=self._pick_ico).pack(anchor="w", padx=20, pady=(10, 0))

        ctk.CTkLabel(mid, text="Preview:", font=("Segoe UI", 11),
                     text_color=MUTED).pack(anchor="w", padx=20, pady=(16, 4))
        self._new_img_lbl = ctk.CTkLabel(mid, text="—", font=("Segoe UI", 24))
        self._new_img_lbl.pack(anchor="w", padx=24)

        self._ico_info = ctk.CTkLabel(mid, text="", font=("Consolas", 10),
                                       text_color=MUTED, justify="left")
        self._ico_info.pack(anchor="w", padx=20, pady=(8, 0))

        # ── Coluna direita: Injetar ───────────────────────────────────────────
        right = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=0)

        ctk.CTkLabel(right, text="③ Injetar & Guardar",
                     font=("Segoe UI", 13, "bold"), text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(16, 8))

        # Opções
        self._suffix_var = ctk.StringVar(value="_patched")
        ctk.CTkLabel(right, text="Sufixo do ficheiro de saída:",
                     font=("Segoe UI", 11), text_color=MUTED
                     ).pack(anchor="w", padx=20)
        ctk.CTkEntry(right, textvariable=self._suffix_var, width=160,
                     font=("Consolas", 12)).pack(anchor="w", padx=20, pady=(4, 12))

        self._btn_inject = ctk.CTkButton(
            right, text="🔧 Injetar ICO no EXE", width=200, height=42,
            corner_radius=10, fg_color=ACCENT, hover_color=ACCENT2,
            font=("Segoe UI", 13, "bold"),
            command=self._do_inject, state="disabled")
        self._btn_inject.pack(anchor="w", padx=20)

        # Resultado
        self._result_frame = ctk.CTkFrame(right, fg_color=CARD, corner_radius=10)
        self._result_frame.pack(fill="x", padx=16, pady=(16, 0))
        self._result_lbl = ctk.CTkLabel(self._result_frame,
                                         text="Seleccione EXE e ICO para começar.",
                                         font=("Segoe UI", 11), text_color=MUTED,
                                         wraplength=280, justify="left")
        self._result_lbl.pack(padx=14, pady=12)

        # Detalhes técnicos
        self._detail_lbl = ctk.CTkLabel(right, text="",
                                         font=("Consolas", 9), text_color=MUTED,
                                         wraplength=280, justify="left")
        self._detail_lbl.pack(anchor="w", padx=20, pady=(8, 0))

        # Aviso
        ctk.CTkLabel(right,
                     text="ℹ️  Grava uma cópia do EXE com o novo ícone.\nO ficheiro original não é modificado.",
                     font=("Segoe UI", 10), text_color=MUTED, justify="left"
                     ).pack(anchor="w", padx=20, pady=(24, 0))

    # ── Selecção EXE ──────────────────────────────────────────────────────────
    def _pick_exe(self):
        path = filedialog.askopenfilename(
            title="Selecionar EXE de origem",
            filetypes=[("Executáveis", "*.exe *.dll"), ("Todos", "*.*")])
        if not path:
            return
        self._exe_path = path
        self._exe_lbl.configure(text=Path(path).name, text_color=TEXT)

        # Lê ícone actual para preview
        threading.Thread(target=self._load_exe_preview, daemon=True).start()

    def _load_exe_preview(self):
        groups, _ = extract_icon_groups(self._exe_path)
        if groups:
            g = groups[0]
            best = max(g["frames"], key=lambda i: i.size[0])
            ctk_img = _make_ctk_img(best, 96)
            sizes = sorted(set(f.size[0] for f in g["frames"]))
            info = (f"{len(g['frames'])} frame(s): " +
                    ", ".join(f"{s}px" for s in sizes) +
                    f"\n{g['source']}  ·  ID #{g['res_id']}")
        else:
            ctk_img = None
            info = "Nenhum ícone encontrado"

        def update():
            if ctk_img:
                self._orig_img_lbl.configure(image=ctk_img, text="")
                self._orig_img_lbl._ctk_img = ctk_img
            else:
                self._orig_img_lbl.configure(image=None, text="❌")
            self._orig_info.configure(text=info)
            self._check_ready()

        self.after(0, update)

    # ── Selecção ICO ──────────────────────────────────────────────────────────
    def _pick_ico(self):
        path = filedialog.askopenfilename(
            title="Selecionar ficheiro ICO",
            filetypes=[("ICO", "*.ico"), ("Todos", "*.*")])
        if not path:
            return
        with open(path, "rb") as f:
            self._ico_bytes = f.read()
        self._ico_lbl.configure(text=Path(path).name, text_color=TEXT)

        # Preview e info
        dibs = ico_to_dibs(self._ico_bytes)
        sizes = sorted(dibs.keys(), key=lambda s: s[0])

        prev_img = ico_preview_image(self._ico_bytes)
        if prev_img:
            ctk_img = _make_ctk_img(prev_img, 96)
            self._new_img_lbl.configure(image=ctk_img, text="")
            self._new_img_lbl._ctk_img = ctk_img

        info = (f"{len(sizes)} frame(s): " +
                ", ".join(f"{w}px" for w, h in sizes))
        self._ico_info.configure(text=info)
        self._check_ready()

    def _check_ready(self):
        ready = self._exe_path is not None and self._ico_bytes is not None
        self._btn_inject.configure(state="normal" if ready else "disabled")

    # ── Injeção ───────────────────────────────────────────────────────────────
    def _do_inject(self):
        if not self._exe_path or not self._ico_bytes:
            return

        exe_p   = Path(self._exe_path)
        suffix  = self._suffix_var.get() or "_patched"
        out_name = exe_p.stem + suffix + exe_p.suffix
        out_path = filedialog.asksaveasfilename(
            title="Guardar EXE com novo ícone",
            defaultextension=exe_p.suffix,
            filetypes=[("Executável", f"*{exe_p.suffix}"), ("Todos", "*.*")],
            initialfile=out_name,
            initialdir=str(exe_p.parent))

        if not out_path:
            return

        self._btn_inject.configure(state="disabled", text="A injetar…")
        self._result_lbl.configure(text="A processar…", text_color=MUTED)

        def worker():
            ok, msg, stats = inject_icon(self._exe_path, self._ico_bytes, out_path)
            def done():
                self._btn_inject.configure(state="normal", text="🔧 Injetar ICO no EXE")
                if ok:
                    self._result_lbl.configure(
                        text=f"✓ Sucesso!\n{Path(out_path).name}",
                        text_color=SUCCESS)
                    detail = (f"Substituídos: {stats.get('replaced',0)}\n"
                              f"Redimensionados: {stats.get('resized',0)}\n"
                              f"Ignorados: {stats.get('skipped',0)}\n"
                              f"Saída: {msg}")
                    self._detail_lbl.configure(text=detail)
                else:
                    self._result_lbl.configure(text=f"✗ Erro:\n{msg}", text_color=ERROR_COL)
                    self._detail_lbl.configure(text="")
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════════
#  Separador — Criar ICO a partir de PNG/imagem
# ═══════════════════════════════════════════════════════════════════════════════

# Tamanhos standard Windows (todos os contextos cobertos)
ICO_SIZES = [16, 24, 32, 40, 48, 64, 72, 96, 128, 256]


class SizeToggle(ctk.CTkFrame):
    """Botão toggle para activar/desactivar um tamanho no ICO."""
    def __init__(self, master, size: int, enabled: bool = True, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.size = size
        self._var = ctk.BooleanVar(value=enabled)
        self._btn = ctk.CTkCheckBox(
            self, text=f"{size}×{size}",
            variable=self._var,
            font=("Consolas", 11),
            text_color=TEXT,
            fg_color=ACCENT,
            hover_color=ACCENT2,
            border_color=BORDER,
            checkmark_color=TEXT,
            width=100)
        self._btn.pack()

    @property
    def active(self) -> bool:
        return self._var.get()


class CreateIcoTab(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._src_img: "Image.Image | None" = None
        self._src_path: str = ""
        self._toggles: list[SizeToggle] = []
        self._build()

    def _build(self):
        # ── Coluna esquerda: fonte ────────────────────────────────────────────
        left = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        ctk.CTkLabel(left, text="① Imagem de origem",
                     font=("Segoe UI", 13, "bold"), text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(16, 8))

        ctk.CTkLabel(left,
                     text="PNG, JPG, BMP, WEBP — recomendado 512×512 ou maior",
                     font=("Segoe UI", 10), text_color=MUTED
                     ).pack(anchor="w", padx=20)

        ctk.CTkButton(left, text="📂 Selecionar imagem", width=170, height=34,
                      corner_radius=8, fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Segoe UI", 12),
                      command=self._pick_image).pack(anchor="w", padx=20, pady=(10, 0))

        # Preview
        self._preview_lbl = ctk.CTkLabel(left, text="—", font=("Segoe UI", 28))
        self._preview_lbl.pack(pady=(16, 4))

        self._src_info = ctk.CTkLabel(left, text="",
                                       font=("Consolas", 10), text_color=MUTED)
        self._src_info.pack(padx=20)

        # ── Coluna central: tamanhos ──────────────────────────────────────────
        mid = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        mid.pack(side="left", fill="both", expand=True, padx=8)

        ctk.CTkLabel(mid, text="② Tamanhos a incluir",
                     font=("Segoe UI", 13, "bold"), text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(mid,
                     text="Selecciona os tamanhos para o ficheiro .ico\n"
                          "Recomendado: activar todos",
                     font=("Segoe UI", 10), text_color=MUTED, justify="left"
                     ).pack(anchor="w", padx=20, pady=(0, 10))

        # Grid de checkboxes
        grid = ctk.CTkFrame(mid, fg_color="transparent")
        grid.pack(anchor="w", padx=20)

        for i, sz in enumerate(ICO_SIZES):
            # 256px activado por defeito, todos os outros também
            t = SizeToggle(grid, sz, enabled=True)
            t.grid(row=i // 2, column=i % 2, padx=4, pady=3, sticky="w")
            self._toggles.append(t)

        # Botões select all / none
        btn_row = ctk.CTkFrame(mid, fg_color="transparent")
        btn_row.pack(anchor="w", padx=20, pady=(10, 0))
        ctk.CTkButton(btn_row, text="Todos", width=70, height=26,
                      corner_radius=6, fg_color=CARD, hover_color=BORDER,
                      border_width=1, border_color=BORDER,
                      font=("Segoe UI", 10),
                      command=lambda: self._set_all(True)
                      ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Nenhum", width=70, height=26,
                      corner_radius=6, fg_color=CARD, hover_color=BORDER,
                      border_width=1, border_color=BORDER,
                      font=("Segoe UI", 10),
                      command=lambda: self._set_all(False)
                      ).pack(side="left")

        # ── Coluna direita: gerar ─────────────────────────────────────────────
        right = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ctk.CTkLabel(right, text="③ Gerar ICO",
                     font=("Segoe UI", 13, "bold"), text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(16, 8))

        # Opção de resampling
        ctk.CTkLabel(right, text="Filtro de redimensionamento:",
                     font=("Segoe UI", 11), text_color=MUTED
                     ).pack(anchor="w", padx=20)

        self._filter_var = ctk.StringVar(value="LANCZOS")
        filter_menu = ctk.CTkOptionMenu(
            right,
            values=["LANCZOS", "BICUBIC", "BILINEAR", "NEAREST"],
            variable=self._filter_var,
            width=160, height=30,
            fg_color=CARD, button_color=ACCENT,
            button_hover_color=ACCENT2,
            font=("Consolas", 11))
        filter_menu.pack(anchor="w", padx=20, pady=(4, 16))

        self._btn_generate = ctk.CTkButton(
            right, text="🎨 Gerar e Guardar ICO",
            width=200, height=42, corner_radius=10,
            fg_color=ACCENT, hover_color=ACCENT2,
            font=("Segoe UI", 13, "bold"),
            command=self._generate, state="disabled")
        self._btn_generate.pack(anchor="w", padx=20)

        # Resultado
        self._result_frame = ctk.CTkFrame(right, fg_color=CARD, corner_radius=10)
        self._result_frame.pack(fill="x", padx=16, pady=(16, 0))
        self._result_lbl = ctk.CTkLabel(
            self._result_frame,
            text="Seleccione uma imagem para começar.",
            font=("Segoe UI", 11), text_color=MUTED,
            wraplength=260, justify="left")
        self._result_lbl.pack(padx=14, pady=12)

        # Preview do ICO gerado
        ctk.CTkLabel(right, text="Preview do ICO gerado:",
                     font=("Segoe UI", 11), text_color=MUTED
                     ).pack(anchor="w", padx=20, pady=(16, 4))

        self._ico_preview_row = ctk.CTkFrame(right, fg_color="transparent")
        self._ico_preview_row.pack(anchor="w", padx=20, fill="x")

    # ── Selecção de imagem ────────────────────────────────────────────────────
    def _pick_image(self):
        path = filedialog.askopenfilename(
            title="Selecionar imagem",
            filetypes=[
                ("Imagens", "*.png *.jpg *.jpeg *.bmp *.webp *.tiff *.gif"),
                ("Todos", "*.*")])
        if not path:
            return
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a imagem:\n{e}")
            return

        self._src_img  = img
        self._src_path = path

        # Preview
        ctk_img = _make_ctk_img(img, 96)
        self._preview_lbl.configure(image=ctk_img, text="")
        self._preview_lbl._ctk_img = ctk_img

        w, h = img.size
        self._src_info.configure(
            text=f"{Path(path).name}\n{w}×{h}px  ·  {img.mode}")

        self._btn_generate.configure(state="normal")

    def _set_all(self, state: bool):
        for t in self._toggles:
            t._var.set(state)

    # ── Geração do ICO ────────────────────────────────────────────────────────
    def _generate(self):
        if self._src_img is None:
            return

        active_sizes = [t.size for t in self._toggles if t.active]
        if not active_sizes:
            messagebox.showwarning("Sem tamanhos", "Selecciona pelo menos um tamanho.")
            return

        # Sugestão de nome baseada no ficheiro fonte
        stem = Path(self._src_path).stem if self._src_path else "icon"
        dest = filedialog.asksaveasfilename(
            title="Guardar ICO",
            defaultextension=".ico",
            filetypes=[("ICO", "*.ico")],
            initialfile=f"{stem}.ico")
        if not dest:
            return

        self._btn_generate.configure(state="disabled", text="A gerar…")

        def worker():
            try:
                ok, msg, previews = _generate_ico(
                    self._src_img, active_sizes,
                    self._filter_var.get(), dest)
            except Exception as e:
                ok, msg, previews = False, str(e), []

            def done():
                self._btn_generate.configure(state="normal",
                                              text="🎨 Gerar e Guardar ICO")
                if ok:
                    self._result_lbl.configure(text=f"✓ ICO gerado!\n{msg}",
                                                text_color=SUCCESS)
                    self._show_ico_preview(previews)
                else:
                    self._result_lbl.configure(text=f"✗ Erro:\n{msg}",
                                                text_color=ERROR_COL)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _show_ico_preview(self, previews: list):
        """Mostra miniaturas dos frames gerados."""
        for w in self._ico_preview_row.winfo_children():
            w.destroy()
        for img, sz in previews[:8]:   # mostra no máximo 8
            f = ctk.CTkFrame(self._ico_preview_row, fg_color="transparent")
            f.pack(side="left", padx=3)
            thumb = _make_ctk_img(img, 32)
            lbl = ctk.CTkLabel(f, image=thumb, text="")
            lbl._ctk_img = thumb
            lbl.pack()
            ctk.CTkLabel(f, text=str(sz), font=("Consolas", 8),
                         text_color=MUTED).pack()


def _generate_ico(src_img: "Image.Image", sizes: list[int],
                  filter_name: str, dest_path: str
                  ) -> tuple[bool, str, list]:
    """
    Gera um ICO multi-frame a partir de uma imagem PIL.
    Retorna (sucesso, mensagem, [(img, size), ...] para preview).
    """
    filter_map = {
        "LANCZOS":  Image.LANCZOS,
        "BICUBIC":  Image.BICUBIC,
        "BILINEAR": Image.BILINEAR,
        "NEAREST":  Image.NEAREST,
    }
    resample = filter_map.get(filter_name, Image.LANCZOS)

    frames = []
    previews = []

    for sz in sorted(sizes):
        resized = src_img.resize((sz, sz), resample).convert("RGBA")
        frames.append(resized)
        previews.append((resized.copy(), sz))

    if not frames:
        return False, "Sem frames para gerar", []

    ico_bytes = build_ico_bytes(frames)
    if not ico_bytes:
        return False, "Erro ao montar ICO", []

    with open(dest_path, "wb") as f:
        f.write(ico_bytes)

    sizes_str = ", ".join(f"{s}×{s}" for s in sorted(sizes))
    msg = f"{len(frames)} tamanhos: {sizes_str}\nGuardado em: {Path(dest_path).name}"
    return True, msg, previews


# ═══════════════════════════════════════════════════════════════════════════════
#  App principal com separadores
# ═══════════════════════════════════════════════════════════════════════════════

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("EXE Icon Extractor & Injector")
        self.geometry("1100x760")
        self.minsize(900, 580)
        self.configure(fg_color=BG)
        self._cards:  list[IconGroupCard] = []
        self._scroll  = None
        self._build_ui()

    def _build_ui(self):
        # Cabeçalho
        hdr = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="⬡  EXE Icon Extractor & Injector",
                     font=("Segoe UI", 19, "bold"), text_color=TEXT
                     ).pack(side="left", padx=24)
        ctk.CTkLabel(hdr, text="Extrai · Converte · Injeta ícones em executáveis Windows",
                     font=("Segoe UI", 11), text_color=MUTED).pack(side="left")

        # Separadores
        self._tabs = ctk.CTkTabview(self, fg_color=BG,
                                     segmented_button_fg_color=PANEL,
                                     segmented_button_selected_color=ACCENT,
                                     segmented_button_selected_hover_color=ACCENT2,
                                     segmented_button_unselected_color=PANEL,
                                     segmented_button_unselected_hover_color=BORDER,
                                     text_color=TEXT)
        self._tabs.pack(fill="both", expand=True, padx=12, pady=(8, 12))

        self._tabs.add("📤  Extrair Ícones")
        self._tabs.add("📥  Injetar ICO")
        self._tabs.add("🎨  Criar ICO")

        self._build_extract_tab(self._tabs.tab("📤  Extrair Ícones"))
        InjectTab(self._tabs.tab("📥  Injetar ICO")).pack(fill="both", expand=True, padx=8, pady=8)
        CreateIcoTab(self._tabs.tab("🎨  Criar ICO")).pack(fill="both", expand=True, padx=8, pady=8)

    # ── Separador Extrair ─────────────────────────────────────────────────────
    def _build_extract_tab(self, tab):
        # Toolbar
        tb = ctk.CTkFrame(tab, fg_color=PANEL, corner_radius=10, height=48)
        tb.pack(fill="x", pady=(0, 8))
        tb.pack_propagate(False)

        ctk.CTkButton(tb, text="📂 Adicionar EXE(s)", width=155, height=34,
                      corner_radius=8, fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Segoe UI", 12, "bold"),
                      command=self._pick_files).pack(side="left", padx=10, pady=7)
        ctk.CTkButton(tb, text="📁 Pasta", width=100, height=34,
                      corner_radius=8, fg_color=CARD, hover_color=BORDER,
                      border_width=1, border_color=BORDER,
                      font=("Segoe UI", 12),
                      command=self._pick_folder).pack(side="left", padx=(0, 8), pady=7)
        ctk.CTkButton(tb, text="🗑 Limpar", width=85, height=34,
                      corner_radius=8, fg_color=CARD, hover_color="#2a1515",
                      border_width=1, border_color=BORDER,
                      font=("Segoe UI", 12), text_color=ERROR_COL,
                      command=self._clear).pack(side="left", pady=7)

        self._btn_save_all = ctk.CTkButton(
            tb, text="💾 Guardar Todos (ICO)", width=170, height=34,
            corner_radius=8, fg_color="#1a2a1a", hover_color="#243524",
            border_width=1, border_color=SUCCESS,
            font=("Segoe UI", 12), text_color=SUCCESS,
            command=self._save_all, state="disabled")
        self._btn_save_all.pack(side="right", padx=10, pady=7)

        self._status = ctk.StringVar(value="Pronto. Adicione ficheiros .exe para extrair ícones.")
        ctk.CTkLabel(tab, textvariable=self._status,
                     font=("Consolas", 11), text_color=MUTED, anchor="w"
                     ).pack(fill="x", padx=4, pady=(0, 4))

        self._progress = ctk.CTkProgressBar(tab, fg_color=CARD, progress_color=ACCENT,
                                            height=4, corner_radius=2)
        self._progress.set(0)

        self._main = ctk.CTkFrame(tab, fg_color="transparent")
        self._main.pack(fill="both", expand=True)
        self._show_drop_zone()

    def _show_drop_zone(self):
        for w in self._main.winfo_children():
            w.destroy()
        self._cards.clear()
        self._scroll = None
        dz = ctk.CTkFrame(self._main, fg_color=CARD, corner_radius=16,
                          border_width=2, border_color=BORDER)
        dz.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.52, relheight=0.62)
        ctk.CTkLabel(dz, text="🗂", font=("Segoe UI Emoji", 48)).pack(pady=(32, 6))
        ctk.CTkLabel(dz, text="Adicione ficheiros .exe",
                     font=("Segoe UI", 16, "bold"), text_color=TEXT).pack()
        ctk.CTkLabel(dz, text="Suporta EXE, DLL e ficheiros empacotados\n(PyInstaller, NSIS, Electron…)",
                     font=("Segoe UI", 11), text_color=MUTED, justify="center").pack(pady=(4, 20))
        ctk.CTkButton(dz, text="📂 Selecionar EXE(s)", width=175, height=38,
                      corner_radius=10, fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Segoe UI", 12, "bold"),
                      command=self._pick_files).pack()

    def _ensure_scroll(self):
        if self._scroll is None:
            for w in self._main.winfo_children():
                w.destroy()
            self._scroll = ctk.CTkScrollableFrame(self._main, fg_color="transparent",
                                                   scrollbar_button_color=BORDER,
                                                   scrollbar_button_hover_color=ACCENT)
            self._scroll.pack(fill="both", expand=True)

    def _pick_files(self):
        paths = filedialog.askopenfilenames(
            title="Selecionar EXE(s)",
            filetypes=[("Executáveis", "*.exe *.dll"), ("Todos", "*.*")])
        if paths:
            self._run(list(paths))

    def _pick_folder(self):
        folder = filedialog.askdirectory(title="Selecionar pasta")
        if not folder:
            return
        found = (glob.glob(os.path.join(folder, "**", "*.exe"), recursive=True) +
                 glob.glob(os.path.join(folder, "**", "*.dll"), recursive=True))
        if not found:
            messagebox.showwarning("Sem EXEs", "Nenhum .exe/.dll encontrado.")
            return
        self._run(found)

    def _clear(self):
        self._cards.clear()
        self._btn_save_all.configure(state="disabled")
        self._status.set("Limpo.")
        self._progress.pack_forget()
        self._show_drop_zone()

    def _run(self, paths):
        self._ensure_scroll()
        self._progress.pack(fill="x", padx=4, pady=(0, 4))
        self._progress.set(0)
        threading.Thread(target=self._worker, args=(paths,), daemon=True).start()

    def _worker(self, paths):
        total = len(paths)
        n_groups = n_errors = 0
        log = []
        for i, path in enumerate(paths):
            name = Path(path).name
            self.after(0, self._status.set, f"A processar {i+1}/{total}: {name}…")
            self.after(0, self._progress.set, (i + 1) / total)
            groups, err = extract_icon_groups(path)
            if err and not groups:
                n_errors += 1
                log.append(f"⚠ {name}: {err}")
            else:
                for g in groups:
                    self.after(0, self._add_card, g)
                    n_groups += 1

        def finish():
            self._progress.pack_forget()
            parts = [f"✓ {n_groups} grupo(s) extraído(s)"]
            if n_errors:
                parts.append(f"⚠ {n_errors} sem ícones")
            self._status.set("  ".join(parts))
            if n_groups > 0:
                self._btn_save_all.configure(state="normal")
            elif n_errors == total:
                messagebox.showwarning("Sem ícones",
                                       "Nenhum ícone encontrado.\n\n" + "\n".join(log[:10]))
        self.after(0, finish)

    def _add_card(self, group):
        if self._scroll is None:
            return
        try:
            cols = max(2, self._scroll.winfo_width() // 210)
        except Exception:
            cols = 4
        n = len(self._cards)
        card = IconGroupCard(self._scroll, group)
        card.grid(row=n // cols, column=n % cols, padx=8, pady=8, sticky="nsew")
        self._scroll.grid_columnconfigure(n % cols, weight=1)
        self._cards.append(card)

    def _save_all(self):
        if not self._cards:
            return
        folder = filedialog.askdirectory(title="Pasta de destino")
        if not folder:
            return
        saved = 0
        for card in self._cards:
            g = card._g
            sizes = sorted(set(f.size[0] for f in g["frames"]))
            name = f"icon_grp{g['group_idx']}_id{g['res_id']}_{'x'.join(str(s) for s in sizes)}.ico"
            with open(os.path.join(folder, name), "wb") as f:
                f.write(g["ico_bytes"])
            saved += 1
        messagebox.showinfo("Guardado", f"{saved} ficheiro(s) .ico guardado(s) em:\n{folder}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
