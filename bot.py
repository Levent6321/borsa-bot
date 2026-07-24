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

# --- 1. VERİ ÇEKME VE DÖNÜŞTÜRME FONKSİYONU ---
def get_company_data(hisse_kodu):
    try:
        ticker = yf.Ticker(hisse_kodu + ".IS")
        guncel_fiyat = ticker.fast_info['lastPrice']
        if guncel_fiyat is None:
            hist = ticker.history(period="1d")
            guncel_fiyat = hist['Close'].iloc[-1]

        df = isyatirimhisse.FetchFinancials.fetch_financials(hisse_kodu)
        if df is None or guncel_fiyat is None:
            return None

        latest_col = None
        for col in df.columns:
            if col not in ['FINANCIAL_ITEM_CODE', 'FINANCIAL_ITEM_NAME_TR', 'FINANCIAL_ITEM_NAME_EN', 'SYMBOL']:
                try:
                    if "/" in str(col):
                        latest_col = col
                        break
                except:
                    pass
        if latest_col is None:
            latest_col = df.columns[-2]

        ozsermaye_temp = df[df['FINANCIAL_ITEM_CODE'] == '2O'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '2O'].empty else 0
        donen_varliklar_temp = df[df['FINANCIAL_ITEM_CODE'] == '1A'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1A'].empty else 0
        duran_varliklar_temp = df[df['FINANCIAL_ITEM_CODE'] == '1AK'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '1AK'].empty else 0
        kisa_borc_temp = df[df['FINANCIAL_ITEM_CODE'] == '2A'][latest_col].values[0] if not df[df['FINANCIAL_ITEM_CODE'] == '2A'].empty else 0
        toplam_hisse = 1600000

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
        net_kar = temizle(net_kar_temp)
        favok = temizle(favok_temp)

        sonuc = {
            'fiyat': round(guncel_fiyat, 2),
            'hbdd': ozsermaye / toplam_hisse if toplam_hisse > 0 else 0,
            'hbk': net_kar / toplam_hisse if toplam_hisse > 0 else 0,
            'ozsermaye': ozsermaye,
            'toplam_varliklar': donen_varliklar + duran_varliklar,
            'kisa_borc': kisa_borc,
            'net_kar': net_kar,
            'favok': favok
        }
        return sonuc
    except Exception as e:
        return {"hata": str(e)}

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
        hedef_fd_favok = (f / ( (f*1600000 + kisa_borc) / favok )) * 10
        net_borc_favok = kisa_borc / favok
    else:
        hedef_fd_favok = 0
        net_borc_favok = 0

    degerler = [hedef_pddd, graham, peter, hedef_fd_favok]
    gecerli = [d for d in degerler if d > 0]
    ic_sel_deger = sum(gecerli) / len(gecerli) if gecerli else 0

    def tl_format(deger):
        return f"{deger:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    rapor = f"📊 **{hisse_kodu} KAPSAMLI DEĞERLEME RAPORU**\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n---\n📈 **Güncel Fiyat:** {tl_format(f)} TL\n"
    rapor += f"💠 **HBK (Hisse Başı Kar):** {tl_format(hbk)} TL\n"
    rapor += f"💠 **HBDD (Hisse Başı DD):** {tl_format(hbdd)} TL\n---\n"

    rapor += f"**🔮 BİLANÇO BAZLI ADİL DEĞERLER:**\n"
    rapor += f"🔹 Graham Değeri: {tl_format(graham)} TL\n" if not is_banka else "🔹 Graham Değeri: Bankalar için uygun değil\n"
    rapor += f"🔹 PD/DD Bazlı Hedef: {tl_format(hedef_pddd)} TL\n"
    if not is_banka:
        rapor += f"🔹 FD/FAVÖK Bazlı Hedef: {tl_format(hedef_fd_favok)} TL\n"

    rapor += f"---\n**📈 BÜYÜME VE KÂRLILIK BAZLI ADİL DEĞERLER:**\n"
    if not is_banka:
        rapor += f"🔸 Peter Lynch Değeri: {tl_format(peter)} TL\n"
        rapor += f"🔸 PEG Rasyosu: {round(peg, 2)}\n"
    rapor += f"🔸 ROE (Özsermaye Kârlılığı): %{round(roe * 100, 2)}\n"

    rapor += f"---\n"
    if ic_sel_deger > 0:
        rapor += f"⭐ **ORTALAMA İÇSEL DEĞER: {tl_format(ic_sel_deger)} TL**\n"
        fark = ((f - ic_sel_deger) / ic_sel_deger) * 100
        if fark < -5:
            rapor += f"📈 Piyasa fiyatına göre %{round(abs(fark), 1)} İskontolu\n"
        elif fark > 5:
            rapor += f"📉 Piyasa fiyatına göre %{round(fark, 1)} Primli\n"
        else:
            rapor += f"⚖️ Piyasa fiyatı adil değere yakın\n"

    rapor += f"---\n**🩺 FİNANSAL SAĞLIK:**\n📊 Cari Oran: {round(cari_oran, 2)}\n📊 Kaldıraç: %{round(kaldiraç * 100, 1)}\n"
    if not is_banka and favok > 0:
        rapor += f"📊 Net Borç / FAVÖK: {round(net_borc_favok, 2)}\n"

    rapor += f"---\nTemel analizdir, Yatırım tavsiyesi değildir. Lütfen Teknik Grafiklere de Bakınız.\n\"Kader ironiye aşıktır. İki 3, üç 2 harften oluşur.\"\n@Levent8263"
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
