# Samsung Electronics DART Financial Agent

DART(OpenDART)에서 삼성전자 정기보고서와 재무제표를 자동 수집하고, 주요 재무수치/재무비율을 계산하여 GitHub Pages 대시보드로 보여주는 프로젝트입니다.

## 구조

- `src/dart_agent.py` : DART 수집 + 정규화 + 재무비율 계산
- `data/raw/` : DART 원본 응답/사업보고서 원문 ZIP
- `data/processed/financials.csv` : 표준화 재무데이터
- `data/processed/ratios.csv` : 계산된 재무비율
- `dashboard/index.html` : GitHub Pages 대시보드
- `.github/workflows/update.yml` : 자동 업데이트
- `config/company.json` : 대상 기업 설정

## 1. DART API 키 등록

OpenDART에서 인증키를 발급합니다.

GitHub 저장소:
`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

- Name: `DART_API_KEY`
- Secret: 발급받은 40자리 인증키

API 키를 코드에 직접 넣지 마세요.

## 2. GitHub Pages

Repository → `Settings` → `Pages`

- Source: `GitHub Actions`

처음에는 Actions의 `Run workflow`를 눌러 수동 실행할 수 있습니다.

## 3. 자동 업데이트

기본 설정은 매일 한국시간 06:20에 실행되도록 되어 있습니다.
GitHub Actions의 schedule은 UTC 기준으로 동작하므로 workflow에서 `Asia/Seoul` timezone을 지정했습니다.

DART에 새 보고서가 추가되면 다음 실행에서:
1. 최신 공시 목록 확인
2. 삼성전자 보고서 접수번호 확인
3. 재무제표 데이터 수집
4. 사업보고서 원문 ZIP 저장
5. 재무비율 재계산
6. 대시보드 JSON 갱신
7. GitHub commit/push
8. GitHub Pages 반영

## 4. 로컬 실행

Python 3.11+ 권장.

```bash
pip install -r requirements.txt
set DART_API_KEY=발급키
python src/dart_agent.py
```

Windows PowerShell:
```powershell
$env:DART_API_KEY="발급키"
python src/dart_agent.py
```

## 5. 대상 기업 변경

`config/company.json`에서 회사명/종목코드만 바꾸면 됩니다.
`corp_code`는 비워두면 DART의 corpCode API에서 자동으로 찾습니다.

## 6. 데이터 기준

연결재무제표(CFS)를 기본 분석 기준으로 사용합니다. 연결/별도 기준을 섞지 않도록 구현했습니다.
ROA/ROE/회전율은 가능한 경우 평균 기초·기말 자산/자본을 사용합니다.

본 프로젝트의 FCF는 가이드에 맞춰 `영업활동현금흐름 - CAPEX`로 계산합니다.
CAPEX는 유형/무형자산 취득 현금흐름의 합계를 추출하려고 시도하며, 공시 계정명이 다른 경우 원본 계정 데이터에서 후보를 탐색합니다.
