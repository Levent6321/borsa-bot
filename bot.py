if f > ic_sel_deger * 5 or f < ic_sel_deger / 5:
            rapor += (
                f"\n⚠️ UYARI: Piyasa fiyatı ile hesaplanan adil değer arasında "
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
