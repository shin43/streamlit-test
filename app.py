"""
우리팀 광고 대시보드 (SQLite + Streamlit)
실행: streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "marketing.db"
UPLOADED_CSV_TABLE = "uploaded_csv"
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


def _agg_channel_week(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["channel", "cost", "revenue", "conversions", "roas"])
    g = df.groupby("channel", as_index=False).agg(
        cost=("cost", "sum"),
        revenue=("revenue", "sum"),
        conversions=("conversions", "sum"),
    )
    g["roas"] = np.where(g["cost"] > 0, g["revenue"] / g["cost"], 0.0)
    return g


def _weekly_delta_pct(curr: object, prev: object) -> np.ndarray:
    c = np.asarray(curr, dtype=float)
    p = np.asarray(prev, dtype=float)
    out = np.full(c.shape, np.nan, dtype=float)
    mask = p > 0
    out[mask] = (c[mask] - p[mask]) / p[mask] * 100.0
    return out


def _style_delta_pct(v: object) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x > 0:
        return "color: #16a34a; font-weight: 600"
    if x < 0:
        return "color: #dc2626; font-weight: 600"
    return ""


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
    today = date.today()
    st.sidebar.markdown("**마케팅팀 v1.0**")
    st.sidebar.caption(f"{today.year}년 {today.month:02d}월 {today.day:02d}일")
    st.sidebar.divider()
    st.sidebar.markdown(
        "**우리팀 광고 대시보드**에 오신 것을 환영합니다. "
        "로그인하면 일별 성과·주간 비교·CSV 업로드 등을 사용할 수 있습니다."
    )
    st.title("우리팀 광고 대시보드")
    st.markdown("마케팅 캠페인 성과를 한곳에서 확인하세요.")
    st.subheader("로그인")
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


def _read_uploaded_csv_bytes(raw: bytes) -> pd.DataFrame:
    bio = io.BytesIO(raw)
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        bio.seek(0)
        try:
            return pd.read_csv(bio, encoding=enc)
        except UnicodeDecodeError:
            continue
    bio.seek(0)
    return pd.read_csv(bio, encoding="utf-8", errors="replace")


def render_csv_upload() -> None:
    st.subheader("CSV 업로드")
    st.caption(
        f"저장 시 `{UPLOADED_CSV_TABLE}` 테이블을 **덮어씁니다** (`marketing.db`). "
        "UTF-8( BOM )·CP949 등을 순서대로 시도합니다."
    )
    up = st.file_uploader("CSV 파일", type=["csv"])
    if up is None:
        st.info("CSV를 선택하면 미리보기·차트·DB 저장을 사용할 수 있습니다.")
        return
    try:
        udf = _read_uploaded_csv_bytes(up.getvalue())
    except Exception as e:
        st.error(f"CSV를 읽는 중 오류가 났습니다: {e}")
        return
    if udf.empty or len(udf.columns) == 0:
        st.warning("빈 파일이거나 컬럼이 없습니다.")
        return

    st.write(f"**행** {len(udf):,} · **열** {len(udf.columns)}")
    st.dataframe(udf.head(50), use_container_width=True, hide_index=True)

    cols = list(udf.columns)
    cx, cy = st.columns(2)
    with cx:
        x_col = st.selectbox("X축", options=cols, key="csv_uploader_x")
    with cy:
        y_idx = 1 if len(cols) > 1 else 0
        y_col = st.selectbox("Y축", options=cols, index=y_idx, key="csv_uploader_y")

    plot = udf[[x_col, y_col]].copy()
    plot["_y"] = pd.to_numeric(plot[y_col], errors="coerce")
    plot = plot.dropna(subset=["_y"])
    if plot.empty:
        st.warning("Y축을 숫자로 해석할 수 있는 행이 없습니다.")
    else:
        agg = plot.groupby(x_col, sort=False)["_y"].sum()
        st.bar_chart(agg)

    if st.button("DB에 저장", type="primary", key="csv_save_to_db"):
        to_save = udf.copy()
        to_save["_uploaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn = sqlite3.connect(DB_PATH)
        try:
            to_save.to_sql(UPLOADED_CSV_TABLE, conn, if_exists="replace", index=False)
            conn.commit()
        finally:
            conn.close()
        st.success(f"`{UPLOADED_CSV_TABLE}` 테이블에 {len(to_save):,}행을 저장했습니다.")


def render_dashboard(df: pd.DataFrame) -> None:
    st.title("우리팀 광고 대시보드")
    st.caption("`marketing.db` · `daily_report` 기준 일별·채널·캠페인 지표")

    min_d = df["date"].min().date()
    max_d = df["date"].max().date()
    channels = sorted(df["channel"].unique().tolist())
    campaigns = sorted(df["campaign"].unique().tolist())

    with st.sidebar:
        today = date.today()
        st.markdown("**마케팅팀 v1.0**")
        st.caption(f"{today.year}년 {today.month:02d}월 {today.day:02d}일")
        st.divider()
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

    tab_dash, tab_query = st.tabs(["대시보드", "데이터 조회"])
    with tab_dash:
        if f.empty:
            st.warning("선택한 필터에 해당하는 데이터가 없습니다.")
        else:
            render_dashboard_main(f)
    with tab_query:
        _sub_csv, = st.tabs(["CSV 업로드"])
        with _sub_csv:
            render_csv_upload()


def render_dashboard_main(f: pd.DataFrame) -> None:
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

    st.subheader("광고비 vs 매출")
    # groupby(시리즈)는 pandas 버전에 따라 첫 열 이름이 date가 아닐 수 있어 KeyError 발생 → 명시 열로 일 단위 집계
    by_day = f.assign(day=f["date"].dt.normalize())
    daily = by_day.groupby("day", as_index=False).agg(
        cost=("cost", "sum"),
        revenue=("revenue", "sum"),
        conversions=("conversions", "sum"),
    )
    _color_cost = "#4834d4"
    _color_rev = "#f0932b"
    fig_vs = go.Figure()
    fig_vs.add_trace(
        go.Bar(
            x=daily["day"],
            y=daily["cost"],
            name="광고비",
            marker_color=_color_cost,
        )
    )
    fig_vs.add_trace(
        go.Scatter(
            x=daily["day"],
            y=daily["revenue"],
            name="매출",
            mode="lines+markers",
            line=dict(color=_color_rev, width=2),
            marker=dict(color=_color_rev, size=7),
        )
    )
    fig_vs.update_layout(
        xaxis_title="일자",
        yaxis_title="금액 (원)",
        legend=dict(
            x=1,
            y=1,
            xref="paper",
            yref="paper",
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.85)",
        ),
        bargap=0.2,
        hovermode="x unified",
        margin=dict(l=60, r=24, t=48, b=56),
        height=420,
    )
    st.plotly_chart(fig_vs, use_container_width=True)

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

    st.subheader("주간 성과 비교")
    f_w = f.assign(_d=f["date"].dt.normalize())
    anchor = f_w["_d"].max()
    this_start = anchor - pd.Timedelta(days=6)
    prev_end = this_start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=6)
    tw = f_w[(f_w["_d"] >= this_start) & (f_w["_d"] <= anchor)]
    pw = f_w[(f_w["_d"] >= prev_start) & (f_w["_d"] <= prev_end)]
    at = _agg_channel_week(tw).rename(
        columns={
            "cost": "cost_tw",
            "revenue": "rev_tw",
            "conversions": "conv_tw",
            "roas": "roas_tw",
        }
    )
    ap = _agg_channel_week(pw).rename(
        columns={
            "cost": "cost_pw",
            "revenue": "rev_pw",
            "conversions": "conv_pw",
            "roas": "roas_pw",
        }
    )
    cmp = at.merge(ap, on="channel", how="outer")
    num_cols = [
        "cost_tw",
        "rev_tw",
        "conv_tw",
        "roas_tw",
        "cost_pw",
        "rev_pw",
        "conv_pw",
        "roas_pw",
    ]
    for c in num_cols:
        if c in cmp.columns:
            cmp[c] = cmp[c].fillna(0)
    cmp["d_cost"] = _weekly_delta_pct(cmp["cost_tw"].to_numpy(), cmp["cost_pw"].to_numpy())
    cmp["d_rev"] = _weekly_delta_pct(cmp["rev_tw"].to_numpy(), cmp["rev_pw"].to_numpy())
    cmp["d_roas"] = _weekly_delta_pct(cmp["roas_tw"].to_numpy(), cmp["roas_pw"].to_numpy())
    cmp["d_conv"] = _weekly_delta_pct(cmp["conv_tw"].to_numpy(), cmp["conv_pw"].to_numpy())
    cmp = cmp.sort_values("channel", ignore_index=True)
    week_tbl = pd.DataFrame(
        {
            "채널": cmp["channel"],
            "광고비(최근7일)": cmp["cost_tw"],
            "광고비(전주)": cmp["cost_pw"],
            "광고비 증감(%)": cmp["d_cost"],
            "매출(최근7일)": cmp["rev_tw"],
            "매출(전주)": cmp["rev_pw"],
            "매출 증감(%)": cmp["d_rev"],
            "ROAS(최근7일)": cmp["roas_tw"],
            "ROAS(전주)": cmp["roas_pw"],
            "ROAS 증감(%)": cmp["d_roas"],
            "전환(최근7일)": cmp["conv_tw"],
            "전환(전주)": cmp["conv_pw"],
            "전환 증감(%)": cmp["d_conv"],
        }
    )
    rng_caption = (
        f"최근 7일: {this_start.date()} ~ {anchor.date()} · "
        f"전주 7일: {prev_start.date()} ~ {prev_end.date()} "
        "(필터 적용 데이터 기준, 종료일은 필터 내 마지막 일자)"
    )
    st.caption(rng_caption)
    pct_cols = [
        "광고비 증감(%)",
        "매출 증감(%)",
        "ROAS 증감(%)",
        "전환 증감(%)",
    ]
    styled_week = (
        week_tbl.style.map(_style_delta_pct, subset=pct_cols)
        .format(
            {
                "광고비(최근7일)": "{:,.0f}",
                "광고비(전주)": "{:,.0f}",
                "매출(최근7일)": "{:,.0f}",
                "매출(전주)": "{:,.0f}",
                "ROAS(최근7일)": "{:.2f}",
                "ROAS(전주)": "{:.2f}",
                "전환(최근7일)": "{:,.0f}",
                "전환(전주)": "{:,.0f}",
                "광고비 증감(%)": "{:+.1f}%",
                "매출 증감(%)": "{:+.1f}%",
                "ROAS 증감(%)": "{:+.1f}%",
                "전환 증감(%)": "{:+.1f}%",
            },
            na_rep="—",
        )
    )
    st.dataframe(styled_week, use_container_width=True, hide_index=True)

    st.subheader("필터 적용 데이터")
    show = f.copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    st.dataframe(show, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="우리팀 광고 대시보드",
        page_icon="📣",
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
