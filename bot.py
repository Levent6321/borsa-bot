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

# --- BANKA LİSTESİ ---
BANKALAR = ["AKBNK", "GARAN", "YKBNK", "ISCTR", "VAKBN", "HALKB", "SKBNK", "TKFEN"]

# --- DCF VARSAYIMLARI ---
DCF_BUYUME_ORANI = 0.15
DCF_ISKONTO_ORANI = 0.30
DCF_TERMINAL_BUYUME = 0.10
DCF_YIL_SAYISI = 5

# --- GORDON (TEMETTÜ İSKONTO MODELİ) VARSAYIMLARI ---
GORDON_BUYUME_ORANI = 0.10
GORDON_ISKONTO_ORANI = 0.30

# --- 1. VERİ ÇEKME VE DÖNÜŞTÜRME FONKSİYONU ---
def get_company_data(hisse_kodu):
    try:
        # --- YENİ: Bankalar farklı mali tablo formatı (UFRS) kullanır.
        # Kodumuzdaki kalem kodları ('2O', '1A', '3L' vb.) sanayi (XI_29)
        # formatı içindir ve bankalarda ya boş gelir ya da YANLIŞ satırla
        # eşleşip hatalı bir sonuç üretebilir. Bu yüzden bankalar için
        # şimdilik net bir "desteklenmiyor" mesajı döndürüyoruz —
        # yanlış rakam üretmektense hiç üretmemek daha güvenli.
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

        # --- DÜZELTME 1: Yıl aralığı artık AÇIKÇA belirtiliyor ---
        # Eskiden: fetch_financials(hisse_kodu)  -> kütüphane varsayılanına
        # kalıyordu ve eski (2024/3 gibi) veri dönebiliyordu.
        guncel_yil = datetime.now().year
        df = isyatirimhisse.FetchFinancials.fetch_financials(
            hisse_kodu,
            start_year=guncel_yil - 1,
            end_year=guncel_yil,
        )
        if df is None or guncel_fiyat is None:
            return None

        # --- DÜZELTME 2: "İlk eşleşen" değil, "SON (en güncel) eşleşen"
        # dönem sütunu seçiliyor. Eskiden ilk bulunan "/" içeren sütunda
        # durup çıkıyordu (break) — bu genelde en eski dönemi seçmek
        # anlamına gelebiliyordu. Şimdi tüm eşleşen sütunları toplayıp
        # en sonuncusunu (en güncel dönemi) alıyoruz.
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

        toplam_hisse = ticker.info.get('sharesOutstanding')
        if not toplam_hisse or toplam_hisse <= 0:
            try:
                toplam_hisse = ticker.fast_info.get('shares')
            except Exception:
                toplam_hisse = None
        if not toplam_hisse or toplam_hisse <= 0:
            return {"hata": f"{hisse_kodu} için hisse adedi (sharesOutstanding) bulunamadı."}

        net_kar_temp = df[df['FINANCIAL_ITEM_CODE'] == '3L'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '3L'].empty else 0
        favok_temp = df[df['FINANCIAL_ITEM_CODE'] == '6A'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '6A'].empty else 0

        def temizle(deger):
            try:
                return float(str(deger).replace(',', '.').strip())
            except:
                return 0.0

        ozsermaye = temizle(ozsermaye_temp)
        donen_varliklar = temizle(donen_varliklar_temp)
        duran_varliklar = temizle(duran_varliklar_temp)
        kisa_borc = temizle(kisa_borc_temp)
        net_kar_kumulatif = temizle(net_kar_temp)
        favok_kumulatif = temizle(favok_temp)

        # --- YENİ: BIST çeyreklik raporları KÜMÜLATİFTİR (yıl başından o
        # aya kadarki toplam). "2026/3" yılın sadece ilk 3 ayını (Q1)
        # kapsar, yıllık değildir. Gelir tablosu kalemlerini (net kâr,
        # FAVÖK) yıllıklandırmak için 12/ay_sayısı ile çarpıyoruz.
        # Bilanço kalemleri (özkaynak, varlıklar, borç) an itibariyle
        # "stok" değerler olduğu için yıllıklandırılmaz, olduğu gibi kalır.
        try:
            _, ay_str = str(latest_col).split("/")
            ay_sayisi = int(ay_str)
        except Exception:
            ay_sayisi = 12  # dönem ayrıştırılamazsa yıllık olduğunu varsay

        if ay_sayisi and 0 < ay_sayisi < 12:
            yillik_carpan = 12 / ay_sayisi
        else:
            yillik_carpan = 1
            ay_sayisi = 12

        net_kar = net_kar_kumulatif * yillik_carpan
        favok = favok_kumulatif * yillik_carpan

        sonuc = {
            'fiyat': round(guncel_fiyat, 2),
            'hbdd': ozsermaye / toplam_hisse if toplam_hisse > 0 else 0,
            'hbk': net_kar / toplam_hisse if toplam_hisse > 0 else 0,
            'ozsermaye': ozsermaye,
            'toplam_varliklar': donen_varliklar + duran_varliklar,
            'kisa_borc': kisa_borc,
            'net_kar': net_kar,
            'favok': favok,
            'toplam_hisse': toplam_hisse,
            'donem': latest_col,
            'temettu_hisse_basi': temettu_hisse_basi,
            'ay_sayisi': ay_sayisi,          # --- YENİ: rapora not düşmek için
            'yillik_carpan': yillik_carpan,  # --- YENİ ---
        }
        return sonuc
    except Exception as e:
        return {"hata": str(e)}


def basit_dcf_deger(net_kar, toplam_hisse, buyume=DCF_BUYUME_ORANI,
                     iskonto=DCF_ISKONTO_ORANI, terminal_buyume=DCF_TERMINAL_BUYUME,
                     yil=DCF_YIL_SAYISI):
    """NOT: FCF yerine net kâr kullanılıyor (proxy), gerçek DCF değil."""
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
    """Gordon Büyüme Modeli: D1 / (r - g). Sadece temettü ödeyen şirketlerde anlamlı."""
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
    kisa_borc = veri['kisa_borc']
    net_kar = veri['net_kar']
    favok = veri['favok']
    toplam_hisse = veri['toplam_hisse']
    donem = veri['donem']
    temettu_hisse_basi = veri.get('temettu_hisse_basi')
    ay_sayisi = veri.get('ay_sayisi', 12)
    yillik_carpan = veri.get('yillik_carpan', 1)

    is_banka = hisse_kodu in BANKALAR

    fk = f / hbk if hbk > 0 else 0
    pddd = f / hbdd if hbdd > 0 else 0
    roe = net_kar / ozsermaye if ozsermaye > 0 else 0
    cari_oran = aktif / kisa_borc if kisa_borc > 0 else 0
    kaldiraç = kisa_borc / aktif if aktif > 0 else 0

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

    tarihsel_fk = 10
    hedef_tarihsel_fk = (f / fk) * tarihsel_fk if fk > 0 and tarihsel_fk > 0 else 0

    tahmini_net_kar = net_kar * 2
    tahmini_hbk = tahmini_net_kar / toplam_hisse if toplam_hisse > 0 else 0
    future_fk = f / tahmini_hbk if tahmini_hbk > 0 else 0
    hedef_future_fk = (f / future_fk) * 7 if future_fk > 0 else 0

    hedef_odennis_sermaye = (net_kar / toplam_hisse) * 10 if toplam_hisse > 0 else 0

    ppd = (net_kar * 7) + (0.5 * ozsermaye)
    hedef_ppd = ppd / toplam_hisse if toplam_hisse > 0 else 0

    hedef_roe = (roe * 10) / pddd if pddd > 0 else 0

    degerler = [hedef_pddd, graham, peter, hedef_fd_favok]
    if dcf_deger is not None and dcf_deger > 0:
        degerler.append(dcf_deger)
    gecerli = [d for d in degerler if d > 0]
    ic_sel_deger = sum(gecerli) / len(gecerli) if gecerli else 0

    def tl_format(deger):
        return f"{deger:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    rapor = f"🇹🇷 **{hisse_kodu} GERÇEK ZAMANLI DEĞERLEME RAPORU** 🇹🇷\n"
    rapor += f"📅 Kullanılan Finansal Dönem: {donem}\n"
    if yillik_carpan != 1:
        rapor += (
            f"🔄 *Not: {ay_sayisi} aylık kümülatif kâr/FAVÖK, ×{yillik_carpan:.2f} "
            f"ile yıllıklandırıldı (basit doğrusal varsayım, mevsimsellik "
            f"dikkate alınmadı).*\n"
        )
    rapor += f"💎 Güncel Piyasa Fiyatı: **{tl_format(f)} TL**\n\n"

    rapor += f"◆ Graham Değeri: {tl_format(graham)} TL\n"
    rapor += f"◆ Peter Lynch Değeri: {tl_format(peter)} TL\n"
    rapor += f"◆ PD/DD Bazlı Hedef: {tl_format(hedef_pddd)} TL\n"
    rapor += f"◆ FD/FAVÖK Bazlı Hedef: {tl_format(hedef_fd_favok)} TL\n"
    if dcf_deger is not None:
        rapor += f"◆ DCF Değeri (Basitleştirilmiş): {tl_format(dcf_deger)} TL\n"
    elif is_banka:
        rapor += f"◆ DCF Değeri: Bankalar için uygun değil\n"

    rapor += f"◆ PEG Rasyosu: {round(peg, 2)}\n"
    if peg > 0:
        if peg < 1:
            rapor += f"   (1'in altında: Hisse büyümesine göre UCUZ görünüyor.)\n"
        elif peg == 1:
            rapor += f"   (1'e eşit: Hisse adil değerinde.)\n"
        else:
            rapor += f"   (1'in üzerinde: Hisse büyümesine göre PAHALI görünüyor.)\n"
    else:
        rapor += f"   (PEG hesaplanamıyor)\n"

    rapor += f"\n———————————————————————\n"

    if ic_sel_deger > 0:
        rapor += f"⭐ **GENEL ORTALAMA ADİL DEĞER:**\n**{tl_format(ic_sel_deger)} TL**\n"
        rapor += f"_(Graham, Peter Lynch, PD/DD, FD/FAVÖK{' ve DCF' if dcf_deger else ''} ortalaması)_\n\n"
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
                f"olağandışı büyük bir fark var (5 kattan fazla). Bu, veri "
                f"kaynağından (dönem karışıklığı, birim hatası vb.) kaynaklanan "
                f"bir hata olabilir. Sonuçlara temkinli yaklaşın, mümkünse "
                f"'{donem}' dönemini ve ham verileri manuel kontrol edin.\n"
            )

    if dcf_deger is not None:
        rapor += (
            f"\nℹ️ *DCF notu: Gerçek serbest nakit akışı verisi yerine net kâr "
            f"kullanıldı (basitleştirilmiş yöntem). Varsayımlar: büyüme "
            f"%{int(DCF_BUYUME_ORANI*100)}, iskonto %{int(DCF_ISKONTO_ORANI*100)}, "
            f"terminal büyüme %{int(DCF_TERMINAL_BUYUME*100)}, {DCF_YIL_SAYISI} yıl.*\n"
        )

    rapor += f"\n📌 **DENEYSEL / BİLGİ AMAÇLI HEDEFLER (Ortalamaya Dahil Değildir):**\n"
    if gordon is not None:
        rapor += (
            f"◆ Gordon Değeri (Temettü İskonto Modeli): {tl_format(gordon)} TL "
            f"_(temettü={tl_format(temettu_hisse_basi)} TL, büyüme=%{int(GORDON_BUYUME_ORANI*100)}, "
            f"iskonto=%{int(GORDON_ISKONTO_ORANI*100)})_\n"
        )
    else:
        rapor += f"◆ Gordon Değeri: Temettü verisi yok veya hesaplanamadı\n"
    rapor += f"◆ Tarihsel F/K Bazlı Hedef: {tl_format(hedef_tarihsel_fk)} TL (Sabit 10 F/K varsayımı)\n"
    rapor += f"◆ Future's F/K Bazlı Hedef: {tl_format(hedef_future_fk)} TL (%100 Büyüme varsayımı)\n"
    rapor += f"◆ Ödenmiş Sermaye Bazlı Hedef: {tl_format(hedef_odennis_sermaye)} TL (HBK x 10)\n"
    rapor += f"◆ PPD Bazlı Hedef: {tl_format(hedef_ppd)} TL (Geleneksel ağırlık)\n"
    rapor += f"◆ ROE Bazlı Referans: {round(hedef_roe, 4)} (Deneysel, TL değil)\n"

    rapor += f"\n———————————————————————\n"
    rapor += f"🩺 **FİNANSAL SAĞLIK:**\n"
    rapor += f"◆ Cari Oran: {round(cari_oran, 2)}\n"
    rapor += f"◆ Kaldıraç Oranı: %{round(kaldiraç * 100, 1)}\n"
    if not is_banka and favok > 0:
        rapor += f"◆ Net Borç / FAVÖK: {round(net_borc_favok, 2)}\n"

    rapor += f"\n———————————————————————\n"
    rapor += f"Temel analizdir, Yatırım tavsiyesi değildir. Lütfen Teknik Grafiklere de Bakınız.\n\"Kader ironiye aşıktır. İki 3, üç 2 harften oluşur.\"\n@Levent8263"
    return rapor

# --- 3. TELEGRAM KOMUTU ---
@bot.message_handler(commands=['hesapla'])
def handle_hesapla(message):
    try:
        komut = message.text.split()
        if len(komut) < 2:
            bot.reply_to(message, "Örnek: /hesapla VESBE")
            return
        bot.reply_to(message, f"🔍 {komut[1].upper()} analiz ediliyor...")
        bot.reply_to(message, hesapla_ve_rapor_ver(komut[1].upper()))
    except Exception as e:
        bot.reply_to(message, f"❌ Hata: {str(e)}")

print("🤖 Borsa Botu başarıyla başlatıldı. Telegram mesajları bekleniyor...")
bot.infinity_polling()
