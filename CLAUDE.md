# yt-youtube-production

## 개요
유튜브 영상 제작 툴킷 (편집 파이프라인 스크립트 + 제작 체크리스트)

## 기술 스택
- Python
- faster-whisper
- Silero VAD
- ffmpeg
- soundfile
- numpy

## 규칙
- 상위 Workspace/CLAUDE.md 규칙 적용
- edit-pipeline 실행 시 ffmpeg/ffprobe 필요
- venv는 프로젝트 내 `.venv`
- 날짜 폴더(`YYYY-MM-DD/`)가 개별 영상 제작 단위
- `checklist-template.md`를 복사해서 사용
- `tools/codex-smart-paste`는 Mac 전용
