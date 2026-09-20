# OpportunityWatch

Python 3.11+ jälgija avalike kandideerimislehtede, fellowship'ide ja tööpakkumiste plokkide jaoks. Kasutab Requests'i ning BeautifulSoup'i. Vaikimisi kontroll iga 10 minuti järel; teade läheb kohe pärast muudatuse tuvastamist Discordi ja/või Telegrami.

**Olemasolev GitHubi paigaldus on ühendatud Discordiga.** Lehejälgija kõrval töötab automaatne võimaluste avastaja. Uuesti paigaldada pole vaja. Allpool olev paigaldusjuhend on uue eraldi paigalduse jaoks. Kaasas on 29 automaattesti.

## Mida see tuvastab?

- Valitud CSS-ploki normaliseeritud teksti ja linkide SHA-256 räsi muutuse; soovi korral puhastatud HTML-i muutuse.
- Märksõnadel põhineva staatuse: OPEN, CLOSED, MIXED või UNKNOWN. Need on signaalid, mitte kinnitus kandideerimise võimalikkuse kohta.
- Esimesel kontrollil saadetakse BASELINE koos hetke staatusega. Nii saad teada ka juba avatud taotlusvoorust, kuid seda ei nimetata uueks avamiseks.
- Kui seadistust muudad, tekib uus BASELINE. Sama sisu korduvat teadet ei tekita.
- Kolm järjestikust lugemisviga annavad MONITOR ERROR teate; taastumine annab MONITOR RECOVERED teate. Viga ei asenda viimast õnnestunud võrdlusolekut.

## Automaatne avastamine

`discovery.py` leiab ise uusi kuulutusi Remote OK, Arbeitnow, Startup Jobs ja Remotive voogudest ning kaheksast Google Newsi märksõnaotsingust. Otsingud hõlmavad AI-tööd, tasustatud andmemärgistamist, praktikat, fellowship'e, stipendiume, toetusi, bounty-projekte, tasustatud uuringuid ja auhinnarahaga häkatone. Otsingutulemuste veebilehti ei pea kasutaja ette teadma.

Töövoog käivitub iga 10 minuti järel. Avastaja küsib enamikku allikaid kuni kord tunnis; Remotive'i iga 6 tunni järel vastavalt allika soovitusele. Remotive'i avalikul vool on lisaks umbes 24-tunnine viivitus. Esimene otsing saadab kuni 4 leidu, järgnevad kuni 5 käivituse kohta ja 20 päevas; ülejäänud sobivad leiud jäävad tähtsuse järgi järjekorda kuni aegumiseni. Muutumata leide ei saadeta uuesti.

Fookus on Eesti/EL-il ja ülemaailmsel kaugtööl; kogemustaset ei piirata. Selgelt ainult USA-le või muule väljaspool valikut asuvale piirkonnale mõeldud kuulutused filtreeritakse. Puuduv või ebaselge asukoht märgitakse kinnitamata sobivusena. Märksõnafiltrid ei asenda kandidaadi sobivuse, tööloa, tasu või kuulutuse usaldusväärsuse kontrolli.

Google Newsi leiud on uudise/otsinguleiu sildiga ja viivad selle uudise juurde, mitte tingimata otse taotlusvormile. Need ei ole kinnitus, et kandideerimine on avatud. Otsija ei kata kogu internetti, tasulisi ega sisselogimise taga kanaleid. Uudiste indekseerimise ja ajastuse viivitused tähendavad, et leidmise minutit ei saa garanteerida.

`monitor.py` jätkab täpsete lehtede staatusevahetuste jälgimist `config.json` järgi. Avastaja konfiguratsioon on `discovery_config.json`. Mõlema olek salvestatakse `monitor-state` harusse. Avastaja raport näitab allikate õnnestumisi/tõrkeid, leitud kandidaatide arvu ja saatmisjärjekorda.

Allikad: [Remote OK](https://remoteok.com/faq), [Arbeitnow](https://www.arbeitnow.com/blog/job-board-api), [Startup Jobs](https://startup.jobs/api), [Remotive](https://github.com/remotive-io/remote-jobs-api). Google Newsi RSS-liides pole stabiilsuse garantiiga API; selle töötamist kontrolliti päris võrgupäringutega.

Sõnumid viitavad allikale ning kasutavad algse kuulutuse linki. Täispikki töökuulutuste kirjeldusi ei salvestata avalikku olekuharusse.

## Kiire kohalik käivitamine

Paki arhiiv lahti ja ava terminal kaustas `opportunity-watch`.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:DISCORD_WEBHOOK_URL = 'SINU_DISCORD_WEBHOOK_URL'
.venv\Scripts\python monitor.py --test-alert
.venv\Scripts\python monitor.py
```

Discordis loo soovitud kanali seadete Integrations / Webhooks all webhook ning kopeeri selle URL. Ära salvesta URL-i koodi, konfiguratsiooni ega avalikku reposse.

Telegrami kasutamiseks loo @BotFather kaudu bot, saada uuele botile `/start` ja leia oma `chat.id` Telegrami Bot API `getUpdates` vastusest. Kui bot kasutab juba webhooki, kasuta eraldi uut boti: getUpdates ja webhook ei tööta samal ajal. Määra:

```powershell
$env:TELEGRAM_BOT_TOKEN = 'SINU_BOT_TOKEN'
$env:TELEGRAM_CHAT_ID = 'SINU_CHAT_ID'
.venv\Scripts\python monitor.py --test-alert
```

Piisab ühest kanalist. Mõlema seadistamisel saadetakse mõlemasse. Testkäsk saadab päris sõnumi. `.env` faile skript automaatselt ei lae: kasuta keskkonnamuutujaid või GitHub Actions Secrets'i.

Pidev kohalik töö:

```powershell
.venv\Scripts\python monitor.py --loop
```

Arvuti peab olema sisse lülitatud ja ärkvel. Ctrl+C peatab protsessi. `--loop` kontrollib kõiki sihtlehti järjest, ootab seejärel 600 sekundit ning lisab 0–15 sekundit juhuslikku nihet. Tegelik tsükkel sisaldab ka päringute kestust. Seadistuse muutmisel taaskäivita protsess. Ühekordne käsk sobib operatsioonisüsteemi ajastajale; ära käivita mitut koopiat sama olekufailiga eri masinates.

## GitHub Actions: tasuta alustamine

1. Loo GitHubis **avalik** repository ja lisa sinna selle kausta sisu, kaasa arvatud peidetud `.github/workflows/watch.yml`. `monitor.py`, `config.json` ja `requirements.txt` peavad olema repo juurkaustas. Ära laadi üles `.venv`, `__pycache__`, kohalikku `state.json` faili ega saladusi.
2. Ava Settings → Secrets and variables → Actions → New repository secret. Lisa `DISCORD_WEBHOOK_URL` või mõlemad `TELEGRAM_BOT_TOKEN` ja `TELEGRAM_CHAT_ID`.
3. Kontrolli Settings → Actions → General alt, et Actions on lubatud ja workflow saab reposse kirjutada. YAML küsib `contents: write` õigust oleku salvestamiseks. Organisatsiooni reeglid võivad seda piirata.
4. Salvesta workflow repo **vaikeharusse**, tavaliselt `main`. Actions → Opportunity watch → Run workflow käivitab esimese kontrolli ja BASELINE teate. Kontrolli, et nii lehtede kontroll kui ka oleku salvestamine lõppesid edukalt.
5. Järgmised kontrollid on UTC minuti 3, 13, 23, 33, 43 ja 53 peal. Viie minuti jaoks asenda cron väärtusega `3-58/5 * * * *`; 15 minuti jaoks `3,18,33,48 * * * *`. `config.json` interval mõjutab ainult `--loop` režiimi, mitte Actionsi ajastust.

Workflow loob eraldi `monitor-state` haru ja salvestab sinna oleku iga käivituse lõpus, ka juhul, kui mõni päring või teavitus ebaõnnestub. Ära kustuta seda haru ega keela sellele harule workflow kirjutamisõigust. Oleku jaoks ei kasutata ajutist Actions cache'i. Käivitused on serialiseeritud; samaaegne uus käivitus ei katkesta vana.

Avalikus repos on nähtavad ka jälgitavad URL-id, staatuste ajalugu ning järjekorras olevate teadete tekstid. Saladusi olekusse ei kirjutata. Kasuta seda näidist avalike lehtede jaoks. Privaatrepo puhul kontrolli oma Actionsi minutite eelarvet; tasuta piiramatus ei kehti.

GitHubi standardrunnerid on avalikes repodes tasuta, kuid **ajastusel puudub täpsuse garantii**. Käivitused võivad hilineda või ära jääda. Avaliku repo ajastused lülitatakse 60-päevase tegevusetuse korral välja; kontrolli perioodiliselt Actionsi olekut ja luba ajastus vajadusel uuesti. Ära eelda, et botikommendid hoiavad seda tingimata aktiivsena. Lülita sisse GitHubi ebaõnnestunud workflow'de teavitused. Skript ei saa ise märku anda, kui ajastaja seda üldse ei käivita.

## PythonAnywhere

**Tasuta kontoga ei saa seda usaldusväärselt iga 5–15 minuti järel ööpäevaringselt käivitada.** Kehtiva dokumentatsiooni järgi pole uutel tasuta kontodel scheduled tasks'i; enne 15.01.2026 loodud kontodel (EL-i süsteemis enne 08.01.2026) on üks päevane ajastatud töö. Always-on tasks nõuab tasulist kontot. Tasuta kontode väljuv internetiühendus on lisaks lubatud domeenide nimekirjaga piiratud.

Tasulisel kontol laadi failid üles, loo virtuaalkeskkond ja paigalda sõltuvused:

```bash
python3.12 -m venv /home/YOUR_USERNAME/opportunity-watch/.venv
/home/YOUR_USERNAME/opportunity-watch/.venv/bin/python -m pip install -r /home/YOUR_USERNAME/opportunity-watch/requirements.txt
```

Kasuta kontol tegelikult saadaolevat Python 3.11+ versiooni. Loo privaatne käivitusfail `/home/YOUR_USERNAME/run-watch.sh`, milles on vajalikud keskkonnamuutujad:

```bash
#!/bin/bash
set -euo pipefail
cd /home/YOUR_USERNAME/opportunity-watch
export DISCORD_WEBHOOK_URL='SINU_WEBHOOK_URL'
# Telegrami jaoks lisa siia selle kaks keskkonnamuutujat.
exec .venv/bin/python -u monitor.py --loop
```

Sea faili õigused `chmod 700 /home/YOUR_USERNAME/run-watch.sh`. Lisa Tasks → Always-on tasks alla käsk `bash /home/YOUR_USERNAME/run-watch.sh`. Ära laadi saladustega käivitusfaili GitHubi. Avatud tasuta konsooli jätmine ei asenda always-on teenust.

## Lehtede lisamine ja müra vähendamine

Muuda `config.json` targets-loendit. Igal sihtmärgil peab olema unikaalne `id`. Näidis (URL ja CSS-selektor tuleb asendada tegeliku lehe omadega):

```json
{
  "id": "company-ai-jobs",
  "name": "Company AI jobs",
  "url": "https://example.org/careers",
  "selector": "#open-positions",
  "ignore_selectors": [".relative-time"],
  "mode": "text",
  "alert_on_change": true
}
```

Leia brauseri Inspect abil konkreetse taotlusploki selektor. Puuduv selektor põhjustab vea; skript ei lähe vaikselt üle kogu lehe jälgimisele. Näidiskonfiguratsioon kasutab CodePathi `body` plokki ning jätab välja päise, navigeerimise ja jaluse, mistõttu ka muu põhisisu muudatus võib teate anda. Kitsam selektor vähendab müra.

- `mode: "text"`: teksti ja linkide muutused; tühikute muutused ei häiri.
- `mode: "html"`: valitud puhastatud HTML-ploki muutused, kaasa arvatud atribuudi `disabled` lisamine/eemaldamine. Rohkem müra. Script/style/noscript/template eemaldatakse mõlemas režiimis.
- `alert_on_change: false`: ainult staatusevahetused, algteade ja terviseteated; uue tööpakkumise lisandumine sama staatuse juures võib siis märkamata jääda.
- `open_patterns` ja `closed_patterns`: lehepõhised Python regex'id. `Applications will open ...` ei sobitu vaikimisi avatud staatusega. Mõlema rühma vaste korral on staatus MIXED.

Märksõnad võivad esineda ajaloolises tekstis, FAQ-s või teise programmi juures. Kohanda plokki ja mustreid ning kontrolli teate järel lehte. CSS-iga peidetud teksti BeautifulSoup ei erista; JavaScripti see ei käivita. JavaScriptiga täidetava tööpakkumiste loendi puhul kasuta eraldi avalikku HTML-lehte või kohanda lahendus ametlikule API-le/RSS-ile. Sisselogimist, CAPTCHA-t ega blokeeringut skript ei ületa.

## Töökindlus ja piirid

Päringutel on ühendus- ja lugemistähtajad, vastuse mahupiirang, sama hosti päringute vahe, robots.txt kontroll, eksponentsiaalne veajärgne ooteaeg ning HTTP 429/503 Retry-After tugi. robots.txt ajutise vea korral sihtlehte ei loeta. 404 robots.txt puhul loetakse leht lubatuks. Robots-päring tehakse igal tsüklil üks kord domeeni kohta. Kasuta väheseid täpseid lehti, järgi saidi kasutustingimusi ja nõutud kontrollisagedust. Viisaka User-Agent'i kasutamine ei taga blokeerimisest pääsemist. Soovi korral määra `MONITOR_USER_AGENT` väärtuseks `OpportunityWatch/1.0 (+sinu kontakt)`.

Oleku ja saatmisjärjekorra salvestus on atomaarne. Ebaõnnestunud teavitus jääb järjekorda ka siis, kui leht hiljem uuesti muutub. Edukalt teavitatud kanalit ei korrata, kui teine kanal ebaõnnestus. Kui eemaldad kanali, millele on veel saatmata sõnumeid, säilivad need: taasta kanal või eemalda pärast varukoopia tegemist vastav kanalinimi olekufaili `outbox[].pending` kirjetest.

Edastus järgib **vähemalt ühe saatmiskatse** põhimõtet: ajakatkestuse või krahhi korral võib juba kohale jõudnud teade korduda. Kui Actionsi oleku git push ebaõnnestub või kogu töö katkestatakse enne salvestamist, võib järgmine käivitus samuti teateid korrata. Null-duplikaate ega katkematut teenust ei saa nende kanalite ja tasuta ajastajaga garanteerida. Katkine olekufail põhjustab vea, mitte vaikset ajaloo kustutamist.

10-minutiline polling tähendab tavaliselt kuni umbes 10 minutit avamise ja tuvastuse vahel, millele lisanduvad päringuaeg, viivitused ja tõrked. Kahe kontrolli vahel avatud ja juba suletud võimalus võib täiesti märkamata jääda. Täpse avamisminuti jaoks on vaja allika enda push-teavitust/webhooki või selle lubatud sagedusega kiiremat pidevat jälgimist; see näidis teadlikult alla 5 minuti ei kontrolli.

## Kontrollimine

```bash
python -m unittest discover -s . -p 'test_*.py' -v
python monitor.py --test-alert
python monitor.py
```

Testid katavad staatusevahetuse, segase staatuse, tekstimüra, lingimuutuse, HTML-atribuudi muutuse, kadunud selektori, robots-keelu, HTTP 429 ooteaja, veast taastumise ja osalise teavituse kordussaatmise pärast oleku uuesti laadimist. Pärast majutusse viimist kontrolli päris algteadet ja vähemalt üht automaatset käivitust. Testid ei tõenda, et kolmanda osapoole veebileht jääb kättesaadavaks.

## Ametlikud allikad (kontrollitud 19.09.2026)

- [GitHub Actions ajastused ja piirangud](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub Actions tasuta kasutus ja arveldamine](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [PythonAnywhere scheduled tasks](https://help.pythonanywhere.com/pages/ScheduledTasks)
- [PythonAnywhere always-on tasks](https://help.pythonanywhere.com/pages/AlwaysOnTasks)
- [PythonAnywhere tasuta internetipiirangud](https://help.pythonanywhere.com/pages/403ForbiddenError/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Discord webhook API](https://docs.discord.com/developers/resources/webhook#execute-webhook)
- [CodePath Claude Corps](https://www.codepath.org/claude-corps): kontrollimise ajal ütleb FAQ, et Cohort 1 kandideerimine on avatud. Samuti nõutakse USA-s töötamise õigust ja kuni kaht aastat täiskohaga töökogemust. Kontrolli sobivust enne kandideerimist.


## Laiendatud otsing ja prioriteedid (20.09.2026)

Tööd ning Claude Corpsi laadsete programmide avamised on põhifookus. Viie teate seas on kuni üks kõrvalvõimalus; neid saadetakse kuni neli päevas, kokku endiselt kuni 20. Programmid ja tööd vahelduvad; ülejäänud kohad täituvad olemasolevate põhivõimalustega. Puuduvat kõrvalvõimalust ei asendata müraga.

Lisatud Jobicy tasuta avalik API (kord tunnis), Hacker Newsi värbamis- ja vabakutseliste vestluste otsing (Algolia, kord tunnis), tasustatud programmide/projektide lisaotsingud ning Google Newsi indeksisse jõudnud YouTube'i, X-i ja TikToki postitused (kord kuue tunni jooksul). Sotsiaalmeedia tulemuse avaldaja domeen kontrollitakse üle. See EI OLE nende platvormide otseühendus ega kogu postituste või videote sisu analüüs. Redditi katse ei leidnud tulemusi ning Reddit pole lisatud toimiva allikana. Sotsiaalmeedia vihjed on kontrollimata; link võib minna Google Newsi kaudu postitusele.

Hacker Newsist võetakse ainult värbamisvestluste algsed kommentaarid; tööotsijate enesereklaam ja vastused jäetakse välja. Jobicy allika tasule lisatakse valuuta ja periood ainult nende olemasolul. Pakkumise sobivust, tähtaega, klienti ega tasumist skript sõltumatult ei kinnita. Automaatset klientidele kirjutamist, kandideerimist, uute domeenide jälgimisse lisamist ega videotranskriptide analüüsi ei toimu.

Allikate juhendid: https://github.com/Jobicy/remote-jobs-api ja https://hn.algolia.com/api . Jobicy päringute intervall järgib vähemalt ühe tunni piiri. GitHubi ajastaja tegelikud viivitused jäävad alles.
