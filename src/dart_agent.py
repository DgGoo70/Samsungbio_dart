import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/company.json"
RAW = ROOT / "data/raw"
PROC = ROOT / "data/processed"
API = "https://opendart.fss.or.kr/api"

REPORT_NAMES = {
    "11011": "사업보고서",
    "11012": "반기보고서",
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}

def api_get(path, params, binary=False):
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise RuntimeError("DART_API_KEY 환경변수가 없습니다.")
    p = {"crtfc_key": key, **params}
    r = requests.get(f"{API}/{path}", params=p, timeout=60)
    r.raise_for_status()
    return r.content if binary else r.json()

def get_corp_code(stock_code, corp_name):
    # corpCode API returns a ZIP containing CORPCODE.xml
    content = api_get("corpCode.xml", {}, binary=True)
    z = zipfile.ZipFile(io.BytesIO(content))
    xml = z.read("CORPCODE.xml").decode("utf-8")
    rows = re.findall(
        r"<list>\s*<corp_code>(.*?)</corp_code>\s*<corp_name>(.*?)</corp_name>\s*"
        r"<corp_eng_name>(.*?)</corp_eng_name>\s*<stock_code>(.*?)</stock_code>",
        xml,
        re.S,
    )
    for code, name, _, stock in rows:
        if stock.strip() == stock_code:
            return code.strip()
    for code, name, _, stock in rows:
        if name.strip() == corp_name:
            return code.strip()
    raise RuntimeError(f"기업코드를 찾지 못했습니다: {corp_name}/{stock_code}")

def get_config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not cfg.get("corp_code"):
        cfg["corp_code"] = get_corp_code(cfg["stock_code"], cfg["corp_name"])
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg

def parse_num(x):
    if x is None or str(x).strip() in ("", "-"):
        return None
    s = str(x).replace(",", "").replace(" ", "").replace("\u00a0", "")
    try:
        return float(s)
    except Exception:
        return None

def account_candidates(name):
    aliases = {
        "total_assets": ["자산총계", "총자산"],
        "cash": ["현금및현금성자산", "현금및현금성자산"],
        "receivables": ["매출채권", "매출채권및기타채권"],
        "inventory": ["재고자산"],
        "ppe": ["유형자산"],
        "total_liabilities": ["부채총계", "총부채"],
        "borrowings": ["단기차입금", "장기차입금", "유동성장기부채", "사채", "차입금"],
        "equity": ["자본총계", "총자본"],
        "revenue": ["매출액", "수익(매출액)", "영업수익"],
        "gross_profit": ["매출총이익"],
        "sga": ["판매비와관리비", "판매비와일반관리비"],
        "operating_income": ["영업이익", "영업이익(손실)"],
        "pretax_income": ["법인세비용차감전순이익", "세전이익"],
        "net_income": ["당기순이익", "당기순이익(손실)"],
        "controlling_net_income": ["지배기업의 소유주에게 귀속되는 당기순이익", "지배기업소유주지분순이익"],
        "cfo": ["영업활동현금흐름", "영업활동으로 인한 현금흐름"],
        "cfi": ["투자활동현금흐름", "투자활동으로 인한 현금흐름"],
        "cff": ["재무활동현금흐름", "재무활동으로 인한 현금흐름"],
        "interest_expense": ["이자비용", "금융비용"],
        "depreciation": ["감가상각비"],
        "amortization": ["무형자산상각비"],
    }
    return aliases.get(name, [name])

def fetch_financials(cfg, year, report_code):
    data = api_get("fnlttSinglAcntAll.json", {
        "corp_code": cfg["corp_code"],
        "bsns_year": str(year),
        "reprt_code": report_code,
        "fs_div": cfg["analysis_fs_div"],
    })
    if data.get("status") != "000":
        return []
    rows = data.get("list", [])
    return rows

def find_value(rows, aliases, period="thstrm_amount"):
    # Prefer exact-ish account name, then substring match.
    for alias in aliases:
        for row in rows:
            if str(row.get("account_nm", "")).strip() == alias:
                v = parse_num(row.get(period))
                if v is not None:
                    return v, row
    for alias in aliases:
        for row in rows:
            nm = str(row.get("account_nm", "")).strip()
            if alias in nm:
                v = parse_num(row.get(period))
                if v is not None:
                    return v, row
    return None, None

def normalize_report(rows, cfg, year, report_code):
    if not rows:
        return None
    out = {
        "corp_name": cfg["corp_name"],
        "stock_code": cfg["stock_code"],
        "corp_code": cfg["corp_code"],
        "year": int(year),
        "report_code": report_code,
        "report_name": REPORT_NAMES[report_code],
        "fs_div": cfg["analysis_fs_div"],
        "collected_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    for key in [
        "total_assets","cash","receivables","inventory","ppe","total_liabilities",
        "equity","revenue","gross_profit","sga","operating_income","pretax_income",
        "net_income","controlling_net_income","cfo","cfi","cff","interest_expense",
        "depreciation","amortization"
    ]:
        v, row = find_value(rows, account_candidates(key))
        out[key] = v
        if row:
            out[f"{key}_account_nm"] = row.get("account_nm")
            out[f"{key}_sj_div"] = row.get("sj_div")
            out[f"{key}_ord"] = row.get("ord")
    # CAPEX: search account names for acquisition of tangible/intangible assets.
    capex = 0.0
    capex_found = False
    for row in rows:
        nm = str(row.get("account_nm", ""))
        if any(k in nm for k in ["유형자산의 취득", "유형자산 취득", "무형자산의 취득", "무형자산 취득"]):
            v = parse_num(row.get("thstrm_amount"))
            if v is not None:
                capex += abs(v)
                capex_found = True
    out["capex"] = capex if capex_found else None
    return out

def collect_disclosures(cfg, years):
    disclosure_rows = []
    for year in years:
        # Search annual/periodic reports around the expected filing period.
        start = f"{year}0101"
        end = f"{year}1231"
        data = api_get("list.json", {
            "corp_code": cfg["corp_code"],
            "bgn_de": start,
            "end_de": end,
            "pblntf_ty": "A",
            "page_no": 1,
            "page_count": 100,
        })
        if data.get("status") != "000":
            continue
        for r in data.get("list", []):
            report_nm = r.get("report_nm", "")
            if any(x in report_nm for x in ["사업보고서", "반기보고서", "분기보고서"]):
                disclosure_rows.append(r)
                # Save annual original disclosure files.
                if "사업보고서" in report_nm and r.get("rcept_no"):
                    save_original(cfg, year, r["rcept_no"])
    return disclosure_rows

def save_original(cfg, year, rcept_no):
    out_dir = RAW / cfg["stock_code"] / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{rcept_no}_사업보고서.zip"
    if target.exists():
        return
    try:
        content = api_get("document.xml", {"rcept_no": rcept_no}, binary=True)
        target.write_bytes(content)
    except Exception as e:
        print(f"[WARN] 원문 저장 실패 {rcept_no}: {e}")

def build_ratios(df):
    d = df.sort_values(["year", "report_code"]).copy()
    annual = d[d["report_code"] == "11011"].copy()
    if annual.empty:
        annual = d.copy()
    annual = annual.sort_values("year")
    for col in ["total_assets","equity"]:
        annual[f"avg_{col}"] = (annual[col] + annual[col].shift(1)) / 2
    def div(a,b):
        return a / b.replace(0, pd.NA) if hasattr(b, "replace") else (a / b if b else pd.NA)
    annual["gross_margin"] = annual["gross_profit"] / annual["revenue"]
    annual["operating_margin"] = annual["operating_income"] / annual["revenue"]
    annual["net_margin"] = annual["net_income"] / annual["revenue"]
    annual["ebitda"] = annual["operating_income"].fillna(0) + annual["depreciation"].fillna(0) + annual["amortization"].fillna(0)
    annual["ebitda_margin"] = annual["ebitda"] / annual["revenue"]
    annual["roa"] = annual["net_income"] / annual["avg_total_assets"]
    annual["roe"] = annual["net_income"] / annual["avg_equity"]
    annual["current_ratio"] = pd.NA  # API account extraction can be extended with current assets/liabilities
    annual["debt_to_equity"] = annual["total_liabilities"] / annual["equity"]
    annual["equity_ratio"] = annual["equity"] / annual["total_assets"]
    annual["borrowings_to_assets"] = annual["borrowings"] / annual["total_assets"]
    annual["interest_coverage"] = annual["operating_income"] / annual["interest_expense"].abs()
    annual["net_debt"] = annual["borrowings"] - annual["cash"]
    annual["net_debt_to_ebitda"] = annual["net_debt"] / annual["ebitda"]
    annual["asset_turnover"] = annual["revenue"] / annual["avg_total_assets"]
    annual["revenue_growth"] = annual["revenue"].pct_change()
    annual["cfo_to_net_income"] = annual["cfo"] / annual["net_income"].replace(0, pd.NA)
    annual["fcf"] = annual["cfo"] - annual["capex"]
    return annual

def main():
    cfg = get_config()
    years = range(datetime.now().year - cfg["keep_years"] + 1, datetime.now().year + 1)
    all_rows = []
    disclosures = collect_disclosures(cfg, years)

    for year in years:
        for code in cfg["reports"]:
            rows = fetch_financials(cfg, year, code)
            normalized = normalize_report(rows, cfg, year, code)
            if normalized:
                all_rows.append(normalized)

    if not all_rows:
        raise RuntimeError("DART 재무데이터가 없습니다. API 키/기업코드/보고서 제공연도를 확인하세요.")

    PROC.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(all_rows).drop_duplicates(subset=["year","report_code"], keep="last")
    df.to_csv(PROC / "financials.csv", index=False, encoding="utf-8-sig")

    ratios = build_ratios(df)
    ratios.to_csv(PROC / "ratios.csv", index=False, encoding="utf-8-sig")

    latest = ratios.iloc[-1].to_dict()
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "company": cfg,
        "latest": latest,
        "annual": json.loads(ratios.where(pd.notna(ratios), None).to_json(orient="records")),
        "reports": disclosures,
    }
    (Path(ROOT) / "dashboard/data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )
    print(f"완료: {len(df)}개 보고서 데이터 / 최신 연도 {latest.get('year')}")

if __name__ == "__main__":
    main()
