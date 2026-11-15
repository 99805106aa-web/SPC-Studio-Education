# SPC Studio 교육용 공유

수강생은 공개 URL을 Chrome 또는 Edge로 엽니다. Python 설치와 앱 로그인이 필요 없습니다.
한국시간 기준 지정일의 00:00부터 다음 날 00:00 전까지만 사용합니다.

## 배포

1. `python tools/build_education_bundle.py`로 `dist/SPC_교육용_배포.zip`을 만듭니다.
2. ZIP을 풀어 **내용물만** 교육 전용 GitHub 저장소에 업로드합니다. 업무 폴더 전체를 올리지 마세요.
3. [Streamlit Community Cloud](https://share.streamlit.io)에서 새 앱을 만들고 저장소와 브랜치를 선택합니다.
4. Main file path는 **`education_app.py`**, Python은 **3.12**를 선택합니다.
5. Advanced settings의 Secrets에 아래 값을 입력하고 배포합니다.

```toml
SPC_ADMIN_PASSWORD = "강사가-정한-긴-비밀번호"
# 고정 교육일이 필요할 때만 추가합니다. 생략하면 앱에서 날짜를 지정합니다.
# SPC_ALLOWED_DATE = "2026-11-20"
```

교육 진입점은 공유 모드를 항상 활성화합니다. 일반 `app.py`는 기존 PC 모드입니다.
일반 진입점에서 공유 모드를 켜려면 환경변수 또는 Secrets에 `SPC_EDUCATION_MODE = true`를 지정합니다.
Cloud 앱은 공개 접근으로 설정하고 생성된 실제 URL을 수강생에게 전달하세요.

ZIP에는 `.streamlit/config.toml`, 비밀값 없는 `.streamlit/secrets.example.toml`, `.gitignore`가 들어갑니다.
프로젝트 루트의 `STREAMLIT_CONFIG_TEMPLATE.toml`, `SECRETS_TEMPLATE.toml`, `GITIGNORE_TEMPLATE.txt`로 생성합니다.
Linux용 `packages.txt`는 한글 폰트와 Chromium을 설치합니다. PDF 워커는 교육 모드의 Linux에서
시스템 Chromium을 사용하고 PC에서는 기존 Playwright 브라우저를 사용합니다.
[공식 배포 안내](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)와
[의존성 설정](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)을 참고하세요.

## 강사 설정

- 사이드바 `사용일 설정 / 관리자`에서 인증한 뒤 사용할 날짜를 저장합니다.
- 날짜 미설정·잘못된 설정·기간 외에는 분석 화면이 잠깁니다. 관리자 설정은 계속 접근할 수 있습니다.
- `SPC_ADMIN_PASSWORD` 미설정 시 관리자 기능을 잠급니다. 소스에 기본 비밀번호를 넣지 않습니다.
- 사용일은 환경변수 `SPC_ALLOWED_DATE` → Cloud Secrets → 앱 저장값 순서로 적용합니다.
  환경변수나 Secrets에 날짜가 있으면 화면에서 변경할 수 없습니다.
- 앱 저장값은 `education_runtime/access_day.json`에 보관합니다. Cloud 재배포·서버 교체로 이 파일이
  사라지면 다시 잠기므로 재지정하세요. 고정 날짜는 Secrets로 관리합니다.
- 관리자 인증은 각 브라우저 세션에만 적용됩니다. 잠금 버튼, 비밀번호 변경, 서버 재시작 시 재인증합니다.
  세션 내 비밀번호 5회 실패 시 1분 대기합니다.
- AI는 관리자 인증 세션만 사용합니다. 키는 서버 Secrets의 `GEMINI_API_KEY`, `GROQ_API_KEY`,
  `OPENROUTER_API_KEY`로 설정합니다. 수강생에게 키 입력창을 제공하지 않습니다.

## 수강생 실습

1. 공유받은 URL을 열고 사용 가능일을 확인합니다.
2. `교육용 예제 → 예제 불러오기`로 직경·두께 각 150개의 합성 데이터와 규격을 불러옵니다.
3. 부분군 크기 5로 `분석 시작`을 누르고 관리도와 공정능력을 확인합니다.
4. 자체 실습 파일은 Excel/CSV로 업로드합니다. 규격은 `항목, USL, LSL, Target` 열을 사용합니다.
5. 종료 전 HTML·Excel·PDF 보고서를 다운로드합니다. 예제 CSV도 내려받을 수 있습니다.

입력·결과·설정은 각 접속 세션에만 보관하며 새 세션에서 자동 복원되지 않습니다.
기밀·개인정보는 업로드하지 마세요. PC 경로·즐겨찾기·자동 감시·메일 발송은 교육 모드에서 제공하지 않습니다.
열린 화면은 1초마다 날짜를 확인하며 분석·AI·보고서 작업 전후에도 서버가 사용일을 확인합니다.
이미 내려받거나 브라우저에 전달된 파일은 회수하지 않습니다.

## 교육 전 확인

- 앱을 미리 열어 절전 상태에서 깨우고 시크릿 창에서 공개 접속을 확인합니다.
- 지정일 전·당일·종료 후 잠금과 기간 외 관리자 설정을 확인합니다.
- 독립 브라우저 2개에서 서로 다른 데이터로 분석해 결과가 섞이지 않는지 확인합니다.
- 예제·Excel/CSV·한글 차트와 HTML·Excel·PDF 다운로드를 확인합니다.
- 실제 예상 인원으로 동시 접속·분석·다운로드를 시험합니다. 특정 수용 인원을 보장하지 않습니다.
- 실제 URL을 강의자료와 안내문에 넣고 교육 종료 전에 결과 저장을 안내합니다.

## 로컬 확인

```powershell
$env:SPC_ADMIN_PASSWORD = "강사용-비밀번호"
python -m streamlit run education_app.py --server.port 8503
```

PC 모드는 기존 `start.bat` 또는 `python -m streamlit run app.py --server.port 8502`로 실행합니다.
