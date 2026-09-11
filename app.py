# -*- coding: utf-8 -*-
"""
BIST Hisse Senedi Temel Analiz Paneli
=======================================
Kullanım:
    streamlit run app.py

Ne yapar:
- Hisse kodu girip "Ara" butonuna basınca:
  - yfinance üzerinden fiyat + finansal tablo verilerini çeker
  - Kârlılık, değerleme, bilanço sağlığı oranlarını hesaplar
  - KAP'tan (pykap kütüphanesi ile) yönetici pay alım-satım
    bildirimlerini ve kâr payı (temettü) açıklamalarını listeler

NOT: Bu uygulama internet erişimi gerektirir ve KENDİ
bilgisayarınızda çalıştırılmalıdır (Claude'un sohbet ortamının
internet erişimi yoktur, bu yüzden burada canlı test edilemedi).
"""

import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="BIST Hisse Analiz Paneli", layout="wide")

# ------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ------------------------------------------------------------------

def safe_get(series_or_df, key, col=0):
    """DataFrame/Series içinden güvenli değer okuma; yoksa None döner."""
    try:
        if isinstance(series_or_df, pd.DataFrame):
            if key in series_or_df.index:
                return series_or_df.loc[key].iloc[col]
        elif isinstance(series_or_df, pd.Series):
            if key in series_or_df.index:
                return series_or_df[key]
    except Exception:
        return None
    return None


def fmt(value, suffix="", digits=2):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Veri yok"
    try:
        return f"{value:,.{digits}f}{suffix}"
    except Exception:
        return str(value)


def fmt_pct(value, digits=1):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Veri yok"
    try:
        return f"%{value * 100:,.{digits}f}"
    except Exception:
        return str(value)


# ------------------------------------------------------------------
# Veri çekme
# ------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def get_yfinance_data(ticker_code: str):
    symbol = ticker_code.upper().strip()
    if not symbol.endswith(".IS"):
        symbol = symbol + ".IS"

    t = yf.Ticker(symbol)
    info = t.info or {}
    financials = t.financials          # yıllık gelir tablosu
    balance_sheet = t.balance_sheet    # yıllık bilanço
    cashflow = t.cashflow              # yıllık nakit akış

    return {
        "symbol": symbol,
        "info": info,
        "financials": financials,
        "balance_sheet": balance_sheet,
        "cashflow": cashflow,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_kap_disclosures(bist_code: str, days_back: int = 730):
    """
    KAP bildirimlerini pykap kütüphanesi ile çeker.
    pykap: https://github.com/cemsinano/pykap
    Kütüphane KAP'ın halka açık uçlarını sarmalıyor; resmi bir
    Anthropic/KAP entegrasyonu değildir, sürüm değişikliklerinde
    kırılabilir.
    """
    try:
        import pykap
    except ImportError:
        return {"error": "pykap kurulu değil. 'pip install pykap' ile kurun."}

    try:
        comp = pykap.BISTCompany(bist_code.upper().strip())
        fromdate = date.today() - timedelta(days=days_back)
        todate = date.today()

        # Özel Durum Açıklamaları (yönetici işlemleri, temettü vb. burada)
        oda = comp.get_historical_disclosure_list(
            fromdate=fromdate, todate=todate, disclosure_type="ODA"
        )
        return {"disclosures": oda, "error": None}
    except Exception as e:
        return {"error": f"KAP verisi çekilemedi: {e}"}


# ------------------------------------------------------------------
# Arayüz
# ------------------------------------------------------------------

st.title("📊 BIST Hisse Senedi Temel Analiz Paneli")
st.caption(
    "Kârlılık, değerleme, bilanço sağlığı ve KAP bildirimlerini tek ekranda toplar."
)

col_input, col_button = st.columns([4, 1])
with col_input:
    ticker_input = st.text_input(
        "Hisse kodu girin (örn. THYAO, ASELS, BIMAS)",
        value="",
        placeholder="THYAO",
    ).strip()
with col_button:
    st.write("")
    st.write("")
    search_clicked = st.button("🔍 Ara", use_container_width=True)

if search_clicked and ticker_input:
    with st.spinner("Veriler çekiliyor..."):
        data = get_yfinance_data(ticker_input)
        kap_data = get_kap_disclosures(ticker_input)

    info = data["info"]
    fin = data["financials"]
    bs = data["balance_sheet"]
    cf = data["cashflow"]

    if not info or info.get("regularMarketPrice") is None:
        st.error(
            f"'{data['symbol']}' için veri bulunamadı. Kodu kontrol edin "
            f"(BIST hisseleri için sonuna otomatik '.IS' eklenir)."
        )
    else:
        st.subheader(f"{info.get('longName', data['symbol'])} ({data['symbol']})")
        st.write(f"Güncel fiyat: **{fmt(info.get('regularMarketPrice'))} TL**")

        tab1, tab2, tab3, tab4, tab5 = st.tabs(
            [
                "1. Kârlılık",
                "2. Değerleme",
                "3. Bilanço Sağlığı",
                "4. Geleceğe Yönelik Sinyaller",
                "KAP Bildirimleri",
            ]
        )

        # ---------------- 1. Kârlılık ----------------
        with tab1:
            net_margin = info.get("profitMargins")
            revenue = safe_get(fin, "Total Revenue")
            ebitda = info.get("ebitda")
            ebitda_margin = (ebitda / revenue) if (ebitda and revenue) else None
            roe = info.get("returnOnEquity")

            revenue_series = fin.loc["Total Revenue"] if fin is not None and "Total Revenue" in fin.index else None
            yoy_growth = None
            cagr = None
            if revenue_series is not None and len(revenue_series) >= 2:
                yoy_growth = (revenue_series.iloc[0] / revenue_series.iloc[1]) - 1
                if len(revenue_series) >= 4:
                    years = len(revenue_series) - 1
                    cagr = (revenue_series.iloc[0] / revenue_series.iloc[-1]) ** (1 / years) - 1

            eps_trailing = info.get("trailingEps")

            c1, c2, c3 = st.columns(3)
            c1.metric("Net Kâr Marjı", fmt_pct(net_margin))
            c1.metric("FAVÖK Marjı", fmt_pct(ebitda_margin))
            c2.metric("ROE", fmt_pct(roe))
            c2.metric("ROIC", "Manuel hesap gerekir (aşağıya bakın)")
            c3.metric("Gelir Büyümesi (YoY)", fmt_pct(yoy_growth))
            c3.metric("Gelir CAGR", fmt_pct(cagr))

            st.metric("EPS (Hisse Başı Kâr, TTM)", fmt(eps_trailing, " TL"))

            st.info(
                "ROIC hazır gelmiyor: NOPAT / Yatırılan Sermaye formülünü "
                "kendiniz kurmanız gerekir. Aşağıda ham bilanço/gelir "
                "tablosu verileri var, bu formülü onlardan türetebilirsiniz."
            )
            with st.expander("Ham gelir tablosu (yıllık)"):
                st.dataframe(fin)

            st.warning(
                "TÜFE'ye göre reel büyüme burada hesaplanmıyor — TÜİK/TCMB "
                "enflasyon verisini ayrıca çekip nominal büyümeden "
                "düşmeniz gerekiyor."
            )

        # ---------------- 2. Değerleme ----------------
        with tab2:
            trailing_pe = info.get("trailingPE")
            forward_pe = info.get("forwardPE")
            peg = info.get("pegRatio")
            ev_ebitda = info.get("enterpriseToEbitda")
            pb = info.get("priceToBook")

            c1, c2, c3 = st.columns(3)
            c1.metric("F/K (Trailing)", fmt(trailing_pe))
            c1.metric("F/K (Forward)", fmt(forward_pe) if forward_pe else "Genelde BIST için yok")
            c2.metric("PEG", fmt(peg) if peg else "yfinance'ta genelde BIST için yok")
            c2.metric("FD/FAVÖK", fmt(ev_ebitda))
            c3.metric("PD/DD", fmt(pb))

            st.caption(
                "yfinance, BIST hisselerinde forward F/K ve PEG'i çoğu zaman "
                "boş döndürür (analist tahmini gerektiriyor, ücretsiz "
                "kaynaklarda nadiren mevcut)."
            )

        # ---------------- 3. Bilanço Sağlığı ----------------
        with tab3:
            total_debt = safe_get(bs, "Total Debt")
            cash = safe_get(bs, "Cash And Cash Equivalents")
            net_debt = (total_debt - cash) if (total_debt is not None and cash is not None) else None
            net_debt_ebitda = (net_debt / ebitda) if (net_debt is not None and ebitda) else None

            current_assets = safe_get(bs, "Current Assets")
            current_liab = safe_get(bs, "Current Liabilities")
            current_ratio = (
                current_assets / current_liab
                if (current_assets is not None and current_liab is not None and current_liab != 0)
                else None
            )

            op_cf = safe_get(cf, "Operating Cash Flow")
            capex = safe_get(cf, "Capital Expenditure")
            fcf = safe_get(cf, "Free Cash Flow")
            if fcf is None and op_cf is not None and capex is not None:
                fcf = op_cf + capex  # capex genelde negatif gelir

            c1, c2, c3 = st.columns(3)
            c1.metric("Net Borç / FAVÖK", fmt(net_debt_ebitda))
            c2.metric("Cari Oran", fmt(current_ratio))
            c3.metric("Serbest Nakit Akışı (FCF)", fmt(fcf, " TL", 0))

            with st.expander("Ham bilanço (yıllık)"):
                st.dataframe(bs)
            with st.expander("Ham nakit akış tablosu (yıllık)"):
                st.dataframe(cf)

        # ---------------- 4. Geleceğe Yönelik Sinyaller ----------------
        with tab4:
            capex_series = cf.loc["Capital Expenditure"] if cf is not None and "Capital Expenditure" in cf.index else None
            if capex_series is not None:
                st.write("CAPEX trendi (yıllara göre, negatif = harcama):")
                st.bar_chart(capex_series)
            else:
                st.write("CAPEX verisi bulunamadı.")

            payout_ratio = info.get("payoutRatio")
            div_yield = info.get("dividendYield")
            c1, c2 = st.columns(2)
            c1.metric("Temettü Verimi", fmt_pct(div_yield))
            c2.metric("Payout Oranı", fmt_pct(payout_ratio))

            st.info(
                "Sipariş defteri / backlog verisi standart bir alan değil. "
                "KAP'taki ODA metinlerinde geçiyorsa 'KAP Bildirimleri' "
                "sekmesindeki başlıklarda arayabilirsiniz — garantili "
                "kapsama yoktur, çoğunlukla sanayi şirketlerinde bulunur."
            )

        # ---------------- KAP Bildirimleri ----------------
        with tab5:
            st.write("Son 2 yıl için Özel Durum Açıklamaları (ODA)")
            if kap_data.get("error"):
                st.error(kap_data["error"])
                st.caption(
                    "pykap kütüphanesi KAP'ın resmi olmayan uçlarını "
                    "kullanıyor; kurulum veya sürüm sorunuysa "
                    "'pip install pykap' deneyin ya da GitHub sayfasını "
                    "kontrol edin: github.com/cemsinano/pykap"
                )
            else:
                disclosures = kap_data.get("disclosures", [])
                if not disclosures:
                    st.write("Bildirim bulunamadı.")
                else:
                    df = pd.DataFrame(disclosures)
                    st.dataframe(df, use_container_width=True)

                    st.write("**Yönetici Pay Alım-Satım Bildirimleri**")
                    mask = df.apply(
                        lambda r: "Pay Alım Satım" in str(r.to_dict()), axis=1
                    ) if len(df) else pd.Series(dtype=bool)
                    st.dataframe(df[mask] if len(df) else df, use_container_width=True)

                    st.write("**Kâr Payı (Temettü) Açıklamaları**")
                    mask2 = df.apply(
                        lambda r: "Kâr Payı" in str(r.to_dict()) or "Temettü" in str(r.to_dict()),
                        axis=1,
                    ) if len(df) else pd.Series(dtype=bool)
                    st.dataframe(df[mask2] if len(df) else df, use_container_width=True)

elif search_clicked and not ticker_input:
    st.warning("Lütfen bir hisse kodu girin.")
else:
    st.write("👆 Bir hisse kodu girip **Ara**'ya basın.")
