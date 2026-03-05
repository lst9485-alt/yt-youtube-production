#!/usr/bin/env python3
"""3_컷편집.py — SRT 기반 2차 컷편집 + SRT 타임코드 재계산

입력: 1_무음제거 출력 MP4 + 교정된 SRT (리테이크 삭제된 버전)
동작: SRT에 남은 자막 구간만 영상 추출 → concat
출력: {stem}_3_컷편집.mp4  +  {stem}_3_컷편집.srt (타임코드 재계산)
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


# ─── SRT 파서 ─────────────────────────────────────────────────────────────────

def parse_srt(srt_path: str) -> list[dict]:
    """SRT → [{"index": int, "start": float, "end": float, "text": str}, ...]"""
    text = Path(srt_path).read_text(encoding="utf-8").strip()
    # 블록 구분: 빈 줄
    blocks = re.split(r"\n\s*\n", text)
    entries = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        tc_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})",
            lines[1].strip(),
        )
        if not tc_match:
            continue
        entries.append({
            "index": idx,
            "start": _srt_to_sec(tc_match.group(1)),
            "end": _srt_to_sec(tc_match.group(2)),
            "text": "\n".join(lines[2:]).strip(),
        })
    return entries


def _srt_to_sec(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _sec_to_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── 구간 병합 ────────────────────────────────────────────────────────────────

def merge_subtitle_ranges(
    entries: list[dict],
    gap: float,
    padding: float,
    pad_start: float = None,
    pad_end: float = None,
    retake_detect: bool = True,
) -> list[tuple[float, float]]:
    """자막 시간 범위를 gap 기준으로 병합 → 비디오 추출 구간 [(start, end), ...]
    retake_detect: SRT 인덱스 불연속 감지 시 해당 지점에서 병합 차단
    """
    if not entries:
        return []

    _pad_start = pad_start if pad_start is not None else padding
    _pad_end = pad_end if pad_end is not None else padding

    padded = [(max(0.0, e["start"] - _pad_start), e["end"] + _pad_end) for e in entries]

    merged = [list(padded[0])]
    for i, (s, e) in enumerate(padded[1:], start=1):
        # 리테이크 경계 감지: SRT 인덱스가 연속적이지 않으면 병합 차단
        is_retake_boundary = (
            retake_detect
            and entries[i]["index"] != entries[i - 1]["index"] + 1
        )
        if not is_retake_boundary and s - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    return [(s, e) for s, e in merged]


# ─── 영상 추출·합치기 ──────────────────────────────────────────────────────────

def extract_and_concat(
    input_path: str,
    segments: list[tuple[float, float]],
    output_path: str,
    tmpdir: str,
) -> None:
    seg_files = []

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
            print(f"  경고: 구간 {i + 1} 추출 실패", file=sys.stderr)
            print(result.stderr.decode(), file=sys.stderr)
            continue
        seg_files.append(seg_path)

    if not seg_files:
        print("오류: 추출된 구간이 없습니다.", file=sys.stderr)
        sys.exit(1)

    print("  합치기 중...", flush=True)
    list_path = os.path.join(tmpdir, "concat.txt")
    with open(list_path, "w") as f:
        for p in seg_files:
            f.write(f"file '{p}'\n")

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


# ─── SRT 타임코드 재계산 ──────────────────────────────────────────────────────

_SENTENCE_END_RE = re.compile(
    r"(합니다|입니다|겁니다|거든요|거죠|거에요|거예요|바랍니다|있는데요|없습니다|됩니다|봅시다|세요|한다|이다|는데요|나요|까요)\s+"
)


def _split_entry(entry: dict) -> list[dict]:
    text = entry["text"]
    m = _SENTENCE_END_RE.search(text)
    if not m:
        return [entry]
    left = text[:m.end()].strip()
    right = text[m.end():].strip()
    if len(left) < 4 or len(right) < 4:
        return [entry]
    duration = entry["end"] - entry["start"]
    split_t = entry["start"] + duration * len(left) / (len(left) + len(right))
    return _split_entry({"start": entry["start"], "end": split_t, "text": left}) + \
           _split_entry({"start": split_t, "end": entry["end"], "text": right})


def split_at_sentence_boundaries(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        result.extend(_split_entry(entry))
    return result


_SENTENCE_START_RE = re.compile(
    r"^(합니다|입니다|겁니다|거든요|거죠|거에요|거예요|바랍니다|있는데요|없습니다|됩니다|봅시다|세요|한다|이다|는데요|나요|까요)\s+"
)


def merge_sentence_prefix(entries: list[dict]) -> list[dict]:
    """entry가 종결어미로 시작하면 앞 entry에 붙이고 나머지를 새 entry로"""
    if not entries:
        return entries
    result = [dict(entries[0])]
    for entry in entries[1:]:
        m = _SENTENCE_START_RE.match(entry["text"])
        if m:
            prefix = entry["text"][:m.end()].strip()
            rest = entry["text"][m.end():].strip()
            if rest and len(rest) >= 4:
                split_t = entry["start"] + (entry["end"] - entry["start"]) * len(prefix) / (len(prefix) + len(rest))
                result[-1]["text"] += " " + prefix
                result[-1]["end"] = split_t
                result.append({"start": split_t, "end": entry["end"], "text": rest})
            else:
                result[-1]["text"] += " " + entry["text"]
                result[-1]["end"] = entry["end"]
        else:
            result.append(dict(entry))
    return result


def recalc_srt(
    entries: list[dict],
    segments: list[tuple[float, float]],
    output_path: str,
) -> None:
    """새 타임라인 기준으로 SRT 타임코드 재계산 후 저장"""
    offsets: list[tuple[float, float, float]] = []
    cumulative = 0.0
    for s, e in segments:
        offsets.append((s, e, cumulative))
        cumulative += e - s

    def map_time(t: float):
        for seg_start, seg_end, offset in offsets:
            if seg_start - 0.05 <= t <= seg_end + 0.05:
                mapped = offset + max(0.0, t - seg_start)
                return min(mapped, offset + (seg_end - seg_start))
        return None

    mapped_entries = []
    for entry in entries:
        t_start = map_time(entry["start"])
        t_end = map_time(entry["end"])
        if t_start is None or t_end is None:
            continue
        if t_end <= t_start:
            t_end = t_start + 0.1
        mapped_entries.append({"start": t_start, "end": t_end, "text": entry["text"]})

    split_entries = split_at_sentence_boundaries(mapped_entries)
    split_entries = merge_sentence_prefix(split_entries)

    lines = []
    for i, e in enumerate(split_entries, 1):
        lines.append(f"{i}\n{_sec_to_srt(e['start'])} --> {_sec_to_srt(e['end'])}\n{e['text']}\n")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {len(split_entries)}개 자막 항목 저장")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SRT 기반 2차 컷편집 + SRT 타임코드 재계산")
    parser.add_argument("input", help="입력 MP4 경로 (silence_cut 출력)")
    parser.add_argument("srt", help="교정된 SRT 파일 경로 (리테이크 삭제된 버전)")
    parser.add_argument("-o", "--output", help="출력 MP4 경로 (기본: {stem}_3_컷편집.mp4)")
    parser.add_argument("--srt-out", help="출력 SRT 경로 (기본: {stem}_3_컷편집.srt)")
    parser.add_argument("--outdir", help="출력 디렉토리 (지정 시 짧은 이름 사용)")
    parser.add_argument("--gap", type=float, default=2.0, metavar="SEC",
                        help="자막 간 병합 간격 초 (기본: 2.0)")
    parser.add_argument("--padding", type=float, default=0.4, metavar="SEC",
                        help="구간 앞뒤 여백 초 (기본: 0.4)")
    parser.add_argument("--pad-start", type=float, default=None, metavar="SEC",
                        help="구간 시작 전 여백 초 (기본: --padding)")
    parser.add_argument("--pad-end", type=float, default=None, metavar="SEC",
                        help="구간 끝 후 여백 초 (기본: --padding)")
    parser.add_argument("--no-retake-detect", action="store_true",
                        help="SRT 인덱스 불연속 기반 리테이크 경계 감지 비활성화")
    parser.add_argument("--preview", action="store_true", help="구간 정보만 출력, 실제 처리 안 함")
    args = parser.parse_args()
    total_steps = 1 if args.preview else 3
    print("═══ 파이프라인 ③/⑤ 컷편집 ═══")

    if not os.path.exists(args.input):
        print(f"오류: MP4 없음: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.srt):
        print(f"오류: SRT 없음: {args.srt}", file=sys.stderr)
        sys.exit(1)

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        output_path = args.output or str(Path(args.outdir) / "3_컷편집.mp4")
        srt_out = args.srt_out or str(Path(args.outdir) / "3_컷편집.srt")
    else:
        out_dir = Path(args.input).parent
        output_path = args.output or str(out_dir / "3_컷편집.mp4")
        srt_out = args.srt_out or str(out_dir / "3_컷편집.srt")

    print(f"\n[1/{total_steps}] SRT 파싱 중...")
    entries = parse_srt(args.srt)
    if not entries:
        print("오류: SRT에서 자막을 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"  자막 항목: {len(entries)}개")

    segments = merge_subtitle_ranges(
        entries, gap=args.gap, padding=args.padding,
        pad_start=args.pad_start, pad_end=args.pad_end,
        retake_detect=not args.no_retake_detect,
    )
    total = sum(e - s for s, e in segments)
    print(f"  추출 구간: {len(segments)}개  (총 {total:.1f}초)")
    for i, (s, e) in enumerate(segments):
        print(f"    [{i + 1:3d}] {s:.2f}s ~ {e:.2f}s  ({e - s:.2f}s)")

    if args.preview:
        print("\n[--preview] 완료.")
        return

    print(f"\n[2/{total_steps}] 영상 컷 중...")
    with tempfile.TemporaryDirectory(prefix="srt_cut_") as tmpdir:
        extract_and_concat(args.input, segments, output_path, tmpdir)

    print(f"\n[3/{total_steps}] SRT 재계산 중...")
    recalc_srt(entries, segments, srt_out)

    print(f"\n완료: {output_path}")
    print(f"자막:  {srt_out}")


if __name__ == "__main__":
    main()
