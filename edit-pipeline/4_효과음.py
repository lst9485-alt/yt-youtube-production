#!/usr/bin/env python3
"""
4_효과음.py — 대본 마커($, $$) → 효과음 트랙(WAV) 자동 생성

사용법:
  .venv/bin/python 4_효과음.py --script 대본.txt --audio 오디오.mp3 --preview
  .venv/bin/python 4_효과음.py --script 대본.txt --audio 오디오.mp3 -o effects.wav
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


# ── 상수 ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
SOUNDS_DIR = SCRIPT_DIR / "sounds"
POP_PATH = SOUNDS_DIR / "pop.wav"
SWOOSH_PATH = SOUNDS_DIR / "swoosh.wav"

MARKER_SWOOSH = "$$"
MARKER_POP = "$"


# ── 데이터 클래스 ────────────────────────────────────────────────────────────

@dataclass
class MarkerInfo:
    """대본에서 파싱된 마커 정보"""
    index: int        # 마커 순번 (1-based)
    marker: str       # "$" or "$$"
    prev_word: str    # 마커 직전 단어
    next_word: str    # 마커 직후 단어


@dataclass
class MatchedMarker:
    """Whisper 매칭 결과"""
    index: int
    marker: str
    timestamp: float  # 효과음 삽입 시간 (초)
    matched_word: str
    match_type: str   # "prev" or "next"


# ── 대본 파서 ────────────────────────────────────────────────────────────────

def parse_script(script_text: str) -> tuple[list[MarkerInfo], list[str]]:
    """
    대본에서 마커를 추출하고, 클린 단어 리스트를 반환.
    $$ 를 먼저 매칭하여 $$ 가 $ 두 개로 잘못 잡히는 것을 방지.

    Returns:
        markers: 마커 정보 리스트
        clean_words: 마커 제거 후 단어 리스트
    """
    # $$를 먼저 플레이스홀더로 치환 후 $ 처리
    placeholder = "\x00SWOOSH\x00"
    text = script_text.replace(MARKER_SWOOSH, placeholder)
    text = text.replace(MARKER_POP, "\x00POP\x00")
    text = text.replace(placeholder, "\x00SWOOSH\x00")

    # 토큰 분리 (마커 + 일반 단어)
    tokens = re.split(r"(\x00SWOOSH\x00|\x00POP\x00)", text)
    tokens = [t for t in tokens if t.strip()]

    markers: list[MarkerInfo] = []
    words: list[str] = []  # 클린 단어 (마커 제외)

    marker_count = 0

    for i, token in enumerate(tokens):
        if token in ("\x00SWOOSH\x00", "\x00POP\x00"):
            marker_str = MARKER_SWOOSH if token == "\x00SWOOSH\x00" else MARKER_POP
            marker_count += 1

            # 직전 단어
            prev_word = ""
            for j in range(i - 1, -1, -1):
                w = tokens[j].strip()
                if w and w not in ("\x00SWOOSH\x00", "\x00POP\x00"):
                    prev_word = _last_word(w)
                    break

            # 직후 단어
            next_word = ""
            for j in range(i + 1, len(tokens)):
                w = tokens[j].strip()
                if w and w not in ("\x00SWOOSH\x00", "\x00POP\x00"):
                    next_word = _first_word(w)
                    break

            markers.append(MarkerInfo(
                index=marker_count,
                marker=marker_str,
                prev_word=prev_word,
                next_word=next_word,
            ))
        else:
            # 일반 텍스트에서 단어 추출
            segment_words = token.split()
            words.extend(segment_words)

    return markers, words


def _last_word(text: str) -> str:
    words = text.split()
    return words[-1] if words else ""


def _first_word(text: str) -> str:
    words = text.split()
    return words[0] if words else ""


# ── Whisper 전사 ─────────────────────────────────────────────────────────────

def transcribe(audio_path: str) -> list[dict]:
    """
    faster-whisper로 단어별 타임스탬프를 추출.

    Returns:
        [{"word": str, "start": float, "end": float}, ...]
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("오류: faster-whisper가 설치되지 않았습니다.")
        print("  .venv/bin/pip install faster-whisper")
        sys.exit(1)

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_path,
        language="ko",
        word_timestamps=True,
    )

    words = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                })

    print(f"  → {len(words)}개 단어 전사 완료 (언어: {info.language})", flush=True)
    return words


# ── 매칭 알고리즘 ─────────────────────────────────────────────────────────────

def _normalize(word: str) -> str:
    """비교용 정규화: 구두점 제거, 소문자화"""
    return re.sub(r"[^\w]", "", word).lower()


def match_markers(
    markers: list[MarkerInfo],
    clean_words: list[str],
    whisper_words: list[dict],
    offset: float = -0.05,
) -> list[MatchedMarker]:
    """
    대본 단어를 Whisper 단어에 순차 매칭하여 마커 타임스탬프를 산출.
    마커 직전 단어의 Whisper end 시간 + offset = 효과음 삽입 시간.
    """
    from difflib import SequenceMatcher

    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()

    # 대본 단어 인덱스 → Whisper 인덱스 매핑
    script_to_whisper: dict[int, int] = {}
    w_cursor = 0

    for s_idx, s_word in enumerate(clean_words):
        best_score = 0.0
        best_w_idx = w_cursor
        # 현재 커서에서 최대 10개 앞뒤로 탐색
        search_start = max(0, w_cursor)
        search_end = min(len(whisper_words), w_cursor + 15)

        for w_idx in range(search_start, search_end):
            score = similarity(s_word, whisper_words[w_idx]["word"])
            if score > best_score:
                best_score = score
                best_w_idx = w_idx

        if best_score >= 0.5:
            script_to_whisper[s_idx] = best_w_idx
            w_cursor = best_w_idx + 1

    # 마커별 타임스탬프 산출
    # 마커가 대본 몇 번째 단어 뒤에 오는지 파악
    # parse_script의 prev_word로 대본 단어 리스트에서 위치를 찾는다
    matched: list[MatchedMarker] = []

    # 마커 순서대로 처리 — 대본 단어 인덱스 커서로 추적
    word_cursor = 0
    s_word_lower = [_normalize(w) for w in clean_words]

    for marker in markers:
        timestamp = None
        match_word = ""
        match_type = ""

        # prev_word 기준: 마커 직전 단어의 end 시간
        if marker.prev_word:
            pn = _normalize(marker.prev_word)
            # word_cursor 이후에서 prev_word 탐색
            for s_idx in range(word_cursor, len(s_word_lower)):
                if s_word_lower[s_idx] == pn or similarity(clean_words[s_idx], marker.prev_word) >= 0.7:
                    if s_idx in script_to_whisper:
                        w_idx = script_to_whisper[s_idx]
                        timestamp = whisper_words[w_idx]["end"] + offset
                        match_word = f"{clean_words[s_idx]}({whisper_words[w_idx]['word']})"
                        match_type = "prev"
                        word_cursor = s_idx + 1
                        break

        # prev_word 실패 시 next_word 기준: 마커 직후 단어의 start 시간
        if timestamp is None and marker.next_word:
            nn = _normalize(marker.next_word)
            for s_idx in range(word_cursor, len(s_word_lower)):
                if s_word_lower[s_idx] == nn or similarity(clean_words[s_idx], marker.next_word) >= 0.7:
                    if s_idx in script_to_whisper:
                        w_idx = script_to_whisper[s_idx]
                        timestamp = max(0.0, whisper_words[w_idx]["start"] + offset)
                        match_word = f"{clean_words[s_idx]}({whisper_words[w_idx]['word']})"
                        match_type = "next"
                        word_cursor = s_idx
                        break

        if timestamp is None:
            print(f"  ⚠ 마커 #{marker.index} ({marker.marker}) 매칭 실패 — 건너뜀")
            continue

        timestamp = max(0.0, timestamp)
        matched.append(MatchedMarker(
            index=marker.index,
            marker=marker.marker,
            timestamp=timestamp,
            matched_word=match_word,
            match_type=match_type,
        ))

    return matched


# ── 오디오 생성 ───────────────────────────────────────────────────────────────

def load_wav_mono(path: Path, target_sr: int = 48000) -> tuple[np.ndarray, int]:
    """WAV 로드 후 모노로 변환, target_sr로 리샘플링"""
    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != target_sr:
        target_len = int(len(data) * target_sr / sr)
        data = np.interp(
            np.linspace(0, len(data) - 1, target_len),
            np.arange(len(data)),
            data,
        ).astype("float32")
    return data, target_sr


def get_duration(audio_path: str) -> float:
    """ffprobe로 미디어 파일 길이(초) 반환 — mp4/mp3/wav 모두 지원"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def generate_track(
    matched: list[MatchedMarker],
    audio_path: str,
    output_path: str,
) -> None:
    """
    원본 오디오와 동일한 길이의 무음 트랙에 효과음을 overlay하여 WAV 저장.
    """
    # 원본 미디어 길이 파악 (mp4/mp3/wav 공통)
    duration_sec = get_duration(audio_path)

    # 효과음 로드 (48000Hz로 통일)
    out_sr = 48000
    pop_data, _ = load_wav_mono(POP_PATH, out_sr)
    swoosh_data, _ = load_wav_mono(SWOOSH_PATH, out_sr)

    # 무음 트랙 생성
    total_samples = int(duration_sec * out_sr)
    track = np.zeros(total_samples, dtype="float32")

    for m in matched:
        sfx = pop_data if m.marker == MARKER_POP else swoosh_data
        start_sample = int(m.timestamp * out_sr)
        end_sample = start_sample + len(sfx)

        if start_sample < 0:
            sfx = sfx[-start_sample:]
            start_sample = 0
            end_sample = start_sample + len(sfx)

        if end_sample > len(track):
            track = np.pad(track, (0, end_sample - len(track)))

        track[start_sample:end_sample] += sfx

    # 클리핑 방지
    peak = np.max(np.abs(track))
    if peak > 1.0:
        track /= peak

    sf.write(output_path, track, out_sr)
    print(f"\n저장 완료: {output_path}")
    print(f"  길이: {len(track)/out_sr:.2f}초  샘플레이트: {out_sr}Hz")


# ── 프리뷰 출력 ───────────────────────────────────────────────────────────────

def print_preview(matched: list[MatchedMarker]) -> None:
    print("\n─── 매칭 결과 ───────────────────────────────")
    for m in matched:
        ts = m.timestamp
        hms = f"{int(ts//3600):02d}:{int((ts%3600)//60):02d}:{ts%60:06.3f}"
        label = "뽁" if m.marker == MARKER_POP else "휙"
        print(f"  #{m.index:<2}  {hms}  {m.marker:<2}  {label}  \"{m.matched_word}\" ({m.match_type})")
    print("─────────────────────────────────────────────")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="대본 마커($, $$) → 효과음 트랙(WAV) 자동 생성"
    )
    parser.add_argument("--script", required=True, help="대본 텍스트 파일 경로")
    parser.add_argument("--audio", required=True, help="캡컷에서 내보낸 오디오 파일 경로")
    parser.add_argument("-o", "--output", default=None, help="출력 WAV 경로 (기본: {YYMMDD}_4_효과음.wav 또는 {stem}_4_효과음.wav)")
    parser.add_argument("--outdir", help="출력 디렉토리 (지정 시 짧은 이름 사용)")
    parser.add_argument("--preview", action="store_true", help="매칭 결과만 출력 (WAV 생성 안 함)")
    parser.add_argument("--offset", type=float, default=-0.05, help="타임스탬프 오프셋(초, 기본: -0.05)")
    args = parser.parse_args()
    total_steps = 3 if args.preview else 4
    print("═══ 파이프라인 ④/⑤ 효과음 ═══")

    # 출력 경로 결정
    script_path = Path(args.script)
    audio_path = Path(args.audio)
    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        output_path = args.output or str(Path(args.outdir) / "4_효과음.wav")
    elif args.output:
        output_path = args.output
    else:
        output_path = str(audio_path.parent / "4_효과음.wav")
    if not script_path.exists():
        print(f"오류: 대본 파일을 찾을 수 없습니다: {script_path}")
        sys.exit(1)
    if not audio_path.exists():
        print(f"오류: 오디오 파일을 찾을 수 없습니다: {audio_path}")
        sys.exit(1)
    if not POP_PATH.exists() or not SWOOSH_PATH.exists():
        print(f"오류: 효과음 파일 없음 — {SOUNDS_DIR}/pop.wav, swoosh.wav 확인")
        sys.exit(1)

    # 대본 파싱
    print(f"\n[1/{total_steps}] 대본 파싱 중...")
    script_text = script_path.read_text(encoding="utf-8")
    markers, clean_words = parse_script(script_text)

    if not markers:
        print("대본에 마커($, $$)가 없습니다.")
        sys.exit(0)

    print(f"  마커 {len(markers)}개 발견: {[m.marker for m in markers]}")

    # Whisper 전사
    print(f"\n[2/{total_steps}] Whisper 전사 중...")
    whisper_words = transcribe(str(audio_path))

    # 매칭
    print(f"\n[3/{total_steps}] 마커 매칭 중...")
    matched = match_markers(markers, clean_words, whisper_words, offset=args.offset)

    # 결과 출력
    print_preview(matched)

    if args.preview:
        return

    if not matched:
        print("매칭된 마커가 없어 WAV를 생성하지 않습니다.")
        sys.exit(1)

    # WAV 생성
    print(f"\n[4/{total_steps}] WAV 생성 중...")
    generate_track(matched, str(audio_path), output_path)


if __name__ == "__main__":
    main()
