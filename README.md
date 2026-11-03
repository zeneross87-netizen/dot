# 도트 영상 변환기 (Windows)

영상을 넣으면 도트(픽셀아트) 스타일 영상으로 바꿔줍니다. 오디오는 그대로 유지됩니다.

## 실행 방법
1. Python 3.10+ 설치 (https://www.python.org/downloads/ , "Add python.exe to PATH" 체크)
2. `run.bat` 더블클릭 (처음 한 번만 라이브러리를 자동 설치합니다. ffmpeg 도 자동 포함)
3. 영상 선택 → 미리보기에서 설정 조절 → 변환 시작

## exe 로 만들기 (Python 없는 PC에 배포)
`build_exe.bat` 실행 → `dist\DotVideoConverter.exe` 생성

## 설정 설명
- 도트 해상도: 가로 도트 개수. 작을수록 도트가 크고 거칠어짐 (샘플 영상은 약 192)
- 색 개수 / 팔레트: 자동 추출 또는 게임보이, PICO-8 등 프리셋
- 디더링: 색 사이를 점무늬로 부드럽게 표현 (샘플 영상에 보이는 점 패턴)
- 채도 / 대비: 도트화 전에 색감 보정
- 출력 배율: 0 이면 원본 크기에 가깝게 정수 배율로 확대 (도트가 선명하게 유지됨)

명령줄: `python pixelate_core.py 입력.mp4 출력.mp4 --width 192 --colors 48`
