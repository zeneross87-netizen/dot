"""도트(픽셀아트) 영상 변환 코어.

파이프라인:
  원본 프레임 -> (가로 N 도트) 축소 -> 채도/대비 보정 -> 팔레트 양자화(+디더링)
  -> 정수 배율 최근접 확대 -> ffmpeg 로 H.264 인코딩 + 원본 오디오 합치기

팔레트는 영상 전체에서 샘플링해 한 번만 만들기 때문에 프레임마다 색이 깜빡이지 않습니다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

# ---------------------------------------------------------------- 팔레트 프리셋
PRESETS = {
    "게임보이 (4색)": ["0F380F", "306230", "8BAC0F", "9BBC0F"],
    "PICO-8 (16색)": [
        "000000", "1D2B53", "7E2553", "008751", "AB5236", "5F574F", "C2C3C7", "FFF1E8",
        "FF004D", "FFA300", "FFEC27", "00E436", "29ADFF", "83769C", "FF77A8", "FFCCAA",
    ],
    "흑백 (4단계)": ["000000", "555555", "AAAAAA", "FFFFFF"],
}
AUTO_PALETTE = "자동 (영상에서 추출)"

DITHER_MODES = ["없음", "4x4 규칙 디더", "8x8 규칙 디더"]


def _bayer(n: int) -> np.ndarray:
    if n == 1:
        return np.zeros((1, 1))
    m = _bayer(n // 2)
    return np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])


def _bayer_norm(n: int) -> np.ndarray:
    b = _bayer(n)
    return (b + 0.5) / (n * n) - 0.5  # -0.5 ~ +0.5


BAYER = {"4x4 규칙 디더": _bayer_norm(4), "8x8 규칙 디더": _bayer_norm(8)}


@dataclass
class Config:
    src: str = ""
    dst: str = ""
    grid_w: int = 192            # 가로 도트 수
    colors: int = 48             # 자동 팔레트 색 수
    palette: str = AUTO_PALETTE  # AUTO_PALETTE 또는 PRESETS 키
    dither: str = "4x4 규칙 디더"
    dither_strength: float = 5.0
    saturation: float = 1.1
    contrast: float = 1.05
    scale: int = 0               # 0 = 원본 크기에 가깝게 자동
    keep_audio: bool = True
    crf: int = 16


@dataclass
class Palette:
    lab: np.ndarray  # (K,3) float32
    bgr: np.ndarray  # (K,3) uint8


# ---------------------------------------------------------------- 유틸
def find_ffmpeg() -> str:
    try:
        import imageio_ffmpeg  # requirements 에 포함 (윈도우용 ffmpeg 내장)
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise RuntimeError("ffmpeg 를 찾을 수 없습니다. `pip install imageio-ffmpeg` 를 실행하세요.")


def grid_size(cfg: Config, src_w: int, src_h: int) -> tuple[int, int]:
    gw = max(8, int(cfg.grid_w))
    gw -= gw % 2
    gh = max(8, round(gw * src_h / src_w))
    gh -= gh % 2
    return gw, gh


def output_scale(cfg: Config, src_w: int, gw: int) -> int:
    if cfg.scale and cfg.scale > 0:
        return int(cfg.scale)
    return max(1, round(src_w / gw))


def _bgr_to_lab(bgr_u8: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr_u8.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)


def _lab_to_bgr_u8(lab: np.ndarray) -> np.ndarray:
    bgr = cv2.cvtColor(lab.astype(np.float32).reshape(1, -1, 3), cv2.COLOR_LAB2BGR)
    return np.clip(bgr * 255.0 + 0.5, 0, 255).astype(np.uint8).reshape(-1, 3)


def prepare_small(bgr: np.ndarray, cfg: Config, gw: int, gh: int) -> np.ndarray:
    """축소 + 채도/대비 보정. uint8 BGR 반환."""
    small = cv2.resize(bgr, (gw, gh), interpolation=cv2.INTER_AREA)
    if abs(cfg.saturation - 1.0) > 1e-3:
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * cfg.saturation, 0, 255)
        small = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    if abs(cfg.contrast - 1.0) > 1e-3:
        f = small.astype(np.float32)
        f = (f - 127.5) * cfg.contrast + 127.5
        small = np.clip(f, 0, 255).astype(np.uint8)
    return small


def _hex_to_bgr(h: str) -> list[int]:
    return [int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)]


def build_palette(cfg: Config, samples: list[np.ndarray]) -> Palette:
    """samples: prepare_small 을 거친 여러 프레임 (uint8 BGR)."""
    if cfg.palette in PRESETS:
        bgr = np.array([_hex_to_bgr(h) for h in PRESETS[cfg.palette]], dtype=np.uint8)
        return Palette(_bgr_to_lab(bgr.reshape(1, -1, 3)).reshape(-1, 3), bgr)

    data = np.concatenate([_bgr_to_lab(s).reshape(-1, 3) for s in samples], axis=0)
    if len(data) > 150_000:
        rng = np.random.default_rng(0)
        data = data[rng.choice(len(data), 150_000, replace=False)]
    k = int(max(2, min(cfg.colors, len(data))))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 25, 0.3)
    cv2.setRNGSeed(1)
    _, _, centers = cv2.kmeans(data.astype(np.float32), k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    bgr = _lab_to_bgr_u8(centers)
    # 양자화된 BGR 기준으로 Lab 을 다시 계산해 매핑과 출력이 정확히 일치하게 함
    lab = _bgr_to_lab(bgr.reshape(1, -1, 3)).reshape(-1, 3)
    return Palette(lab, bgr)


def sample_frames(cap: cv2.VideoCapture, cfg: Config, gw: int, gh: int, n: int) -> list[np.ndarray]:
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(n):
        pos = int((i + 0.5) * total / n)
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ok, frame = cap.read()
        if ok:
            out.append(prepare_small(frame, cfg, gw, gh))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return out


def quantize(small_bgr: np.ndarray, pal: Palette, cfg: Config) -> np.ndarray:
    """작은 프레임을 팔레트 색으로 치환 (uint8 BGR, 크기 동일)."""
    h, w = small_bgr.shape[:2]
    lab = _bgr_to_lab(small_bgr)
    if cfg.dither in BAYER and cfg.dither_strength > 0:
        m = BAYER[cfg.dither]
        tile = np.tile(m, (h // m.shape[0] + 1, w // m.shape[1] + 1))[:h, :w]
        lab = lab + tile[..., None] * cfg.dither_strength
    px = lab.reshape(-1, 3)
    # (N,K) 거리행렬 — 도트 해상도가 작아 빠름
    d = ((px[:, None, :] - pal.lab[None, :, :]) ** 2).sum(-1)
    idx = d.argmin(1)
    return pal.bgr[idx].reshape(h, w, 3)


def upscale(img: np.ndarray, scale: int) -> np.ndarray:
    if scale == 1:
        return img
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


# ---------------------------------------------------------------- 미리보기
class Previewer:
    def __init__(self) -> None:
        self._key = None
        self._pal: Optional[Palette] = None

    def render(self, cfg: Config, frac: float) -> tuple[np.ndarray, np.ndarray]:
        """(원본 프레임, 도트 프레임) BGR 반환."""
        cap = cv2.VideoCapture(cfg.src)
        if not cap.isOpened():
            raise RuntimeError("영상을 열 수 없습니다.")
        try:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            gw, gh = grid_size(cfg, w, h)
            key = (cfg.src, gw, cfg.colors, cfg.palette, cfg.saturation, cfg.contrast)
            if key != self._key:
                self._pal = build_palette(cfg, sample_frames(cap, cfg, gw, gh, 10))
                self._key = key
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frac * (total - 1)))
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("프레임을 읽을 수 없습니다.")
            small = prepare_small(frame, cfg, gw, gh)
            dot = upscale(quantize(small, self._pal, cfg), output_scale(cfg, w, gw))
            return frame, dot
        finally:
            cap.release()


# ---------------------------------------------------------------- 변환 본체
def process_video(
    cfg: Config,
    progress: Callable[[float, str], None] = lambda p, m: None,
    cancel: Optional[threading.Event] = None,
) -> None:
    cap = cv2.VideoCapture(cfg.src)
    if not cap.isOpened():
        raise RuntimeError("영상을 열 수 없습니다.")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    gw, gh = grid_size(cfg, w, h)
    sc = output_scale(cfg, w, gw)
    ow, oh = gw * sc, gh * sc

    progress(0.0, "팔레트 추출 중...")
    pal = build_palette(cfg, sample_frames(cap, cfg, gw, gh, 40))

    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}", "-r", f"{fps:.5f}", "-i", "-",
    ]
    if cfg.keep_audio:
        cmd += ["-i", cfg.src, "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", cfg.dst]

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err, creationflags=flags)
    done = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("취소됨")
            ok, frame = cap.read()
            if not ok:
                break
            small = prepare_small(frame, cfg, gw, gh)
            out = upscale(quantize(small, pal, cfg), sc)
            proc.stdin.write(np.ascontiguousarray(out).tobytes())
            done += 1
            if done % 5 == 0:
                progress(min(done / total, 0.99) if total else 0.5, f"변환 중... {done}/{total or '?'} 프레임")
        proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            err.seek(0)
            raise RuntimeError("ffmpeg 오류:\n" + err.read().decode(errors="replace")[-800:])
        progress(1.0, f"완료: {cfg.dst}")
    except BaseException:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()
        if os.path.exists(cfg.dst):
            try:
                os.remove(cfg.dst)
            except OSError:
                pass
        raise
    finally:
        cap.release()
        err.close()


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="영상을 도트 영상으로 변환")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--width", type=int, default=192)
    ap.add_argument("--colors", type=int, default=48)
    ap.add_argument("--dither", default="4x4 규칙 디더", choices=DITHER_MODES)
    a = ap.parse_args()
    c = Config(src=a.src, dst=a.dst, grid_w=a.width, colors=a.colors, dither=a.dither)
    process_video(c, lambda p, m: print(f"\r{p*100:5.1f}% {m}", end=""))
    print()
