"""
마케팅 일별 리포트 대시보드 (SQLite + Streamlit)
실행: streamlit run app.py
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "marketing.db"
ADMIN_USER = "admin"
# SHA-256("admin1234") — 로그인 시 입력 비밀번호의 SHA-256과 비교
ADMIN_PASSWORD_SHA256 = (
    "ac9689e2272427085e35b9d3e3e8bed88cb3434828b43b86fc0596cad4c6e270"
)
MAX_ATTEMPTS = 3
LOCKOUT_SECONDS = 300


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def init_auth_state() -> None:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "login_failed_count" not in st.session_state:
        st.session_state.login_failed_count = 0
    if "lockout_until" not in st.session_state:
        st.session_state.lockout_until = 0.0


@st.cache_data
def load_report_data(db_path_str: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path_str)
    try:
        df = pd.read_sql_query("SELECT * FROM daily_report ORDER BY date, channel, campaign", conn)
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def render_login() -> None:
    st.sidebar.markdown("로그인 후 **사이드바**에서 기간·채널·캠페인 필터를 조정할 수 있습니다.")
    st.title("로그인")
    now = time.time()
    lock_until = float(st.session_state.lockout_until)

    if lock_until > now:
        left = int(lock_until - now)
        m, s = left // 60, left % 60
        st.error(f"로그인 시도가 너무 많습니다. {m}분 {s}초 후에 다시 시도하세요.")
        return

    with st.form("login_form", clear_on_submit=False):
        uid = st.text_input("아이디", autocomplete="username")
        pw = st.text_input("비밀번호", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("로그인")

    if not submitted:
        return

    if uid.strip() != ADMIN_USER or _sha256_hex(pw) != ADMIN_PASSWORD_SHA256:
        st.session_state.login_failed_count += 1
        left_attempts = MAX_ATTEMPTS - st.session_state.login_failed_count
        if st.session_state.login_failed_count >= MAX_ATTEMPTS:
            st.session_state.lockout_until = time.time() + LOCKOUT_SECONDS
            st.session_state.login_failed_count = 0
            st.error(f"비밀번호가 올바르지 않습니다. {MAX_ATTEMPTS}회 실패로 5분간 로그인이 제한됩니다.")
        else:
            st.error(f"아이디 또는 비밀번호가 올바르지 않습니다. (남은 시도: {left_attempts}회)")
        return

    st.session_state.authenticated = True
    st.session_state.login_failed_count = 0
    st.session_state.lockout_until = 0.0
    st.rerun()


def render_dashboard(df: pd.DataFrame) -> None:
    st.title("마케팅 성과 대시보드")
    st.caption("`marketing.db` · `daily_report` 기준 일별·채널·캠페인 지표")

    min_d = df["date"].min().date()
    max_d = df["date"].max().date()
    channels = sorted(df["channel"].unique().tolist())
    campaigns = sorted(df["campaign"].unique().tolist())

    with st.sidebar:
        st.header("필터")
        dr = st.date_input(
            "기간",
            value=(min_d, max_d),
            min_value=min_d,
            max_value=max_d,
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            d_start, d_end = dr
        else:
            d_start = d_end = dr

        sel_ch = st.multiselect("채널", options=channels, default=channels)
        camp_options = sorted(
            df[df["channel"].isin(sel_ch)]["campaign"].unique().tolist()
        ) if sel_ch else campaigns
        sel_camp = st.multiselect("캠페인", options=camp_options, default=camp_options)

        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.login_failed_count = 0
            st.rerun()

    mask = (
        (df["date"].dt.date >= d_start)
        & (df["date"].dt.date <= d_end)
        & (df["channel"].isin(sel_ch))
        & (df["campaign"].isin(sel_camp))
    )
    f = df.loc[mask].copy()

    if f.empty:
        st.warning("선택한 필터에 해당하는 데이터가 없습니다.")
        return

    total_cost = int(f["cost"].sum())
    total_rev = int(f["revenue"].sum())
    total_conv = int(f["conversions"].sum())
    roas = total_rev / total_cost if total_cost else 0.0

    total_clicks = int(f["clicks"].sum())
    total_impressions = int(f["impressions"].sum())
    ctr_pct = (total_clicks / total_impressions * 100) if total_impressions else 0.0
    cpc = (total_cost / total_clicks) if total_clicks else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 비용", f"{total_cost:,}원")
    c2.metric("총 매출", f"{total_rev:,}원")
    c3.metric("ROAS", f"{roas:.2f}")
    c4.metric("총 전환", f"{total_conv:,}")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("총 클릭수", f"{total_clicks:,}")
    d2.metric("총 노출수", f"{total_impressions:,}")
    d3.metric("평균 CTR", f"{ctr_pct:.2f}%")
    d4.metric("평균 CPC", f"{cpc:,.0f}원")

    st.subheader("일별 추이")
    # groupby(시리즈)는 pandas 버전에 따라 첫 열 이름이 date가 아닐 수 있어 KeyError 발생 → 명시 열로 일 단위 집계
    by_day = f.assign(day=f["date"].dt.normalize())
    daily = by_day.groupby("day", as_index=False).agg(
        cost=("cost", "sum"),
        revenue=("revenue", "sum"),
        conversions=("conversions", "sum"),
    )
    chart_df = daily.set_index("day")[["cost", "revenue"]]
    st.line_chart(chart_df)

    st.subheader("채널별 비용·매출")
    by_ch = f.groupby("channel", as_index=False).agg(
        cost=("cost", "sum"),
        revenue=("revenue", "sum"),
    )
    st.bar_chart(by_ch.set_index("channel"))

    st.subheader("캠페인별 전환 (상위 10개)")
    top_c = (
        f.groupby("campaign", as_index=False)["conversions"]
        .sum()
        .sort_values("conversions", ascending=False)
        .head(10)
    )
    st.bar_chart(top_c.set_index("campaign"))

    st.subheader("필터 적용 데이터")
    show = f.copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    st.dataframe(show, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="마케팅 대시보드",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_auth_state()

    if not st.session_state.authenticated:
        render_login()
        return

    if not DB_PATH.is_file():
        st.error(f"DB 파일을 찾을 수 없습니다: {DB_PATH}\n`python setup_data.py`로 먼저 생성하세요.")
        if st.sidebar.button("로그아웃"):
            st.session_state.authenticated = False
            st.rerun()
        return

    df = load_report_data(str(DB_PATH))
    if df.empty:
        st.warning("DB에 데이터가 없습니다. `python setup_data.py`를 실행하세요.")
        if st.sidebar.button("로그아웃"):
            st.session_state.authenticated = False
            st.rerun()
        return

    render_dashboard(df)


if __name__ == "__main__":
    main()
