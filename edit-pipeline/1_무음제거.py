#!/usr/bin/env python3
"""1_무음제거.py — MP4 무음 구간 자동 제거

Phase 1: silencedetect (rough cut)
Phase 2: Whisper 전사 (rough cut 대상만)
Phase 3: 컷 포인트 산출 + SRT 생성
Phase 4: 영상 컷 + 합치기
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


# ─── Phase 1 ────────────────────────────────────────────────────────────────

def detect_silence(input_path: str, thresh: float = -30, min_duration: float = 0.5) -> list[tuple[float, float]]:
    """FFmpeg silencedetect → 무음 구간 [(start, end), ...]"""
    cmd = [
        "ffmpeg", "-i", input_path,
        "-af", f"silencedetect=noise={thresh}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr

    starts = re.findall(r"silence_start: ([\d.]+)", stderr)
    ends = re.findall(r"silence_end: ([\d.]+)", stderr)

    silences = [(float(s), float(e)) for s, e in zip(starts, ends)]

    # 마지막 silence_start만 있고 silence_end가 없는 경우 (영상 끝까지 무음)
    if len(starts) > len(ends):
        total = get_duration(input_path)
        silences.append((float(starts[-1]), total))

    return silences


def get_duration(input_path: str) -> float:
    """ffprobe로 총 재생시간 반환"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def invert_to_speech(
    silences: list[tuple[float, float]],
    total_duration: float,
    pad_start: float = 0.5,
    pad_end: float = 0.5,
) -> list[tuple[float, float]]:
    """무음 반전 → 음성 구간 (비대칭 padding 지원), 겹치는 구간 병합"""
    if not silences:
        return [(0.0, total_duration)]

    speech = []
    prev_end = 0.0

    for silence_start, silence_end in silences:
        seg_end = silence_start
        if seg_end > prev_end:
            s = max(0.0, prev_end - pad_start)
            e = min(total_duration, seg_end + pad_end)
            speech.append((s, e))
        prev_end = silence_end

    # 마지막 구간
    if prev_end < total_duration:
        s = max(0.0, prev_end - pad_start)
        speech.append((s, total_duration))

    # 겹치는 구간 병합
    if not speech:
        return []

    merged = [list(speech[0])]
    for s, e in speech[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    return [(s, e) for s, e in merged]


def refine_segment_boundaries(
    input_path: str,
    segments: list[tuple[float, float]],
    window: float = 0.5,
) -> list[tuple[float, float]]:
    """Silero VAD 기반 구간 경계 미세조정 (±window 범위에서 speech 타임스탬프 탐색)"""
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    # 전체 PCM 한 번만 추출 (16kHz mono float32)
    cmd = ["ffmpeg", "-i", input_path, "-vn", "-ar", "16000", "-ac", "1", "-f", "f32le", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return segments

    pcm_full = np.frombuffer(result.stdout, dtype=np.float32)
    sample_rate = 16000
    total_samples = len(pcm_full)

    vad_opts = VadOptions(
        threshold=0.35,
        neg_threshold=0.2,
        min_speech_duration_ms=50,
        min_silence_duration_ms=50,
        speech_pad_ms=0,
    )

    def refine_boundary(t: float, side: str) -> float:
        lo_s = max(0, int((t - window) * sample_rate))
        hi_s = min(total_samples, int((t + window) * sample_rate))
        chunk = pcm_full[lo_s:hi_s]
        if len(chunk) == 0:
            return t
        timestamps = get_speech_timestamps(chunk, vad_opts)
        if not timestamps:
            return t
        lo = lo_s / sample_rate
        if side == "start":
            return lo + timestamps[0]["start"] / sample_rate
        else:
            return lo + timestamps[-1]["end"] / sample_rate

    refined = []
    for seg_start, seg_end in segments:
        new_start = refine_boundary(seg_start, "start")
        new_end = refine_boundary(seg_end, "end")
        if new_start >= new_end:
            new_start, new_end = seg_start, seg_end
        refined.append((new_start, new_end))

    return refined


# ─── Phase 2 ────────────────────────────────────────────────────────────────

def transcribe_segments(
    input_path: str,
    speech_segments: list[tuple[float, float]],
    tmpdir: str,
) -> list[dict]:
    """Whisper로 음성 구간 전사 → 원본 타임라인 기준 단어 목록"""
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8")
    all_words = []

    for i, (seg_start, seg_end) in enumerate(speech_segments):
        duration = seg_end - seg_start
        if duration <= 0:
            continue

        print(f"  구간 {i+1}/{len(speech_segments)} 전사 중...", flush=True)
        wav_path = os.path.join(tmpdir, f"seg_{i:04d}.wav")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(seg_start), "-to", str(seg_end),
            "-i", input_path,
            "-vn", "-ar", "16000", "-ac", "1",
            wav_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)

        segments, _ = model.transcribe(wav_path, language="ko", word_timestamps=True)

        for segment in segments:
            if not segment.words:
                continue
            for word in segment.words:
                all_words.append({
                    "word": word.word,
                    "start": word.start + seg_start,
                    "end": word.end + seg_start,
                })

    return all_words


# ─── Phase 3 ────────────────────────────────────────────────────────────────

FILLER_WORDS = {"음", "어", "그", "뭐", "아", "이", "저", "에"}


def filter_fillers(words: list[dict]) -> list[dict]:
    """필러 단어를 자막 대상에서 제외 (컷 포인트 계산에는 영향 없음)"""
    return [w for w in words if w["word"].strip() not in FILLER_WORDS]


def compute_cut_points(
    words: list[dict],
    mode: str = "segment",
    padding: float = 0.2,
    gap: float = 0.7,
    pad_start: float = None,
    pad_end: float = None,
) -> list[tuple[float, float]]:
    """
    단어 목록 → 최종 컷 구간 [(start, end), ...]
    mode="segment": 큰 침묵(2초)으로 문장 단위 그룹핑
    mode="tight":   단어 간 gap > 임계값이면 분리
    pad_start/pad_end: 비대칭 패딩 (None이면 padding 값 사용)
    """
    if not words:
        return []

    _pad_start = pad_start if pad_start is not None else padding
    _pad_end = pad_end if pad_end is not None else padding

    SENTENCE_GAP = 2.0
    threshold = gap if mode == "tight" else SENTENCE_GAP

    groups: list[list[dict]] = []
    current: list[dict] = [words[0]]

    for word in words[1:]:
        if word["start"] - current[-1]["end"] > threshold:
            groups.append(current)
            current = [word]
        else:
            current.append(word)
    groups.append(current)

    segments = []
    for group in groups:
        s = max(0.0, group[0]["start"] - _pad_start)
        e = group[-1]["end"] + _pad_end
        segments.append((s, e))

    # 겹치거나 인접한 구간 병합
    merged = [list(segments[0])]
    for s, e in segments[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    return [(s, e) for s, e in merged]


def _is_split_point(text: str) -> int:
    """분할 우선순위 반환. 0=아님, 1=강(문장끝), 2=중(쉼표)"""
    if not text:
        return 0
    last = text.rstrip()
    if not last:
        return 0
    if last[-1] in ".!?。":
        return 1
    if last[-1] in ",，":
        return 2
    bare = last.rstrip(",.!?")
    if bare.endswith(("니다", "거든요", "는데요", "잖아요")):
        return 1
    if bare.endswith(("다", "요", "죠")):
        return 1
    return 0


def _chunk_by_meaning(words, max_chars=20, min_chars=10):
    chunks = []
    buf = []
    buf_len = 0

    for w in words:
        text = w["word"].strip()
        added = len(text) + (1 if buf else 0)

        if buf and buf_len + added > max_chars:
            chunks.append(buf)
            buf = [w]
            buf_len = len(text)
            continue

        buf.append(w)
        buf_len += added

        level = _is_split_point(text)
        if level == 1:
            chunks.append(buf)
            buf = []
            buf_len = 0
        elif level == 2 and buf_len >= min_chars:
            chunks.append(buf)
            buf = []
            buf_len = 0

    if buf:
        chunks.append(buf)
    return chunks


def _to_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(
    words: list[dict],
    segments: list[tuple[float, float]],
    output_path: str,
) -> None:
    """컷 후 타임라인 기준 SRT 자막 생성"""
    # 각 원본 구간의 컷 후 오프셋 계산
    offsets: list[tuple[float, float, float]] = []  # (seg_start, seg_end, cut_offset)
    cumulative = 0.0
    for seg_start, seg_end in segments:
        offsets.append((seg_start, seg_end, cumulative))
        cumulative += seg_end - seg_start

    def map_time(t: float):
        for seg_start, seg_end, offset in offsets:
            if seg_start <= t <= seg_end:
                return offset + (t - seg_start)
        return None

    # 단어를 구간별로 배분
    seg_words: list[list[dict]] = [[] for _ in segments]
    for word in words:
        mid = (word["start"] + word["end"]) / 2
        for i, (seg_start, seg_end) in enumerate(segments):
            if seg_start <= mid <= seg_end:
                seg_words[i].append(word)
                break

    MAX_CHARS = 20
    MIN_CHARS = 10
    srt_lines: list[str] = []
    entry = 1

    for i, group in enumerate(seg_words):
        if not group:
            continue
        for chunk in _chunk_by_meaning(group, MAX_CHARS, MIN_CHARS):
            t_start = map_time(chunk[0]["start"])
            t_end = map_time(chunk[-1]["end"])
            if t_start is None or t_end is None:
                continue
            text = "".join(w["word"] for w in chunk).strip()
            if not text:
                continue
            srt_lines.append(
                f"{entry}\n{_to_srt_time(t_start)} --> {_to_srt_time(t_end)}\n{text}\n"
            )
            entry += 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))


# ─── Phase 4 ────────────────────────────────────────────────────────────────

def extract_and_concat(
    input_path: str,
    segments: list[tuple[float, float]],
    output_path: str,
    tmpdir: str,
) -> None:
    """각 구간을 re-encode로 추출 후 concat demuxer로 합치기"""
    segment_files = []

    for i, (start, end) in enumerate(segments):
        print(f"  구간 {i+1}/{len(segments)} 추출 중...", flush=True)
        seg_path = os.path.join(tmpdir, f"part_{i:04d}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-to", str(end),
            "-i", input_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            seg_path,
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            print(f"  경고: 구간 {i+1} 추출 실패", file=sys.stderr)
            print(result.stderr.decode(), file=sys.stderr)
            continue
        segment_files.append(seg_path)

    if not segment_files:
        print("오류: 추출된 구간이 없습니다.", file=sys.stderr)
        sys.exit(1)

    print("  합치기 중...", flush=True)
    list_path = os.path.join(tmpdir, "concat_list.txt")
    with open(list_path, "w") as f:
        for seg_path in segment_files:
            f.write(f"file '{seg_path}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print("오류: concat 실패", file=sys.stderr)
        print(result.stderr.decode(), file=sys.stderr)
        sys.exit(1)


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MP4 무음 구간 자동 제거")
    parser.add_argument("input", help="입력 MP4 경로")
    parser.add_argument("-o", "--output", help="출력 MP4 경로 (기본: {stem}_1_무음제거.mp4)")
    parser.add_argument("--srt", help="SRT 자막 출력 경로 (기본: {stem}_1_자막.srt)")
    parser.add_argument("--preview", action="store_true", help="구간 정보만 출력, 실제 처리 안 함")
    parser.add_argument("--tight", action="store_true", help="단어 단위로 빡빡하게 컷")
    parser.add_argument("--silence-thresh", type=float, default=-35, metavar="DB", help="무음 판단 데시벨 (기본: -35)")
    parser.add_argument("--min-silence", type=float, default=0.4, metavar="SEC", help="최소 무음 길이 초 (기본: 0.4)")
    parser.add_argument("--padding", type=float, default=0.5, metavar="SEC", help="음성 구간 앞뒤 여백 초 (기본: 0.5)")
    parser.add_argument("--pad-start", type=float, default=None, metavar="SEC", help="음성 시작 전 여백 초 (기본: --padding)")
    parser.add_argument("--pad-end", type=float, default=None, metavar="SEC", help="음성 끝 후 여백 초 (기본: --padding)")
    parser.add_argument("--gap", type=float, default=0.7, metavar="SEC", help="단어 간 간격 임계값 --tight용 (기본: 0.7)")
    parser.add_argument("--no-refine", action="store_true", help="Silero VAD 경계 미세조정 비활성화 (기본: 활성)")
    parser.add_argument("--filter-fillers", action="store_true", help="필러 단어(음, 어, 그 등) 자막 제외 (기본: 비활성)")
    parser.add_argument("--keep-temp", action="store_true", help="임시 파일 보존")
    parser.add_argument("--outdir", help="출력 디렉토리 (지정 시 짧은 이름 사용)")
    args = parser.parse_args()
    total_steps = 3 if args.preview else 4
    print("═══ 파이프라인 ①/⑤ 무음제거 ═══")

    input_path = args.input
    if not os.path.exists(input_path):
        print(f"오류: 파일을 찾을 수 없음: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        output_path = args.output or str(Path(args.outdir) / "1_무음제거.mp4")
        srt_path = args.srt or str(Path(args.outdir) / "1_자막.srt")
    else:
        stem = Path(input_path).stem
        out_dir = Path(input_path).parent / stem
        os.makedirs(out_dir, exist_ok=True)
        output_path = args.output or str(out_dir / "1_무음제거.mp4")
        srt_path = args.srt or str(out_dir / "1_자막.srt")

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    print(f"\n[1/{total_steps}] 무음 구간 탐지 중...")
    total_duration = get_duration(input_path)
    silences = detect_silence(input_path, thresh=args.silence_thresh, min_duration=args.min_silence)
    # Whisper용 러프 컷: 고정 0.5초 패딩으로 넉넉하게
    speech_rough = invert_to_speech(silences, total_duration, pad_start=0.5, pad_end=0.5)

    print(f"  총 재생시간: {total_duration:.1f}초")
    print(f"  무음 구간: {len(silences)}개")
    print(f"  음성 구간 (러프): {len(speech_rough)}개")
    for s, e in speech_rough:
        print(f"    {s:.2f}s ~ {e:.2f}s  ({e - s:.2f}s)")

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    print(f"\n[2/{total_steps}] Whisper 전사 중...")

    def _run(tmpdir: str) -> None:
        words = transcribe_segments(input_path, speech_rough, tmpdir)
        print(f"  단어 수: {len(words)}개")

        # ── Phase 3 ──────────────────────────────────────────────────────────
        print(f"\n[3/{total_steps}] 컷 포인트 산출 중...")
        mode = "tight" if args.tight else "segment"
        segments = compute_cut_points(
            words, mode=mode, padding=args.padding, gap=args.gap,
            pad_start=args.pad_start, pad_end=args.pad_end,
        )

        if not args.no_refine:
            print("  [refine] Silero VAD 경계 미세조정 중...")
            segments = refine_segment_boundaries(input_path, segments)

        total_cut = sum(e - s for s, e in segments)
        print(f"  최종 구간: {len(segments)}개  ({total_cut:.1f}초, 원본의 {total_cut / total_duration * 100:.1f}%)")
        for i, (s, e) in enumerate(segments):
            print(f"    [{i + 1:3d}] {s:.2f}s ~ {e:.2f}s  ({e - s:.2f}s)")

        if args.preview:
            print("\n[--preview] 구간 분석 완료. 실제 처리를 건너뜁니다.")
            return

        srt_words = filter_fillers(words) if args.filter_fillers else words
        generate_srt(srt_words, segments, srt_path)
        print(f"\n[SRT] {srt_path} 생성 완료")

        # ── Phase 4 ──────────────────────────────────────────────────────────
        print(f"\n[4/{total_steps}] 영상 컷 및 합치기 중...")
        extract_and_concat(input_path, segments, output_path, tmpdir)

        print(f"\n완료: {output_path}")
        print(f"자막:  {srt_path}")
        if args.keep_temp:
            print(f"임시:  {tmpdir}")

    if args.keep_temp:
        tmpdir = tempfile.mkdtemp(prefix="silence_cut_")
        _run(tmpdir)
    else:
        with tempfile.TemporaryDirectory(prefix="silence_cut_") as tmpdir:
            _run(tmpdir)


if __name__ == "__main__":
    main()
