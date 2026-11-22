# SPC Studio

SPC Studio는 계량형·계수형 관리도와 공정능력을 분석합니다. 일반용과 교육용은 이 저장소의 분석 코드를 함께 사용합니다. 현재 계산 버전은 `2026.11.20.3`입니다. 관리도 선택 가이드, 계수형 분석, 확장 슈하르트 관리도와 Pearson 예제를 포함합니다.

## 일반용 · PC

Python 환경에서 아래 명령을 실행합니다. Windows에서 `start.bat`로도 열 수 있습니다.

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## 교육용 · Streamlit Community Cloud

[교육용 앱 열기](https://spc-studio-education-cws95g5pxnyshjfoymvije.streamlit.app/)

Streamlit 배포는 이 저장소의 `main` 브랜치에서 `education_app.py`를 Python 3.12로 실행합니다. 기존 주소에서 사용일을 지정하는 방법과 교육용 화면 차이는 [교육용 안내](README_EDUCATION.md)를 참고하세요.
