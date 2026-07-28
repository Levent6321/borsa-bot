import os
import telebot
import yfinance as yf
import isyatirimhisse
from datetime import datetime
import math
import time

# --- BOT AYARLARI ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ HATA: BOT_TOKEN ortam değişkeni bulunamadı!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- BANKA/FİNANS KURULUŞU LİSTESİ (sadece BIST için) ---
# NOT: TKFEN (Tekfen Holding) buradan çıkarıldı — o bir banka değil,
# inşaat/tarım ağırlıklı bir holding. Yanlışlıkla bankalar listesindeydi
# ve gereksiz yere Graham/DCF hesaplarından dışlanıyordu.
# KTLEV (Katılımevim Tasarruf Finansman) eklendi — BDDK lisanslı bir
# finansman şirketi, bankalarla aynı sebepten (farklı bilanço formatı,
# FD/FAVÖK gibi oranlar anlamsız) burada tutuluyor.
BANKALAR = ["AKBNK", "GARAN", "YKBNK", "ISCTR", "VAKBN", "HALKB", "SKBNK", "KTLEV"]

# --- DCF VARSAYIMLARI ---
DCF_BUYUME_ORANI = 0.15
DCF_ISKONTO_ORANI = 0.30
DCF_TERMINAL_BUYUME = 0.10
DCF_YIL_SAYISI = 5

# --- GORDON (TEMETTÜ İSKONTO MODELİ) VARSAYIMLARI ---
GORDON_BUYUME_ORANI = 0.10
GORDON_ISKONTO_ORANI = 0.30


def cari_oran_yorum(deger):
    """Cari Oran: kısa vadeli borç ödeme gücü. İdeal aralık 1,5-2,5."""
    if deger <= 0:
        return "veri yok"
    if 1.5 <= deger <= 2.5:
        return "İyi"
    elif deger >= 1:
        return "Normal"
    else:
        return "Riskli"


def kaldirac_yorum(yuzde):
    """Kaldıraç Oranı (Toplam Borç/Toplam Varlık %): kaynağa göre ≤%50 istenir."""
    if yuzde <= 0:
        return "veri yok"
    if yuzde <= 50:
        return "İyi"
    elif yuzde <= 65:
        return "Normal"
    else:
        return "Riskli"

# --- SEKTÖR/EMSAL GRUPLARI (gerçek BIST piyasa değeri verisiyle güncellendi) ---
# Her grup, o sektördeki en büyük (en likit) piyasa değerine sahip hisselerden
# seçildi — performans için grup başına ~5-8 hisseyle sınırlı tutuldu.
# NOT: GYO (Gayrimenkul Yatırım Ortaklığı) şirketleri gelirlerini gayrimenkul
# yeniden değerleme kazançlarından da elde ettiği için Graham/Peter Lynch gibi
# kâr-bazlı formüllerde diğer sektörlere göre daha az güvenilir olabilir.
#
# NOT (DEVRE DIŞI BIRAKILDI): Bu grup tanımları hâlâ burada duruyor çünkü
# sektor_ortalama_carpanlar() fonksiyonu kod tabanından silinmedi — sadece
# çağrılmıyor (bkz. hesapla_ve_rapor_ver içindeki "sektor_bilgi = None").
# İleride zaman-bazlı bir cache eklenirse tek satır değiştirilerek geri
# açılabilir.
SEKTOR_GRUPLARI = {
    "Otomotiv": ["FROTO", "TOASO", "DOAS", "OTKAR", "BRISA"],
    "Demir-Çelik": ["EREGL", "ISDMR", "BRSAN", "KRDMD", "KRDMA", "KRDMB"],
    "Holding": ["KCHOL", "SAHOL", "DOHOL", "ALARK", "TKFEN"],
    "Perakende": ["BIMAS", "MGROS", "SOKM"],
    "Gıda-İçecek": ["ULKER", "CCOLA", "AEFES", "TATGD"],
    "Havacılık-Ulaşım": ["THYAO", "PGSUS", "TAVHL", "RYSAS", "CLEBI"],
    "Telekomünikasyon": ["TCELL", "TTKOM", "KRONT"],
    "Enerji-Petrol": ["TUPRS", "AKSEN", "ENJSA", "AHGAZ", "ENERY", "AYGAZ", "ZOREN"],
    "İnşaat Malzemeleri": ["OYAKC", "BSOKE", "CIMSA", "AKCNS", "NUHCM", "BTCIM"],
    "GYO": ["EKGYO", "TRGYO", "ZRGYO", "RALYH", "PEKGY", "RGYAS", "RYGYO", "AKFIS"],
    "Kimya-Petrokimya": ["SASA", "PETKM", "TRALT", "GUBRF", "AKSA", "HEKTS"],
    "Savunma-Teknoloji": ["ASELS", "LOGO", "NETAS"],
    "Sigorta": ["TURSG", "ANSGR", "AGESA"],
    "Beyaz Eşya-Elektronik": ["ARCLK", "VESTL"],
    "Madencilik": ["KOZAL", "KOZAA"],
    "Cam-Kimya": ["SISE"],
    "Sağlık": ["SELEC", "MPARK", "ECILC", "GENIL", "DEVA", "KAYSE"],
}


def hissenin_sektoru(hisse_kodu):
    for sektor, hisseler in SEKTOR_GRUPLARI.items():
        if hisse_kodu in hisseler:
            return sektor, hisseler
    return None, []


def sektor_ortalama_carpanlar(hisse_kodu):
    """
    Aynı sektördeki diğer hisselerin F/K ve PD/DD medyanını hesaplar.
    NOT: Sadece 2-5 hisselik küçük gruplar olduğu için istatistiksel
    olarak "kesin" bir sektör ortalaması değil, kaba bir emsal kıyaslaması.
    Ortalamaya (Genel Ortalama Adil Değer'e) dahil edilmez, sadece bağlam
    sağlamak için deneysel bölümde gösterilir.

    NOT: Bu fonksiyon şu an çağrılmıyor (bkz. hesapla_ve_rapor_ver içindeki
    "sektor_bilgi = None" satırı). Sebep: her çağrıda 4-8 emsal hisseyi
    ayrı ayrı çekmek (isyatirim + yfinance), tek bir /hesapla komutunu
    5+ hissenin verisini çekmeye dönüştürüp ciddi yavaşlığa ve rate-limit
    riskine yol açıyordu. Fonksiyon ileride bir cache mekanizmasıyla
    birlikte tekrar devreye alınabilir diye silinmedi.

    DÜZELTME: Aritmetik ortalama yerine MEDYAN kullanılıyor ve aşırı uçuk
    F/K (>60) ile PD/DD (>10) değerleri filtreleniyor. Sebep: küçük emsal
    gruplarında (özellikle sektörün geneli düşük kârlılık yaşadığında) tek
    bir aşırı yüksek F/K'lı hisse, ortalamayı anlamsız şekilde şişirebiliyor
    (örn. EREGL testinde "Sektör Ort. F/K: 171" gibi gerçekçi olmayan bir
    sonuç çıkmıştı — medyan ve filtre bunu büyük ölçüde engeller).
    """
    sektor, emsaller = hissenin_sektoru(hisse_kodu)
    if sektor is None:
        return None

    FK_UST_SINIR = 60
    PDDD_UST_SINIR = 10

    fk_listesi = []
    pddd_listesi = []
    for emsal in emsaller:
        if emsal == hisse_kodu:
            continue
        try:
            veri = get_bist_data(emsal)
        except Exception:
            veri = None
        if veri is None or "hata" in veri:
            continue
        f_emsal = veri['fiyat']
        hbk_emsal = veri['hbk']
        hbdd_emsal = veri['hbdd']
        if hbk_emsal and hbk_emsal > 0:
            fk_emsal = f_emsal / hbk_emsal
            if 0 < fk_emsal <= FK_UST_SINIR:
                fk_listesi.append(fk_emsal)
        if hbdd_emsal and hbdd_emsal > 0:
            pddd_emsal = f_emsal / hbdd_emsal
            if 0 < pddd_emsal <= PDDD_UST_SINIR:
                pddd_listesi.append(pddd_emsal)

    if not fk_listesi and not pddd_listesi:
        return None

    def medyan(liste):
        s = sorted(liste)
        n = len(s)
        orta = n // 2
        if n % 2 == 0:
            return (s[orta - 1] + s[orta]) / 2
        return s[orta]

    return {
        'sektor': sektor,
        'emsal_sayisi': len(emsaller) - 1,
        'ort_fk': medyan(fk_listesi) if fk_listesi else None,
        'ort_pddd': medyan(pddd_listesi) if pddd_listesi else None,
        'kullanilan_emsal_sayisi': len(fk_listesi),  # filtre sonrası kaç hisse gerçekten kullanıldı
    }


def format_para(deger, para_birimi="TL"):
    """TL için Türk formatı (1.234,56), USD için standart format (1,234.56)."""
    if para_birimi == "TL":
        return f"{deger:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        return f"{deger:,.2f}"


# --- 1a. BIST VERİ ÇEKME (isyatirimhisse ile) ---
def get_bist_data(hisse_kodu):
    if hisse_kodu in BANKALAR:
        return {"hata": (
            f"{hisse_kodu} bir banka hissesi. Bankalar farklı bir mali "
            f"tablo formatı (UFRS) kullandığı için bu bot şu an banka "
            f"analizini desteklemiyor. Yanlış kalem eşleşmesiyle hatalı "
            f"sonuç üretmektense bu özelliği henüz devre dışı bıraktık."
        )}

    ticker = yf.Ticker(hisse_kodu + ".IS")
    guncel_fiyat = ticker.fast_info['lastPrice']
    if guncel_fiyat is None:
        hist = ticker.history(period="1d")
        guncel_fiyat = hist['Close'].iloc[-1]

    try:
        temettu_hisse_basi = ticker.info.get('dividendRate')
    except Exception:
        temettu_hisse_basi = None

    guncel_yil = datetime.now().year
    df = isyatirimhisse.FetchFinancials.fetch_financials(
        hisse_kodu,
        start_year=guncel_yil - 1,
        end_year=guncel_yil,
    )
    if df is None or guncel_fiyat is None:
        return None

    donem_sutunlari = []
    for col in df.columns:
        if col not in ['FINANCIAL_ITEM_CODE', 'FINANCIAL_ITEM_NAME_TR', 'FINANCIAL_ITEM_NAME_EN', 'SYMBOL']:
            if "/" in str(col):
                donem_sutunlari.append(col)

    if donem_sutunlari:
        latest_col = donem_sutunlari[-1]
    else:
        latest_col = df.columns[-2]

    ozsermaye_temp = df[df['FINANCIAL_ITEM_CODE'] == '2O'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '2O'].empty else 0
    donen_varliklar_temp = df[df['FINANCIAL_ITEM_CODE'] == '1A'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1A'].empty else 0
    duran_varliklar_temp = df[df['FINANCIAL_ITEM_CODE'] == '1AK'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1AK'].empty else 0
    kisa_borc_temp = df[df['FINANCIAL_ITEM_CODE'] == '2A'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '2A'].empty else 0
    # --- /debug ile bulunan gerçek kodlar ---
    stoklar_temp = df[df['FINANCIAL_ITEM_CODE'] == '1AF'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1AF'].empty else 0
    ticari_alacaklar_temp = df[df['FINANCIAL_ITEM_CODE'] == '1AC'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1AC'].empty else 0
    toplam_varliklar_gercek_temp = df[df['FINANCIAL_ITEM_CODE'] == '1BL'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1BL'].empty else 0
    uzun_vadeli_borc_temp = df[df['FINANCIAL_ITEM_CODE'] == '2B'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '2B'].empty else 0

    toplam_hisse = ticker.info.get('sharesOutstanding')
    if not toplam_hisse or toplam_hisse <= 0:
        try:
            toplam_hisse = ticker.fast_info.get('shares')
        except Exception:
            toplam_hisse = None
    if not toplam_hisse or toplam_hisse <= 0:
        return {"hata": f"{hisse_kodu} için hisse adedi (sharesOutstanding) bulunamadı."}

    def temizle(deger):
        try:
            return float(str(deger).replace(',', '.').strip())
        except:
            return 0.0

    ozsermaye = temizle(ozsermaye_temp)
    donen_varliklar = temizle(donen_varliklar_temp)
    duran_varliklar = temizle(duran_varliklar_temp)
    kisa_borc = temizle(kisa_borc_temp)
    stoklar = temizle(stoklar_temp)
    ticari_alacaklar = temizle(ticari_alacaklar_temp)
    toplam_varliklar_gercek = temizle(toplam_varliklar_gercek_temp)
    uzun_vadeli_borc = temizle(uzun_vadeli_borc_temp)

    # --- GERÇEK TTM (SON 4 ÇEYREK) HESAPLAMASI ---
    # Formül: TTM = Bu yılki kümülatif + Geçen yılın tamamı(12 ay) - Geçen yılın aynı dönemi
    # Bu, son 4 gerçek çeyreği otomatik olarak toplar, mevsimselliği dikkate alır.
    try:
        yil_str, ay_str = str(latest_col).split("/")
        yil = int(yil_str)
        ay_sayisi = int(ay_str)
    except Exception:
        yil = None
        ay_sayisi = 12

    def item_deger(kod, kolon):
        if kolon is None or kolon not in df.columns:
            return None
        seri = df[df['FINANCIAL_ITEM_CODE'] == kod]
        if seri.empty:
            return None
        try:
            return temizle(seri[kolon].values[0])
        except Exception:
            return None

    def ttm_hesapla(kod):
        guncel = item_deger(kod, latest_col)
        if guncel is None:
            return 0.0, False
        if ay_sayisi == 12 or yil is None:
            return guncel, True  # zaten yıllık veri

        # 1. Deneme: isme göre ara ("2025/12", "2025/3" gibi)
        onceki_tam_yil_kolon = f"{yil - 1}/12"
        ayni_donem_gecen_yil_kolon = f"{yil - 1}/{ay_sayisi}"
        onceki_tam = item_deger(kod, onceki_tam_yil_kolon)
        ayni_donem = item_deger(kod, ayni_donem_gecen_yil_kolon)
        if onceki_tam is not None and ayni_donem is not None:
            return guncel + onceki_tam - ayni_donem, True  # gerçek TTM

        # 2. Deneme: isim eşleşmesi başarısız olursa, POZİSYONA
        # göre ara. donem_sutunlari kronolojik sırayla tüm dönem
        # sütunlarını içeriyor (örn. [...,"2025/3","2025/6","2025/9",
        # "2025/12","2026/3"]). Şirket her yıl 4 çeyrek raporluyorsa:
        # - "bir önceki tam yıl" = latest_col'dan 1 önceki sütun
        # - "geçen yılın aynı dönemi" = latest_col'dan 4 önceki sütun
        try:
            latest_index = donem_sutunlari.index(latest_col)
            onceki_tam_idx = latest_index - 1
            ayni_donem_idx = latest_index - 4
            if onceki_tam_idx >= 0 and ayni_donem_idx >= 0:
                onceki_tam_kolon_poz = donem_sutunlari[onceki_tam_idx]
                ayni_donem_kolon_poz = donem_sutunlari[ayni_donem_idx]
                # Doğrulama: "bir önceki tam yıl" gerçekten ".../12" ile bitiyor mu?
                if str(onceki_tam_kolon_poz).endswith("/12"):
                    onceki_tam_poz = item_deger(kod, onceki_tam_kolon_poz)
                    ayni_donem_poz = item_deger(kod, ayni_donem_kolon_poz)
                    if onceki_tam_poz is not None and ayni_donem_poz is not None:
                        return guncel + onceki_tam_poz - ayni_donem_poz, True
        except (ValueError, IndexError):
            pass

        # 3. Son çare: geçmiş yıl verisi hiçbir şekilde bulunamadı
        # (örn. yeni halka arz), kaba yıllıklandırmaya düş
        carpan = 12 / ay_sayisi if ay_sayisi else 1
        return guncel * carpan, False

    net_kar, net_kar_ttm_gercek = ttm_hesapla('3L')
    favok, favok_ttm_gercek = ttm_hesapla('6A')
    # --- Hasılat ve Satışların Maliyeti de TTM olarak hesaplanıyor ---
    hasilat, hasilat_ttm_gercek = ttm_hesapla('3C')
    satislarin_maliyeti_ham, cogs_ttm_gercek = ttm_hesapla('3CA')
    satislarin_maliyeti = abs(satislarin_maliyeti_ham)  # "(-)" işaretli kalem, mutlak değer alınıyor
    ttm_gercek = net_kar_ttm_gercek and favok_ttm_gercek
    yillik_carpan = 12 / ay_sayisi if ay_sayisi and ay_sayisi < 12 else 1

    # --- Future F/K formülü için gerçek 6 aylık (H1) net kâr ---
    # Doğru formül: Future F/K = PD / (6 aylık net kâr × 2)
    net_kar_6ay = None
    if yil is not None:
        h1_kolon = f"{yil}/6"
        h1_deger = item_deger('3L', h1_kolon)
        if h1_deger is not None and h1_deger > 0:
            net_kar_6ay = h1_deger
        elif ay_sayisi == 6:
            # Zaten 6 aylık dönemdeysek mevcut kümülatif değer budur
            net_kar_6ay = item_deger('3L', latest_col)

    return {
        'fiyat': round(guncel_fiyat, 2),
        'hbdd': ozsermaye / toplam_hisse if toplam_hisse > 0 else 0,
        'hbk': net_kar / toplam_hisse if toplam_hisse > 0 else 0,
        'ozsermaye': ozsermaye,
        'toplam_varliklar': toplam_varliklar_gercek if toplam_varliklar_gercek > 0 else (donen_varliklar + duran_varliklar),
        'donen_varliklar': donen_varliklar,
        'duran_varliklar': duran_varliklar,
        'kisa_borc': kisa_borc,
        'uzun_vadeli_borc': uzun_vadeli_borc,
        'stoklar': stoklar,
        'ticari_alacaklar': ticari_alacaklar,
        'hasilat': hasilat,
        'satislarin_maliyeti': satislarin_maliyeti,
        'net_kar': net_kar,
        'favok': favok,
        'toplam_hisse': toplam_hisse,
        'donem': latest_col,
        'temettu_hisse_basi': temettu_hisse_basi,
        'ay_sayisi': ay_sayisi,
        'yillik_carpan': yillik_carpan,
        'ttm_gercek': ttm_gercek,  # gerçek TTM mi, kaba tahmin mi
        'net_kar_6ay': net_kar_6ay,
        'piyasa': 'BIST',
        'para_birimi': 'TL',
    }


# --- 1b. ABD BORSASI VERİ ÇEKME (sadece yfinance ile) ---
def get_us_data(hisse_kodu):
    """
    ABD hisseleri için yfinance zaten TTM (son 12 ay) EPS ve defter değerini
    hazır verdiğinden BIST'teki gibi kümülatif dönem/yıllıklandırma
    sorununa gerek yok.
    """
    ticker = yf.Ticker(hisse_kodu)
    info = ticker.info

    guncel_fiyat = info.get('currentPrice') or info.get('regularMarketPrice')
    if guncel_fiyat is None:
        hist = ticker.history(period="1d")
        if hist.empty:
            return None
        guncel_fiyat = hist['Close'].iloc[-1]

    eps = info.get('trailingEps')          # zaten TTM
    bvps = info.get('bookValue')           # zaten hisse başına
    toplam_hisse = info.get('sharesOutstanding')
    favok = info.get('ebitda')
    temettu_hisse_basi = info.get('dividendRate')

    if not toplam_hisse or toplam_hisse <= 0:
        return {"hata": f"{hisse_kodu} için hisse adedi bulunamadı."}
    if eps is None and bvps is None:
        return None  # muhtemelen geçerli bir ABD hissesi değil

    net_kar = (eps * toplam_hisse) if eps else 0
    ozsermaye = (bvps * toplam_hisse) if bvps else 0

    # Cari oran / kaldıraç için bilanço verisini dene (opsiyonel, yoksa 0 kalır)
    kisa_borc = 0.0
    toplam_varliklar = 0.0
    try:
        bs = ticker.balance_sheet
        if bs is not None and not bs.empty:
            for row_name in ["Total Current Liabilities", "Current Liabilities"]:
                if row_name in bs.index:
                    kisa_borc = float(bs.loc[row_name].iloc[0])
                    break
            for row_name in ["Total Assets"]:
                if row_name in bs.index:
                    toplam_varliklar = float(bs.loc[row_name].iloc[0])
                    break
    except Exception:
        pass

    return {
        'fiyat': round(guncel_fiyat, 2),
        'hbdd': bvps if bvps else 0,
        'hbk': eps if eps else 0,
        'ozsermaye': ozsermaye,
        'toplam_varliklar': toplam_varliklar,
        'kisa_borc': kisa_borc,
        'net_kar': net_kar,
        'favok': favok if favok else 0,
        'toplam_hisse': toplam_hisse,
        'donem': "TTM (Son 12 Ay)",
        'temettu_hisse_basi': temettu_hisse_basi,
        'ay_sayisi': 12,
        'yillik_carpan': 1,
        'piyasa': 'ABD',
        'para_birimi': 'USD',
    }


# --- 1c. EVRENSEL VERİ ÇEKME: önce BIST, olmazsa ABD dener ---
def get_company_data(hisse_kodu):
    # --- DEĞİŞİKLİK: ABD borsası desteği devre dışı bırakıldı ---
    # Sebep: Her /hesapla komutunda önce BIST denenip başarısız olunca
    # ABD'yi denemek, BIST hisselerinde bile gereksiz gecikmeye (yavaşlığa)
    # yol açıyordu. Odağı BIST'e geri veriyoruz.
    try:
        veri = get_bist_data(hisse_kodu)
        if veri is not None and "hata" not in veri:
            return veri
        bist_hata = veri.get("hata") if veri else "Veri bulunamadı."
    except Exception as e:
        bist_hata = str(e)

    return {"hata": bist_hata}


def basit_dcf_deger(net_kar, toplam_hisse, buyume=DCF_BUYUME_ORANI,
                     iskonto=DCF_ISKONTO_ORANI, terminal_buyume=DCF_TERMINAL_BUYUME,
                     yil=DCF_YIL_SAYISI):
    if not net_kar or net_kar <= 0 or not toplam_hisse or toplam_hisse <= 0:
        return None
    if iskonto <= terminal_buyume:
        return None

    pv_toplam = 0.0
    nakit_akisi = net_kar
    for yil_no in range(1, yil + 1):
        nakit_akisi = nakit_akisi * (1 + buyume)
        pv_toplam += nakit_akisi / ((1 + iskonto) ** yil_no)

    terminal_deger = nakit_akisi * (1 + terminal_buyume) / (iskonto - terminal_buyume)
    pv_terminal = terminal_deger / ((1 + iskonto) ** yil)

    toplam_deger = pv_toplam + pv_terminal
    return toplam_deger / toplam_hisse


def gordon_deger(temettu_hisse_basi, buyume=GORDON_BUYUME_ORANI, iskonto=GORDON_ISKONTO_ORANI):
    if not temettu_hisse_basi or temettu_hisse_basi <= 0:
        return None
    if iskonto <= buyume:
        return None
    d1 = temettu_hisse_basi * (1 + buyume)
    return d1 / (iskonto - buyume)


# --- 2. HESAPLAMA FONKSİYONU ---
def hesapla_ve_rapor_ver(hisse_kodu):
    veri = get_company_data(hisse_kodu)
    if veri is None or "hata" in veri:
        return f"❌ Veri çekilemedi: {veri.get('hata', 'Bilinmeyen hata')}"

    f = veri['fiyat']
    hbk = veri['hbk']
    hbdd = veri['hbdd']
    ozsermaye = veri['ozsermaye']
    aktif = veri['toplam_varliklar']
    donen_varliklar = veri.get('donen_varliklar', 0)
    duran_varliklar = veri.get('duran_varliklar', 0)
    kisa_borc = veri['kisa_borc']
    uzun_vadeli_borc = veri.get('uzun_vadeli_borc', 0)
    stoklar = veri.get('stoklar', 0)
    ticari_alacaklar = veri.get('ticari_alacaklar', 0)
    hasilat = veri.get('hasilat', 0)
    satislarin_maliyeti = veri.get('satislarin_maliyeti', 0)
    net_kar = veri['net_kar']
    favok = veri['favok']
    toplam_hisse = veri['toplam_hisse']
    donem = veri['donem']
    temettu_hisse_basi = veri.get('temettu_hisse_basi')
    ay_sayisi = veri.get('ay_sayisi', 12)
    yillik_carpan = veri.get('yillik_carpan', 1)
    ttm_gercek = veri.get('ttm_gercek', True)
    net_kar_6ay = veri.get('net_kar_6ay')
    piyasa = veri.get('piyasa', 'BIST')
    para_birimi = veri.get('para_birimi', 'TL')
    sembol = "TL" if para_birimi == "TL" else "$"

    is_banka = hisse_kodu in BANKALAR  # sadece BIST bankaları için anlamlı

    fk = f / hbk if hbk > 0 else 0
    pddd = f / hbdd if hbdd > 0 else 0
    roe = net_kar / ozsermaye if ozsermaye > 0 else 0
    cari_oran = aktif / kisa_borc if kisa_borc > 0 else 0

    # --- Kaldıraç Oranı kaynağa göre doğru hesaplanıyor:
    # (Kısa + Uzun Vadeli Toplam Borç) / Toplam Varlıklar
    toplam_borc = kisa_borc + uzun_vadeli_borc
    kaldiraç = toplam_borc / aktif if aktif > 0 else 0

    # --- Asit-Test Oranı (Dönen Varlıklar - Stoklar) / Kısa Vadeli Borç ---
    asit_test = (donen_varliklar - stoklar) / kisa_borc if kisa_borc > 0 else 0

    # --- Duran Varlıkların Özsermayeye Oranı ---
    duran_ozkaynak_orani = duran_varliklar / ozsermaye if ozsermaye > 0 else 0

    # --- Alacak Devir Hızı = Hasılat / Ticari Alacaklar ---
    alacak_devir_hizi = hasilat / ticari_alacaklar if ticari_alacaklar > 0 else 0

    # --- Stok Devir Hızı = Satışların Maliyeti / Stoklar ---
    # NOT: Doğrusu "ortalama stok" kullanmaktır (dönem başı+sonu / 2),
    # şu an sadece dönem sonu stok kullanılıyor (yaklaşık değer).
    stok_devir_hizi = satislarin_maliyeti / stoklar if stoklar > 0 else 0

    # --- Net Kâr Marjı = Net Kâr / Hasılat ---
    net_kar_marji = net_kar / hasilat if hasilat > 0 else 0

    hedef_pddd = (f / pddd) * 1.3 if pddd > 0 else 0
    graham = math.sqrt(22.5 * hbk * hbdd) if (hbk > 0 and hbdd > 0 and not is_banka) else 0
    peter = hbk * 15 if hbk > 0 else 0
    peg = fk / 15 if fk > 0 else 0

    if not is_banka and favok > 0:
        hedef_fd_favok = (f / ((f * toplam_hisse + kisa_borc) / favok)) * 10
        net_borc_favok = kisa_borc / favok
    else:
        hedef_fd_favok = 0
        net_borc_favok = 0

    dcf_deger = basit_dcf_deger(net_kar, toplam_hisse) if not is_banka else None
    gordon = gordon_deger(temettu_hisse_basi)

    # --- DEĞİŞİKLİK: sektör/emsal karşılaştırması geçici olarak kapatıldı ---
    # Sebep: Her hissede 4-8 emsal hisseyi ayrı ayrı çekmek (isyatirim + yfinance),
    # tek bir /hesapla komutunu 5+ hissenin verisini çekmeye dönüştürüyordu,
    # bu da ciddi yavaşlığa ve rate-limit riskine yol açıyordu. İleride
    # basit bir zaman-bazlı cache (örn. 1 saatlik) ile geri getirilebilir.
    sektor_bilgi = None
    sektor_hedef_fk = None
    if sektor_bilgi and sektor_bilgi.get('ort_fk') and hbk > 0:
        sektor_hedef_fk = hbk * sektor_bilgi['ort_fk']

    # --- Tarihsel/Future F/K artık keyfi sabitler yerine gerçek sektör
    # (veya BIST100 hazır olduğunda) F/K'sını çarpan olarak kullanıyor.
    # Sektör verisi yoksa bu deneysel değerler hesaplanamaz, "N/A" gösterilir.
    carpan_fk = sektor_bilgi['ort_fk'] if sektor_bilgi and sektor_bilgi.get('ort_fk') else None

    # Tarihsel F/K: gerçek 3 yıllık ortalama F/K verisi şu an çekilmiyor
    # (geçmiş her dönem için ayrı fiyat verisi gerektirir), bu yüzden bu
    # değer güvenilir değildir ve raporda ayrıca işaretlenir.
    hedef_tarihsel_fk = None  # yeterli veri yok, gösterilmeyecek

    # Future F/K: PD / (6 aylık net kâr × 2) — dokümandaki formül birebir
    if net_kar_6ay and net_kar_6ay > 0 and carpan_fk:
        piyasa_degeri = f * toplam_hisse
        future_fk = piyasa_degeri / (net_kar_6ay * 2)
        hedef_future_fk = (f / future_fk) * carpan_fk if future_fk > 0 else None
    else:
        hedef_future_fk = None

    hedef_odennis_sermaye = (net_kar / toplam_hisse) * 10 if toplam_hisse > 0 else 0

    ppd = (net_kar * 7) + (0.5 * ozsermaye)
    hedef_ppd = ppd / toplam_hisse if toplam_hisse > 0 else 0

    # --- sondaki "× Hisse Fiyatı" adımı eklendi (kaynak formülle
    # doğrulandı, önceden eksikti — bu yüzden anlamsız küçük sayı çıkıyordu) ---
    hedef_roe = ((roe * 10) / pddd) * f if pddd > 0 else None

    degerler = [hedef_pddd, graham, peter, hedef_fd_favok]
    gecerli = [d for d in degerler if d > 0]
    ic_sel_deger = sum(gecerli) / len(gecerli) if gecerli else 0

    def tl(deger):
        return format_para(deger, para_birimi)

    rapor = f"{'🇹🇷' if piyasa == 'BIST' else '🇺🇸'} **{hisse_kodu} KAPSAMLI DEĞERLEME RAPORU** {'🇹🇷' if piyasa == 'BIST' else '🇺🇸'}\n"
    rapor += f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    rapor += f"🌍 Piyasa: {piyasa}\n"
    rapor += f"📅 Kullanılan Finansal Dönem: {donem}\n"
    if yillik_carpan != 1:
        if ttm_gercek:
            rapor += (
                f"🔄 *Not: Son 4 gerçek çeyreğin toplamı (TTM) kullanıldı — "
                f"mevsimsellik dikkate alınmış oldu (kaba ×{yillik_carpan:.2f} "
                f"yıllıklandırma değil).*\n"
            )
        else:
            rapor += (
                f"🔄 *Not: {ay_sayisi} aylık kümülatif kâr/FAVÖK, geçmiş yıl "
                f"verisi bulunamadığı için kaba ×{yillik_carpan:.2f} ile "
                f"yıllıklandırıldı (basit doğrusal varsayım, mevsimsellik "
                f"dikkate alınmadı).*\n"
            )
    rapor += f"---\n"
    rapor += f"📈 **Güncel Fiyat:** {tl(f)} {sembol}\n"
    rapor += f"💠 **HBK (Hisse Başı Kâr):** {tl(hbk)} {sembol}\n"
    rapor += f"💠 **HBDD (Hisse Başı Defter Değeri):** {tl(hbdd)} {sembol}\n"
    rapor += f"---\n\n"

    rapor += f"**🔮 BİLANÇO BAZLI ADİL DEĞERLER:**\n"
    rapor += f"🔹 Graham Değeri: {tl(graham)} {sembol}\n"
    rapor += f"🔹 PD/DD Bazlı Hedef: {tl(hedef_pddd)} {sembol}\n"
    rapor += f"🔹 FD/FAVÖK Bazlı Hedef: {tl(hedef_fd_favok)} {sembol}\n"
    rapor += f"---\n\n"

    rapor += f"**📈 BÜYÜME VE KÂRLILIK BAZLI ADİL DEĞERLER:**\n"
    rapor += f"🔸 Peter Lynch Değeri: {tl(peter)} {sembol}\n"
    rapor += f"🔸 PEG Rasyosu: {round(peg, 2)}\n"
    if peg > 0:
        if peg < 1:
            rapor += f"   (1'in altında: Hisse büyümesine göre UCUZ görünüyor.)\n"
        elif peg == 1:
            rapor += f"   (1'e eşit: Hisse adil değerinde.)\n"
        else:
            rapor += f"   (1'in üzerinde: Hisse büyümesine göre PAHALI görünüyor.)\n"
    else:
        rapor += f"   (PEG hesaplanamıyor)\n"
    rapor += f"🔸 ROE (Özsermaye Kârlılığı): %{round(roe * 100, 2)}\n"
    rapor += f"---\n\n"

    if ic_sel_deger > 0:
        rapor += f"⭐ **GENEL ORTALAMA ADİL DEĞER:**\n**{tl(ic_sel_deger)} {sembol}**\n"
        rapor += f"_(Graham, Peter Lynch, PD/DD ve FD/FAVÖK ortalaması)_\n\n"
        fark = ((f - ic_sel_deger) / ic_sel_deger) * 100
        if fark < -5:
            rapor += f"📈 Hisse adil değerine göre %{round(abs(fark), 1)} İSKONTOLU (UCUZ) görünüyor.\n"
        elif fark > 5:
            rapor += f"📉 Hisse adil değerine göre %{round(fark, 1)} PRİMLİ (PAHALI) görünüyor.\n"
        else:
            rapor += f"⚖️ Hisse adil değerine göre tam değerinde görünüyor.\n"

        if f > ic_sel_deger * 5 or f < ic_sel_deger / 5:
            rapor += (
                f"\n⚠️ **UYARI:** Piyasa fiyatı ile hesaplanan adil değer arasında "
                f"olağandışı büyük bir fark var (5 kattan fazla). Sonuçlara "
                f"temkinli yaklaşın, mümkünse ham verileri manuel kontrol edin.\n"
            )

        if pddd and pddd > 8:
            rapor += (
                f"\n⚠️ **UYARI:** Bu şirketin PD/DD oranı çok yüksek (şu an "
                f"{round(pddd, 1)}x) — bu genelde hisse geri alımları "
                f"(buyback) nedeniyle defter değeri çok düşük olan, "
                f"\"varlık hafif\" (asset-light) şirketlerde görülür (Apple "
                f"gibi ABD teknoloji devlerinde yaygın). Bu durumda Graham "
                f"ve PD/DD Bazlı Hedef gibi varlık-temelli formüller "
                f"anlamlı sonuç vermez, ortalamayı yapay şekilde aşağı "
                f"çeker. Bu tür şirketlerde DCF veya büyüme çarpanlarına "
                f"bakmak daha doğru olur.\n"
            )
    rapor += f"---\n\n"

    rapor += f"**📌 DENEYSEL / BİLGİ AMAÇLI HEDEFLER (Ortalamaya Dahil Değildir):**\n"
    if sektor_bilgi:
        rapor += (
            f"🏭 Sektör: {sektor_bilgi['sektor']} "
            f"({sektor_bilgi['emsal_sayisi']} emsal hisse ile karşılaştırıldı)\n"
        )
        if sektor_bilgi['sektor'] == "GYO":
            rapor += (
                "⚠️ *GYO şirketleri gelirini gayrimenkul yeniden değerleme "
                "kazançlarından da elde eder, bu yüzden Graham/Peter Lynch "
                "gibi kâr-bazlı kıyaslamalar bu sektörde daha az güvenilirdir.*\n"
            )
        if sektor_bilgi.get('ort_fk'):
            rapor += f"🔻 Sektör Medyan F/K: {round(sektor_bilgi['ort_fk'], 2)}"
            if sektor_hedef_fk:
                rapor += f" → Sektöre Göre Hedef: {tl(sektor_hedef_fk)} {sembol}\n"
            else:
                rapor += "\n"
        if sektor_bilgi.get('ort_pddd'):
            rapor += f"🔻 Sektör Medyan PD/DD: {round(sektor_bilgi['ort_pddd'], 2)} (bu hissenin PD/DD'si: {round(pddd, 2) if pddd else 'N/A'})\n"
    if dcf_deger is not None:
        rapor += (
            f"🔻 DCF Değeri (Basitleştirilmiş): {tl(dcf_deger)} {sembol} "
            f"_(FCF yerine net kâr kullanıldı; büyüme=%{int(DCF_BUYUME_ORANI*100)}, "
            f"iskonto=%{int(DCF_ISKONTO_ORANI*100)}, terminal=%{int(DCF_TERMINAL_BUYUME*100)}, "
            f"{DCF_YIL_SAYISI} yıl)_\n"
        )
    elif is_banka:
        rapor += f"🔻 DCF Değeri: Bankalar için uygun değil\n"
    if gordon is not None:
        rapor += (
            f"🔻 Gordon Değeri (Temettü İskonto Modeli): {tl(gordon)} {sembol} "
            f"_(temettü={tl(temettu_hisse_basi)} {sembol}, büyüme=%{int(GORDON_BUYUME_ORANI*100)}, "
            f"iskonto=%{int(GORDON_ISKONTO_ORANI*100)})_\n"
        )
    else:
        rapor += f"🔻 Gordon Değeri: Temettü verisi yok veya hesaplanamadı\n"
    rapor += f"🔻 Tarihsel F/K Bazlı Hedef: N/A _(gerçek 3 yıllık geçmiş F/K verisi henüz çekilmiyor)_\n"
    if hedef_future_fk is not None:
        rapor += (
            f"🔻 Future's F/K Bazlı Hedef: {tl(hedef_future_fk)} {sembol} "
            f"_(6 aylık kâr × 2 ve sektör F/K'sına göre)_\n"
        )
    else:
        rapor += f"🔻 Future's F/K Bazlı Hedef: N/A _(6 aylık veri veya sektör F/K'sı yok)_\n"
    rapor += f"🔻 Ödenmiş Sermaye Bazlı Hedef: {tl(hedef_odennis_sermaye)} {sembol} (HBK x 10)\n"
    rapor += f"🔻 PPD Bazlı Hedef: {tl(hedef_ppd)} {sembol} (Geleneksel ağırlık)\n"
    if hedef_roe is not None:
        rapor += f"🔻 ROE Bazlı Hedef: {tl(hedef_roe)} {sembol} _(× Hisse Fiyatı adımı eklendi, artık TL cinsinden)_\n"
    else:
        rapor += f"🔻 ROE Bazlı Hedef: N/A\n"
    rapor += f"---\n\n"

    rapor += f"**🩺 FİNANSAL SAĞLIK:**\n"
    rapor += f"📊 Cari Oran: {round(cari_oran, 2)} ({cari_oran_yorum(cari_oran)})\n"
    if asit_test > 0:
        rapor += f"📊 Asit-Test Oranı: {round(asit_test, 2)} _(ideal: 0,7-1,3)_\n"
    rapor += f"📊 Kaldıraç Oranı: %{round(kaldiraç * 100, 1)} ({kaldirac_yorum(kaldiraç * 100)})\n"
    if duran_ozkaynak_orani > 0:
        rapor += f"📊 Duran Varlık/Özsermaye: {round(duran_ozkaynak_orani, 2)} _(ideal: ≤1)_\n"
    if not is_banka and favok > 0:
        rapor += f"📊 Net Borç / FAVÖK: {round(net_borc_favok, 2)}\n"
    if alacak_devir_hizi > 0:
        rapor += f"📊 Alacak Devir Hızı: {round(alacak_devir_hizi, 2)} _(ideal: >2)_\n"
    if stok_devir_hizi > 0:
        rapor += f"📊 Stok Devir Hızı: {round(stok_devir_hizi, 2)} _(ideal: >2, ortalama stok yerine dönem sonu kullanıldı)_\n"
    if net_kar_marji != 0:
        rapor += f"📊 Net Kâr Marjı: %{round(net_kar_marji * 100, 2)} _(ideal: >%8, sektöre göre değişir)_\n"
    rapor += f"---\n\n"

    if piyasa == 'ABD':
        rapor += (
            f"ℹ️ *ABD hissesi notu: Cari Oran/Kaldıraç için bilanço verisi "
            f"her zaman tam gelmeyebilir, bu durumda 0 görünür. Ayrıca banka "
            f"türü ABD şirketleri (JPM, BAC vb.) için de BIST'teki gibi "
            f"özel bir format ayrımı henüz yapılmadı, dikkatli yorumlayın.*\n"
        )

    rapor += f"Temel analizdir, Yatırım tavsiyesi değildir. Lütfen Teknik Grafiklere de Bakınız.\n\"Kader ironiye aşıktır. İki 3, üç 2 harften oluşur.\"\n@Levent8263"
    return rapor

# --- 3. TELEGRAM KOMUTU ---
# --- /debug KOMUTU ---
# Bir hissenin isyatirim'den gelen TÜM ham kalem kodlarını ve isimlerini
# gösterir. Amaç: Hasılat, Stoklar, Ticari Alacaklar gibi henüz kod
# numarasını bilmediğimiz kalemleri GERÇEK veriden bulmak — tahmin
# etmek yerine. Çıktıyı görüp doğru kodları koda ekleyeceğiz.
@bot.message_handler(commands=['debug'])
def handle_debug(message):
    try:
        komut = message.text.split()
        if len(komut) < 2:
            bot.reply_to(message, "Örnek: /debug EREGL")
            return
        hisse_kodu = komut[1].upper()
        bot.reply_to(message, f"🔍 {hisse_kodu} için ham veri kalemleri çekiliyor...")

        guncel_yil = datetime.now().year
        df = isyatirimhisse.FetchFinancials.fetch_financials(
            hisse_kodu,
            start_year=guncel_yil - 1,
            end_year=guncel_yil,
        )
        if df is None or df.empty:
            bot.reply_to(message, "❌ Veri bulunamadı.")
            return

        # Sütun isimlerini göster (hangi dönemler geldi?)
        kolon_metni = "📋 **Gelen dönem sütunları:**\n" + ", ".join([str(c) for c in df.columns]) + "\n\n"
        bot.reply_to(message, kolon_metni)

        # Her kalem kodu + Türkçe ismini listele (tekrar etmeden)
        if 'FINANCIAL_ITEM_CODE' in df.columns and 'FINANCIAL_ITEM_NAME_TR' in df.columns:
            satirlar = []
            for _, row in df.iterrows():
                kod = row.get('FINANCIAL_ITEM_CODE', '?')
                isim = row.get('FINANCIAL_ITEM_NAME_TR', '?')
                satirlar.append(f"{kod} → {isim}")
            # Telegram mesaj uzunluğu sınırlı, parçalara bölerek gönder
            metin = "\n".join(satirlar)
            for i in range(0, len(metin), 3500):
                bot.reply_to(message, metin[i:i+3500])
        else:
            bot.reply_to(message, "⚠️ FINANCIAL_ITEM_CODE/NAME_TR sütunları bulunamadı, DataFrame yapısı beklenenden farklı.")
    except Exception as e:
        bot.reply_to(message, f"❌ Hata: {str(e)}")


@bot.message_handler(commands=['hesapla'])
def handle_hesapla(message):
    try:
        komut = message.text.split()
        if len(komut) < 2:
            bot.reply_to(message, "Örnek: /hesapla VESBE  (veya ABD için: /hesapla AAPL)")
            return
        bot.reply_to(message, f"🔍 {komut[1].upper()} analiz ediliyor...")
        bot.reply_to(message, hesapla_ve_rapor_ver(komut[1].upper()))
    except Exception as e:
        bot.reply_to(message, f"❌ Hata: {str(e)}")

print("🤖 Borsa Botu başarıyla başlatıldı. Telegram mesajları bekleniyor...")
bot.infinity_polling()
