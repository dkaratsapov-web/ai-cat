"""QC идентичности кота и превью-кадры.

Автоматика здесь — только подсказки (сдвиг окраса относительно референса). Вердикт «внешность совпадает»
ставит человек (владелец/директор) после просмотра контактного листа: машина его не утверждает.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from ..character.library import CharacterLibrary
from ..editing import ffmpeg, fonts
from ..project import Project
from . import state

CHECKLIST = [
    ("folded_ears", "Уши сложены и прижаты (не стоят)"),
    ("tabby", "Окрас: серебристо-серый табби, мраморные полосы"),
    ("face", "Строение мордочки: круглая, плоская, розовый нос, усы"),
    ("eyes", "Цвет глаз: янтарно-зелёные, круглые"),
    ("white_paws_chest", "Белые лапы и грудка"),
    ("hoodie", "Чёрное худи на месте"),
    ("proportions", "Пропорции тела правильные"),
    ("no_extra_limbs", "Нет лишних лап/пальцев"),
    ("no_human_features", "Нет человеческих черт лица"),
    ("no_breed_drift", "Порода не «уплыла» (не другой кот)"),
]


def frame_at(video: Path, t: float, out: Path, width: int = 360) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(t, 0):.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)], capture_output=True)
    return out if r.returncode == 0 and out.exists() else None


def preview_frames(video: Path, out_dir: Path, n: int = 4, width: int = 360) -> list[Path]:
    dur = ffmpeg.duration(video)
    pts = [dur * (i + 0.5) / n for i in range(n)]
    return [p for i, t in enumerate(pts) if (p := frame_at(video, t, out_dir / f"f{i + 1}_{t:.1f}s.jpg", width))]


def sheet(images: list[Path | Image.Image], labels: list[str], out: Path, cols: int = 5, cell_w: int = 300) -> Path:
    ims = [Image.open(i).convert("RGB") if isinstance(i, Path) else i.convert("RGB") for i in images]
    cell_h = max(int(cell_w * im.height / im.width) for im in ims) if ims else cell_w
    rows = (len(ims) + cols - 1) // cols
    f = fonts.font(Path("assets/fonts"), 22)
    canvas = Image.new("RGB", (cols * cell_w, rows * (cell_h + 34)), (24, 26, 32))
    d = ImageDraw.Draw(canvas)
    for k, (im, lab) in enumerate(zip(ims, labels)):
        x, y = (k % cols) * cell_w, (k // cols) * (cell_h + 34)
        canvas.paste(im.resize((cell_w, int(cell_w * im.height / im.width))), (x, y + 34))
        d.text((x + 8, y + 6), lab, font=f, fill=(255, 212, 59))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=88)
    return out


def _center_hist(im: Image.Image) -> list[float]:
    w, h = im.size
    c = im.crop((int(w * .25), int(h * .15), int(w * .75), int(h * .6))).convert("HSV").resize((96, 96))
    hist = c.getchannel("S").histogram()[::8] + c.getchannel("V").histogram()[::8]
    s = float(sum(hist)) or 1.0
    return [v / s for v in hist]


def fur_tone_similarity(ref: Path, frames: list[Path]) -> float:
    """0–1: насколько тон центральной области кадров похож на референс (подсказка, не вердикт)."""
    hr = _center_hist(Image.open(ref))
    sims = []
    for f in frames:
        hf = _center_hist(Image.open(f))
        sims.append(sum((a * b) ** 0.5 for a, b in zip(hr, hf)))   # коэффициент Бхаттачарьи
    return round(sum(sims) / len(sims), 3) if sims else 0.0


def prepare_identity_qc(project: Project, scene_id: str) -> dict:
    """Готовит материалы QC: контактный лист «референс + кадры клипа», подсказку по окрасу, чек-лист."""
    script = project.load_script()
    sc = next(s for s in script.scenes if s.id == scene_id)
    src = project.scene_source(scene_id)
    if not src:
        raise state.StateError(f"{scene_id}: нет видео сцены — QC нечего проверять")
    ref, ref_path = CharacterLibrary(project.settings).resolve(sc.reference)
    qdir = project.dir("scenes") / scene_id / "qc"
    frames = preview_frames(src, qdir, n=4)
    sheet([Path(ref_path)] + frames, [f"референс {ref['id']}"] + [p.stem.split("_", 1)[1] for p in frames],
          qdir / "identity_sheet.jpg")
    sim = fur_tone_similarity(Path(ref_path), frames)
    hint = "ок" if sim >= 0.85 else ("проверить окрас" if sim >= 0.7 else "ОКРАС СИЛЬНО ОТЛИЧАЕТСЯ — вероятно, другой кот")
    md = [f"# QC идентичности — {scene_id}", "", f"Контактный лист: `identity_sheet.jpg` (референс + 4 кадра клипа).",
          f"Подсказка по окрасу: сходство {sim} — {hint}. Это не вердикт.", "", "Чек-лист (отмечает человек):",
          *[f"- [ ] {txt}" for _k, txt in CHECKLIST], "",
          f"Вердикт: `studio idqc {project.id} {scene_id} --pass` или `--fail --notes \"что не так\"`"]
    (qdir / "identity_qc.md").write_text("\n".join(md), encoding="utf-8")
    return {"sheet": str(qdir / "identity_sheet.jpg"), "similarity": sim, "hint": hint, "frames": [str(f) for f in frames]}


def record_verdict(project: Project, scene_id: str, verdict: str, notes: str = "", reviewer: str = "owner") -> dict:
    if verdict not in ("pass", "fail", "manual"):
        raise state.StateError("Вердикт: pass | fail | manual")
    data = state.load(project)
    st = data.setdefault(scene_id, {"status": "generated", "history": [], "identity_qc": None, "versions": 0})
    if st.get("versions", 0) < 1:
        raise state.StateError(f"{scene_id}: ещё нет сгенерированной версии")
    st["identity_qc"] = {"verdict": verdict, "notes": notes, "reviewer": reviewer, "version": st.get("versions"),
                         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state.save(project, data)
    if verdict == "fail" and st["status"] in ("generated", "review", "final"):
        state.set_status(project, scene_id, "revise", f"QC идентичности: {notes}", force=True)
    return st["identity_qc"]


def qc_summary(project: Project) -> list[dict]:
    data = state.load(project)
    return [{"scene": sid, "status": st["status"], "identity_qc": (st.get("identity_qc") or {}).get("verdict", "—"),
             "version": st.get("versions", 0)} for sid, st in data.items()]


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)
