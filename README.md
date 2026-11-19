# TikTok-bot: klippsystem för GTA-streams

Boten är byggd efter videon *"GTA clipping system setup"* (@kaylumai). Den bevakar streamers, spelar in när de går live, hittar de roliga ögonblicken och skickar klippen till dig i Discord. Trycker du **Godkänn** publiceras klippet på TikTok och dina andra konton via Post for Me.

## Så fungerar den (steg för steg som i videon)

| Videon säger | Vad boten gör | Fil |
|---|---|---|
| Kick, YouTube och Twitch har API:er som visar när en streamer går live. Gör ett skript per plattform och en lista med streamers. | Var 2:a minut (`POLL_INTERVAL_SEC`) frågas Twitch och Kick via deras officiella API:er. YouTube kollas via kanalens `/live`-sida, vilket inte kostar någon API-kvot. Streamers listas i `streamers.json`. | `platforms.py` |
| Du får en notis så fort streamern går live. | Ett Discord-meddelande: *"🔴 X är live … Börjar spela in."* | `main.py` |
| Streamlink spelar in varje stream i 10-minutersbitar. | `streamlink \| ffmpeg` sparar bitar om 600 s (`CHUNK_SECONDS`). | `recorder.py` |
| AI-sentimentanalys: mer skratt? Ökad chattaktivitet? Vissa emojis? | Varje färdig bit analyseras på tre sätt: ljudtoppar (skratt och skrik), hur aktiv chatten är jämfört med det normala, och skratt- och hype-emotes (KEKW, LUL, 😂, 💀 …). Signalerna vägs ihop till en poäng per sekund. | `analysis.py`, `chat.py` |
| När den hittar något klipps det, och 35–45 s före tas med så att uppbyggnaden syns. | Klippet börjar 40 s före toppen (`CLIP_PRE_SEC`) och slutar 15 s efter. Det görs om till stående 9:16-format för TikTok, Shorts och Reels. | `clipper.py` |
| Klippet skickas till Discord, du tittar och godkänner. | Klippet skickas med förhandsvisning, anledning, chattutdrag och bildtext. Under det finns knapparna **Godkänn och publicera** och **Avvisa**. | `discord_bot.py` |
| Post for Me publicerar på alla dina konton (10 $/mån för 1 000 inlägg). | **Gratisläget (standard):** när du godkänner skickar boten en TikTok-färdig fil (720×1280, under 10 MB) och bildtexten i Discord. Du sparar den i mobilen och laddar upp i TikTok. **Med Post for Me:** lägg in nyckel och konton, så publiceras klippet automatiskt i stället. | `discord_bot.py`, `postforme.py` |

Utöver videon: med en `ANTHROPIC_API_KEY` skriver Claude bildtexten utifrån chatten. Utan nyckel används en gratis mall.

## Kom igång

### 1. Datorn den körs på
Boten måste vara igång dygnet runt eftersom den spelar in live. GitHub Actions fungerar inte för det. Använd en egen dator som står på eller en billig VPS (Hetzner, DigitalOcean m.fl.). Beräkna minst 2 GB RAM och 20 GB disk, och mer om du bevakar många streamers samtidigt.

**Med Docker (enklast):**
```bash
cp .env.example .env                       # fyll i, se steg 2–5
cp streamers.example.json streamers.json   # fyll i dina streamers
docker compose up -d --build
docker compose logs -f
```

**Utan Docker:** installera Python 3.12 och ffmpeg, och kör sedan:
```bash
pip install -r requirements.txt
python -m clipbot check   # testar nycklarna och visar vem som är live
python -m clipbot run
```

### 2. Plattformarnas API:er (gratis)
- **Twitch:** dev.twitch.tv/console/apps → Register Your Application → kopiera Client ID och skapa en Client Secret. Lägg in dem som `TWITCH_CLIENT_ID` och `TWITCH_CLIENT_SECRET`. Twitch-chatten läses anonymt och kräver ingen inloggning.
- **Kick:** kick.com → Settings → Developer → skapa en app. Lägg in `KICK_CLIENT_ID` och `KICK_CLIENT_SECRET`.
- **YouTube:** livekollen behöver ingen nyckel. För att även läsa chatten: Google Cloud Console → aktivera YouTube Data API v3 → skapa en API-nyckel (`YOUTUBE_API_KEY`).

### 3. Discord
1. discord.com/developers/applications → New Application → Bot → **Reset Token**. Lägg in tokenet som `DISCORD_BOT_TOKEN`.
2. OAuth2 → URL Generator: kryssa i `bot` med behörigheterna *Send Messages*, *Attach Files* och *Read Message History*. Öppna länken och lägg till boten på din server.
3. Discord → Inställningar → Avancerat → slå på **Utvecklarläge**. Högerklicka sedan på kanalen → **Kopiera kanal-ID** och lägg in det som `DISCORD_CHANNEL_ID`.

### 4. Publicering
**Gratisläget (standard, inget att ställa in):** tryck **Godkänn – ge mig TikTok-filen** i Discord. Boten svarar med videofilen och bildtexten. Öppna Discord i mobilen, spara videon, öppna TikTok → **+** → **Ladda upp**, välj videon och klistra in bildtexten.

**Automatiskt via Post for Me (kostar):**
1. Skapa ett konto på postforme.dev och koppla ditt nya TikTok-konto, och gärna fler konton.
2. Skapa en API-nyckel och lägg in den som `POSTFORME_API_KEY`.
3. Kör `python -m clipbot accounts`. Kopiera id:n för kontona som klippen ska ut på till `POSTFORME_ACCOUNT_IDS`, kommaseparerade.
4. Boten byter till automatisk publicering så fort båda finns (`PUBLISH_MODE=auto`). Vill du stanna i gratisläget ändå: sätt `PUBLISH_MODE=manual`.

### 5. Testa och slå på
1. `DRY_RUN=true` (standard) gäller bara Post for Me: **Godkänn** publicerar inget automatiskt. Gratisläget påverkas inte, eftersom du ändå laddar upp själv.
2. Testa klippningen på en egen video: `python -m clipbot analyze minvideo.mp4`. Klippen hamnar i `data/clips/`.
3. Låt boten gå under en kväll och titta på vilka klipp den hittar. Justera vid behov `SCORE_THRESHOLD`: höj för färre klipp och sänk för fler.
4. Med Post for Me: när du är nöjd sätter du `DRY_RUN=false` och startar om.

## Kostnader
| Post | Kostnad |
|---|---|
| Twitch, Kick, YouTube API, Discord, Streamlink, ffmpeg | 0 kr |
| Publicering | 0 kr i gratisläget. Post for Me: ca 10 $/mån enligt videon (kolla aktuellt pris) |
| Server/VPS | ca 50–100 kr/mån, eller 0 kr på en egen dator som står på |
| Claude (valfritt) | några ören per klipp |

## Viktigt innan du publicerar
- **Rättigheter:** klippen visar någon annans stream. Många streamers tillåter klipp, men inte alla. Välj streamers som uttryckligen tillåter klippkonton, och skriv deras namn i bildtexten (Claude gör det automatiskt). Spelmusik och musik i bakgrunden kan göra att TikTok stänger av ljudet eller tar bort klippet.
- **TikTok-kontot:** publicera i lagom takt och börja med få klipp om dagen. Många identiska klipp på flera konton samtidigt kan flaggas som spam.
- **Godkännandet är skyddet:** inget publiceras utan att du har tryckt på **Godkänn**. I gratisläget publicerar boten aldrig något själv.

## Testat och inte testat
- **Testat (tester körs på GitHub vid varje ändring):** tolkningen av svaren från Twitch, Kick och YouTube, chattolkningen, poängsättningen, klippfönstret och raderingen av gamla bitar. Det finns också ett helt flöde på en påhittad 3-minuters "stream": ljudtoppen hittas, klippet går över bitgränsen, blir 1080×1920 och har rätt längd. Gratislägets TikTok-fil (720×1280) håller sig under Discords 10 MB även för ett 60 s klipp med mycket rörelse.
- **Inte provat mot riktiga tjänster:** inspelning av en riktig stream, Kick-API:t och Kick-chatten (Kicks chatt-id hämtas från Kicks inofficiella webb-API och kan sluta fungera; sätt då `kick_chatroom_id` i `streamers.json`), Discord-boten med ett riktigt token och Post for Me:s API. Kör `check` och `accounts` först, så ser du direkt om något inte stämmer.

## Filer
```
clipbot/
  main.py         startpunkt: livekoll-loop, sessioner, kommandon
  config.py       alla inställningar (.env)
  platforms.py    Twitch / Kick / YouTube – vem är live?
  chat.py         läser chatten (Twitch IRC, Kick, YouTube)
  recorder.py     Streamlink + ffmpeg i 10-minutersbitar
  analysis.py     sentimentanalys: ljud, chattaktivitet, emotes
  clipper.py      klipper ut ögonblicket, gör 9:16 och förhandsvisning
  session.py      en inspelning per streamer: spela in -> analysera -> klippa
  captions.py     bildtext (Claude eller mall)
  discord_bot.py  notiser och knapparna Godkänn/Avvisa
  postforme.py    publicering via Post for Me
  store.py        klippens status (data/clips.json)
```
