# Piano di Porting: jarvis-agent → C++ nativo con Qt6/KDE Frameworks 6

## 1. Visione

Riscrivere l'agente desktop Jarvis (1794 linee Python, PySide6, webrtcvad, faster-whisper, gTTS) in **C++20 nativo** con **Qt 6.11** e **KDE Frameworks 6.27** per ottenere:

- Integrazione nativa con KDE Plasma 6 (StatusNotifierItem, KNotification, KGlobalAccel, KColorScheme)
- Prestazioni native CPU/GPU (whisper.cpp invece di Python faster-whisper)
- Zero dipendenze Python — un eseguibile singolo + librerie di sistema
- UI moderna via Kirigami o QWidgets con stile Breeze KDE

---

## 2. Dipendenze

### Runtime (pacchetti RPM OpenSUSE Tumbleweed)

```
# KF6 core
kf6-kconfig              # KConfig — impostazioni
kf6-kconfigwidgets       # KConfigDialog
kf6-knotifications       # KNotification — notifiche KDE
kf6-kglobalaccel         # KGlobalAccel — shortcut globali (Meta+V)
kf6-kwindowsystem        # KWindowSystem — lista finestre
kf6-kio                  # KIO — apri URL/file
kf6-krunner              # KRunner — integrazione ricerca
kf6-ki18n                # i18n — traduzioni
kf6-kcolorscheme         # KColorScheme — tema chiaro/scuro
kf6-kservice             # KService — .desktop parser

# Qt6
qt6-base                 # widgets, network, dbus
qt6-multimedia           # QAudioSource — cattura microfono
qt6-httpserver           # QHttpServer — IPC via HTTP locale
qt6-speech               # QTextToSpeech — TTS (opzionale)

# Librerie C
libespeak-ng-devel       # espeak-ng — TTS offline
whisper.cpp              # STT locale (da compilare, vedi sez. 5.5)
ffmpeg-devel             # libavformat/libswresample — conversione audio
webrtc-audio-processing-devel  # WebRTC VAD C API
```

### Build

```
extra-cmake-modules      # ECM — modulo CMake standard KDE
cmake ≥ 3.28
gcc-c++ ≥ 13
qt6-base-devel
qt6-multimedia-devel
qt6-httpserver-devel
kf6-*-devel (tutti i KF6 sopra)
```

---

## 3. Struttura progetto

```
jarvis/desktop_agent/
├── CMakeLists.txt                    # Root CMake
├── src/
│   ├── CMakeLists.txt               # Sorgenti
│   ├── main.cpp                     # Entry point
│   ├── app/
│   │   ├── jarvisapp.h/.cpp         # QApplication + wiring
│   │   ├── config.h/.cpp            # KConfig wrapper
│   │   └── thememanager.h/.cpp      # KColorSchemeManager
│   ├── tray/
│   │   └── statusnotifier.h/.cpp    # KStatusNotifierItem
│   ├── audio/
│   │   ├── audiocapture.h/.cpp      # QAudioSource pipeline
│   │   ├── vad.h/.cpp               # WebRTC VAD wrapper
│   │   ├── whisperengine.h/.cpp     # whisper.cpp STT
│   │   └── ttsengine.h/.cpp         # espeak-ng + gTTS
│   ├── api/
│   │   └── jarvisapi.h/.cpp         # QNetworkAccessManager
│   ├── desktop/
│   │   └── actions.h/.cpp           # [azione:] parser + executor
│   ├── chat/
│   │   ├── chatwindow.h/.cpp        # QSplitter dialog
│   │   ├── conversationmodel.h/.cpp # QAbstractListModel
│   │   └── messagewidget.h/.cpp     # QWidget bubble
│   ├── ipc/
│   │   └── ipcserver.h/.cpp         # QHttpServer
│   └── ui/
│       └── configdialog.h/.cpp      # KConfigDialog
├── resources/
│   ├── icons/                       # SVG icon
│   ├── jarvis-agent.desktop
│   └── jarvis-agent.appdata.xml
├── tests/
│   ├── CMakeLists.txt
│   ├── tst_vad.cpp
│   ├── tst_whisper.cpp
│   ├── tst_api.cpp
│   ├── tst_chat.cpp
│   └── tst_config.cpp
├── packaging/
│   └── jarvis-agent.spec
├── tools/
│   └── jarvis-trigger.sh            # curl http://localhost:9876/toggle
└── PORTPLAN.md                      # Questo file
```

---

## 4. Architettura — classi e flussi

### 4.1 Diagramma classi

```
main()
└── JarvisApp (QObject singleton)
    ├── Config (KConfig)              ← impostazioni su disco
    ├── StatusNotifier (KStatusNotifierItem)  ← tray nativo KDE
    ├── ThemeManager (KColorSchemeManager)    ← tema chiaro/scuro/sistema
    ├── AudioCapture (QAudioSource)   ← microfono → frame PCM
    │   └── Vad (WebRTC VAD C)        ← speech detection (30ms/frame)
    ├── WhisperEngine (whisper.cpp)   ← STT in QThread separato
    ├── TtsEngine (espeak-ng + gTTS)  ← TTS multi-backend
    ├── JarvisApi (QNetworkAccessManager) ← HTTP chat API
    ├── DesktopActions (KWindowSystem/KIO)  ← azioni finestre/URL/app
    ├── ChatWindow (QDialog + QSplitter)
    │   ├── ConversationModel (QAbstractListModel)
    │   └── MessageWidget (QWidget painter)
    └── IpcServer (QHttpServer)       ← localhost:9876 trigger API
```

### 4.2 Flusso `always_on` (ascolto continuo)

```
QAudioSource (16kHz, S16LE, mono)
  → 30ms frame (480 campioni)
  → Vad::isSpeech(frame) → speech_frames++
  → buffer circolare pre-roll 300ms
  → silence_timeout (0.8s) → utterance completa
  → fw: buffer pre-roll + utterance → WAV temp
  → signal: utteranceReady(path)

  main thread riceve segnale:
  → WhisperEngine::transcribeAsync(path)
      [QThread] whisper_full() → text
      → signal: transcriptionReady(text)
  → JarvisApi::queryAsync(text)
      [async HTTP] → risposta JSON
      → parse [azione: ...] → DesktopActions::execute()
      → text pulito
  → TtsEngine::speak(text)  [se tts_enabled]
  → ChatWindow::addMessage("assistant", text)
```

### 4.3 Thread model

| Thread | Componenti | Ruolo |
|--------|-----------|-------|
| **Main (GUI)** | QApplication, StatusNotifier, ChatWindow, ConfigDialog | Event loop Qt, UI reattiva |
| **Audio** | AudioCapture → Vad | Cattura microfono, VAD framerate reale 16kHz |
| **Whisper** | WhisperEngine (QThread) | Inferenza STT CPU-bound, non blocca GUI |
| **Async HTTP** | JarvisApi (QNetworkAccessManager) | I/O asincrono, callback su main thread |
| **TTS** | TtsEngine (processo separato o thread) | Riproduzione audio non bloccante |

### 4.4 Comunicazione tra thread

- **Audio → Main**: `QObject::connect(audioCapture, &AudioCapture::utteranceReady, this, &JarvisApp::onUtteranceReady, Qt::QueuedConnection)`
- **Main → Whisper**: `QtConcurrent::run()` o `QThreadPool` per call asincrona
- **Whisper → Main**: `Q_EMIT transcriptionReady(text)` (queued connection)
- **API → Main**: callback `QNetworkReply::finished` con `Qt::AutoConnection`

---

## 5. Specifica moduli dettagliata

### 5.1 Config (`app/config.h/.cpp`)

```cpp
class Config : public QObject {
    Q_OBJECT
public:
    explicit Config(QObject *parent = nullptr);

    // Lettura
    QString jarvisUrl() const;
    QString userId() const;
    QString jarvisModel() const;
    QString whisperModel() const;
    QString recordMode() const;      // "always_on" | "vad" | "manual"
    int vadAggressiveness() const;   // 0-3
    double silenceTimeout() const;   // secondi
    int vadMinSpeechFrames() const;
    int vadPreMs() const;
    int vadPostMs() const;
    bool ttsSlow() const;
    QString ttsTld() const;          // "it" | "com" | "co.uk"
    int ttsPitch() const;            // 0-99
    int ttsSpeed() const;            // 80-450 wpm
    bool sttEnabled() const;
    bool ttsEnabled() const;
    bool showNotifications() const;
    QString themeMode() const;       // "system" | "chiaro" | "scuro"

    // Scrittura + sync
    void setJarvisUrl(const QString &v);
    // ... tutti i setter ...
    void save();

signals:
    void configChanged();  // emesso dopo save()

private:
    KConfig m_config;
    KConfigGroup m_general, m_audio, m_voice, m_theme;
};
```

Implementazione: ogni getter chiama `m_general.readEntry("key", default)`, ogni setter chiama `m_general.writeEntry("key", v)` e `m_config->sync()`.

Ogni chiave di configurazione è compatibile con il file JSON `~/.config/jarvis-agent/config.json` esistente (usando KConfig INI o JSON backend).

---

### 5.2 StatusNotifier (`tray/statusnotifier.h/.cpp`)

```cpp
class StatusNotifier : public QObject {
    Q_OBJECT
public:
    explicit StatusNotifier(QObject *parent = nullptr);

    void setStatusText(const QString &text);
    void setRecordingIcon(bool recording);

signals:
    void toggleRecording();
    void openChat();
    void showConfig();
    void setRecordMode(const QString &mode);
    void setTheme(const QString &mode);
    void quitApp();

private:
    KStatusNotifierItem *m_item;
    void buildMenu();
    QMenu *m_menu;
};
```

- Usa **KStatusNotifierItem** (non QSystemTrayIcon):
  - Supporto nativo StatusNotifierItem su KDE Plasma 6
  - Icona: Breeze `face-smile` personalizzata con badge recording
  - Categoria: `ApplicationStatus`
- Menu contestuale:
  - `Stato: {status}` (disabilitato)
  - — Separatore —
  - `Modo: {mode}` (toggle ciclo always_on → vad → manual)
  - `Sensibilità VAD` → sottomenu radio (Bassa/Media/Alta/Molto alta)
  - — Separatore —
  - `Chat con Jarvis...`
  - — Separatore —
  - `Tema` → sottomenu radio (Sistema/Chiaro/Scuro)
  - — Separatore —
  - `Configurazione...`
  - — Separatore —
  - `Esci`
- `KStatusNotifierItem::activate` → `toggleRecording` (click sinistro)

---

### 5.3 AudioCapture + VAD (`audio/`)

#### VAD (header-only wrapper C++ attorno WebRTC VAD C API)

```cpp
class Vad {
public:
    explicit Vad(int aggressiveness = 3);  // 0-3
    ~Vad();

    bool isSpeech(const int16_t *frame, size_t samples);
    void reset();
    void setAggressiveness(int level);

private:
    VadInst *m_handle = nullptr;
};
```

- Frame: 30ms, 480 samples a 16kHz
- Aggressiveness: 0 (più sensibile) → 3 (meno falsi positivi)
- WebRTC VAD C library: funzione `WebRtcVad_Process()` (da `webrtc-audio-processing-devel`)

#### AudioCapture

```cpp
class AudioCapture : public QObject {
    Q_OBJECT
public:
    explicit AudioCapture(QObject *parent = nullptr);

    bool start();   // avvia QAudioSource
    void stop();
    bool isRunning() const;

    // Modalità
    void setMode(const QString &mode);  // always_on / vad / manual
    void triggerStart();  // da click utente
    void triggerStop();   // da click utente

signals:
    void utteranceReady(const QByteArray &wavData, const QString &filePath);
    void recordingStarted();
    void recordingStopped();
    void vadSpeechDetected();
    void vadSilenceDetected();

private:
    QAudioSource *m_audioSource = nullptr;
    QIODevice *m_audioDevice = nullptr;

    // Buffer circolare pre-roll (300ms = 4800 samples)
    std::vector<int16_t> m_ringBuffer;
    size_t m_ringPos = 0;

    // VAD
    Vad m_vad;
    int m_speechFrames = 0;
    int m_silenceFrames = 0;
    int m_totalFrames = 0;

    enum State { Idle, PreRoll, Speaking, PostRoll } m_state = Idle;

    void onDataReady();
    void commitUtterance();
};
```

Flusso interno `onDataReady()`:
1. Legge chunk da QAudioSource (es. 480 samples = 30ms)
2. Scrive nel ring buffer (per pre-roll)
3. Chiama `m_vad.isSpeech(frame, 480)`
4. State machine:
   - `Idle`: scarta, accumula ring buffer
   - `Speaking`: incrementa speech_frames per ogni frame speech; silence_frames se no
   - `PostRoll`: dopo speech, accumula fino a `vadPostMs` di silenzio, poi `commitUtterance()`
5. `commitUtterance()`:
   - Prende pre-roll dal ring buffer + utterance dal buffer live
   - Salva come WAV (header manuale + libsndfile o semplice write)
   - `Q_EMIT utteranceReady(wavData, filePath)`

---

### 5.4 WhisperEngine (`audio/whisperengine.h/.cpp`)

```cpp
class WhisperEngine : public QObject {
    Q_OBJECT
public:
    explicit WhisperEngine(QObject *parent = nullptr);
    ~WhisperEngine();

    void loadModel(const QString &modelName);  // tiny/base/small/medium/large-v3
    bool isLoaded() const;

    // Transcribe (chiamare in QThread separato)
    QString transcribe(const QString &wavPath);

signals:
    void transcriptionReady(const QString &text);
    void transcriptionError(const QString &error);
    void loadingStarted();
    void loadingFinished();
    void loadingProgress(float percent);

private:
    whisper_context *m_ctx = nullptr;
    QString m_currentModel;
    std::mutex m_mutex;
    bool m_loading = false;
};
```

- `whisper_init()` con modello GGML da `~/.cache/jarvis/whisper/`
- `whisper_full()` con parametri: `beam_size=1`, `language="it"`, `print_progress=false`
- Download automatico modello: se file non esiste, HTTP GET da HuggingFace
- Gestione cambio modello a caldo: `loadModel()` in thread separato, locking con mutex, flag `m_loading`

**Fallback**: se whisper.cpp non disponibile, buffer audio → `faster-whisper` via QProcess
(chiamata allo script Python esistente).

---

### 5.5 TtsEngine (`audio/ttsengine.h/.cpp`)

```cpp
class TtsEngine : public QObject {
    Q_OBJECT
public:
    explicit TtsEngine(QObject *parent = nullptr);

    void speak(const QString &text);
    void stop();
    bool isSpeaking() const;

signals:
    void speakingStarted();
    void speakingFinished();
    void speakingError(const QString &error);

private:
    // Backend prioritario: espeak-ng
    bool speakEspeak(const QString &text);
    // Fallback: gTTS via HTTP
    bool speakGTTS(const QString &text);
    // Opzionale: Qt TextToSpeech
    bool speakQtSpeech(const QString &text);

    QMediaPlayer *m_player = nullptr;
    bool m_speaking = false;
};
```

Strategia `speak()`:
1. Se pitch/speed sono custom (non default 50/175) → espeak-ng (controllo preciso)
2. Se gTTS è disponibile (test ping) → richiesta HTTP GET a translate.google.com
3. Scarica audio → file temporaneo → QMediaPlayer::setSource() → play
4. Segnale `speakingFinished()` a fine riproduzione

---

### 5.6 JarvisApi (`api/jarvisapi.h/.cpp`)

```cpp
class JarvisApi : public QObject {
    Q_OBJECT
public:
    explicit JarvisApi(QObject *parent = nullptr);

    // Asincrono: risultato via segnale
    void queryAsync(const QString &text);
    void queryAsync(const QString &text, const QString &model);

signals:
    void responseReady(const QString &response, const QString &raw);
    void responseError(const QString &error);

private:
    QNetworkAccessManager *m_nam;
    QNetworkReply *postJson(const QJsonObject &body);

    QString parseResponse(const QJsonObject &json);
    QStringList extractActions(const QString &text);
};
```

`queryAsync()`:
1. Prepara JSON body: `{ "model": "...", "messages": [...], "stream": false }`
2. `m_nam->post(request, body)`
3. `connect(reply, &QNetworkReply::finished, this, &JarvisApi::onReply)`
4. `onReply()`: estrae `response` field, cerca `[azione: ...]` con regex
5. `Q_EMIT responseReady(cleanText, rawText)`

Timeout implementato con `QTimer::singleShot(120s, reply, &QNetworkReply::abort)`.

---

### 5.7 DesktopActions (`desktop/actions.h/.cpp`)

```cpp
class DesktopActions : public QObject {
    Q_OBJECT
public:
    explicit DesktopActions(QObject *parent = nullptr);

    // Analizza e esegue azioni dal testo risposta
    // Restituisce il testo ripulito dai tag azione
    QString parseAndExecute(const QString &text);

    // Esecuzione diretta
    void listWindows();
    void openUrl(const QString &url);
    void launchApp(const QString &app);
    void searchWeb(const QString &query);

signals:
    void actionResult(const QString &summary);
};
```

- `parseAndExecute()`: regex `\[azione:\s*(finestre|apri|avvia|cerca)\s*(.*?)\]`
- A seconda del comando, chiama la funzione corrispondente
- `listWindows()`: `KWindowSystem::windows()` o `QProcess("wmctrl", {"-l"})`
- `openUrl()`: `QDesktopServices::openUrl(QUrl(url))` o `KIO::openUrl()`
- `launchApp()`: `QProcess::startDetached(app, {})`
- `searchWeb()`: `QDesktopServices::openUrl(QUrl("https://www.google.com/search?q=" + query))`

---

### 5.8 ChatWindow (`chat/`)

#### Layout widget

```
┌─────────────────────────────────────────────────────┐
│  Header: "Jarvis" + stato connesso + pulsanti        │
├──────────┬──────────────────────────────────────────┤
│          │  ┌────────────────────────────────────┐  │
│  Lista   │  │  Messaggi (bubbles)                │  │
│  Conv    │  │  ┌───────────────────────────┐     │  │
│  [1]     │  │  │  Ciao come va?            │ ← │  │
│  [2]     │  │  └───────────────────────────┘     │  │
│  [3]*    │  │  ┌───────────────────────────┐     │  │
│          │  │  │ Jarvis: Tutto bene!       │ → │  │
│  [+]     │  │  └───────────────────────────┘     │  │
│          │  │                                    │  │
│          │  └────────────────────────────────────┘  │
│          │  ┌────────────────────────────────────┐  │
│          │  │ [ Scrivi messaggio...      ][Invia] │  │
└──────────┴──────────────────────────────────────────┘
```

#### ConversationModel (`chat/conversationmodel.h/.cpp`)

```cpp
struct Message {
    QString role;    // "user" | "assistant" | "system"
    QString text;
    qint64 timestamp;
};

struct Conversation {
    QString id;
    QString title;
    QList<Message> messages;
};

class ConversationModel : public QAbstractListModel {
    Q_OBJECT
public:
    explicit ConversationModel(QObject *parent = nullptr);

    // QAbstractListModel
    int rowCount(const QModelIndex &parent = {}) const override;
    QVariant data(const QModelIndex &index, int role) const override;

    // Gestione
    QString newConversation(const QString &title = {});
    void deleteConversation(const QString &id);
    void renameConversation(const QString &id, const QString &title);
    void addMessage(const QString &convId, const Message &msg);
    Conversation conversation(const QString &id) const;

    // Persistenza
    void load(const QString &path);
    void save(const QString &path) const;

private:
    QList<Conversation> m_conversations;
};
```

#### MessageWidget (`chat/messagewidget.h/.cpp`)

```cpp
class MessageWidget : public QWidget {
    Q_OBJECT
public:
    explicit MessageWidget(const Message &msg, QWidget *parent = nullptr);

    QSize sizeHint() const override;

protected:
    void paintEvent(QPaintEvent *event) override;

private:
    Message m_message;
    QTextDocument m_doc;  // per rendering HTML markdown-like

    void renderBubble(QPainter &p, const QRect &rect);
    QColor bubbleColor() const;
};
```

- Usa QPainter per disegnare bolla con bordo arrotondato
- **Utente**: bolla blu (#0d6efd chiaro / #3daee9 scuro), allineata a destra, testo bianco
- **Assistente**: bolla grigia superficie, allineata a sinistra, etichetta "Jarvis" bold
- Markdown leggero: `**bold**`, `*italic*`, `` `code` ``, ``` ```code``` ```
- HTML to QTextDocument via `setHtml()` per rendering

#### ChatWindow (`chat/chatwindow.h/.cpp`)

```cpp
class ChatWindow : public QDialog {
    Q_OBJECT
public:
    explicit ChatWindow(QWidget *parent = nullptr);

    void addMessage(const QString &role, const QString &text);
    void setVisible(bool visible) override;

signals:
    void sendMessage(const QString &text);  // verso JarvisApp

private:
    QListView *m_convList;
    ConversationModel *m_convModel;
    QScrollArea *m_messageArea;            // area con MessageWidget
    QPlainTextEdit *m_input;
    QPushButton *m_sendBtn;

    void rebuildMessages();
    void onSend();
    void onNewConversation();
    void onDeleteConversation();
    // eventFilter per Enter / Shift+Enter
};
```

---

### 5.9 IpcServer (`ipc/ipcserver.h/.cpp`)

```cpp
class IpcServer : public QObject {
    Q_OBJECT
public:
    explicit IpcServer(QObject *parent = nullptr);

    bool start(quint16 port = 9876);
    void stop();

signals:
    void toggleRecording();
    void sendText(const QString &text);
    void quitApp();

private:
    QHttpServer *m_server = nullptr;
};
```

Endpoints:
- `GET /toggle` → `toggleRecording` signal
- `GET /text?msg=...` → `sendText(msg)` signal (URL decoded)
- `GET /status` → risponde JSON con stato corrente
- `GET /quit` → `quitApp()` signal

Sostituisce socket Unix `/tmp/jarvis-agent.sock` con HTTP locale.
Trigger via strumenti standard: `curl http://localhost:9876/toggle`.

---

### 5.10 ThemeManager (`app/thememanager.h/.cpp`)

```cpp
class ThemeManager : public QObject {
    Q_OBJECT
public:
    explicit ThemeManager(QObject *parent = nullptr);

    void applyTheme(const QString &mode);  // "system" | "chiaro" | "scuro"
    QString currentTheme() const;

signals:
    void themeChanged(const QString &mode);

private:
    QString m_currentMode = "system";
    KColorSchemeManager *m_schemeManager = nullptr;
};
```

- `applyTheme("system")`: `QApplication::setStyle("")` → stile KDE Breeze nativo
- `applyTheme("chiaro")`: crea QPalette chiara con `KColorScheme::ColorSet::View`, applica a QApplication
- `applyTheme("scuro")`: QPalette scura via `KColorScheme(QColorScheme::Complementary, KColorScheme::Window, KColorScheme::Dark)`

---

### 5.11 ConfigDialog (`ui/configdialog.h/.cpp`)

```cpp
class ConfigDialog : public KConfigDialog {
    Q_OBJECT
public:
    explicit ConfigDialog(QWidget *parent = nullptr);

    // Pagine gestite automaticamente da KConfigDialog
    void updateSettings() override;
    void updateWidgets() override;
    bool hasChanged() const override;
};
```

KConfigDialog con pagine (QScrollArea + QFormLayout):

| Pagina | Widgets |
|--------|---------|
| **Aspetto** | QComboBox tema (Sistema/Chiaro/Scuro) |
| **Server** | QLineEdit URL, QLineEdit User ID, QComboBox editabile modello AI |
| **STT** | QLineEdit lingua, QComboBox modello Whisper (tiny/base/small/medium/large-v3) |
| **Voce** | QCheckBox slow, QComboBox TLD (it/com/co.uk), QSpinBox pitch (0-99), QSpinBox speed (80-450) |
| **Registrazione** | QComboBox modalità, QSpinBox sensibilità VAD (0-3), QDoubleSpinBox timeout (0.3-5.0), QSpinBox min frame, QSpinBox pre-roll, QSpinBox post-roll |
| **Opzioni** | QCheckBox STT, QCheckBox TTS, QCheckBox notifiche |

KConfigDialog si occupa di save/revert tramite KConfig.

---

## 6. Roadmap — 7 fasi

| Fase | Durata | Deliverable | Dipendenza |
|------|--------|-------------|------------|
| **1. Scheletro** | 1 sett | CMake + main.cpp + JarvisApp + Config + StatusNotifier | nessuna |
| **2. Audio** | 1 sett | AudioCapture + Vad + WAV writer | Fase 1 |
| **3. STT + TTS** | 1-2 sett | WhisperEngine + TtsEngine | Fase 2 |
| **4. API + Azioni** | 1 sett | JarvisApi + DesktopActions | Fase 1 |
| **5. Chat** | 1-2 sett | ChatWindow + ConversationModel + MessageWidget | Fase 4 |
| **6. Config + Tema + IPC** | 1 sett | ConfigDialog + ThemeManager + IpcServer | Fase 1 |
| **7. Packaging + Test** | 1 sett | RPM spec, test unitari, README, install.sh | Tutte |

**Totale stimato**: 7-10 settimane part-time.

### Dettaglio Fase 1 — Scheletro

- [ ] Installare dipendenze di build: `sudo zypper install extra-cmake-modules cmake gcc-c++`
- [ ] Installare dev packages: tutte le dipendenze enumerate in sezione 2
- [ ] Creare `CMakeLists.txt` root con `find_package(ECM REQUIRED)`, `find_package(KF6 REQUIRED COMPONENTS ...)`, `find_package(Qt6 REQUIRED COMPONENTS ...)`
- [ ] Creare `src/CMakeLists.txt` con `add_executable(jarvis-agent ...)` + `target_link_libraries(...)`
- [ ] Implementare `main.cpp`: QApplication, KAboutData, JarvisApp, app.exec()
- [ ] Implementare `Config` con tutte le chiavi e KConfig backend
- [ ] Implementare `StatusNotifier` con KStatusNotifierItem e menu fisso
- [ ] Build e test: `cmake -B build && cmake --build build && ./build/jarvis-agent`

### Dettaglio Fase 2 — Audio

- [ ] Compilare/integrare WebRTC VAD C library
- [ ] Implementare `Vad` wrapper C++
- [ ] Implementare `AudioCapture` con QAudioSource + ring buffer + state machine
- [ ] Test VAD: generare toni, verificare `isSpeech()`
- [ ] Test cattura: salvare WAV su file, verificare con `ffprobe`

### Dettaglio Fase 3 — STT + TTS

- [ ] Clonare/compilare whisper.cpp: `git clone https://github.com/ggerganov/whisper.cpp`
- [ ] Integrare `whisper.h` nel progetto CMake (ExternalProject_Add o submodulo)
- [ ] Implementare `WhisperEngine::loadModel()` + `transcribe()`
- [ ] Implementare `TtsEngine::speakEspeak()` con espeak-ng API C
- [ ] Implementare `TtsEngine::speakGTTS()` con QNetworkAccessManager
- [ ] Test: trascrivere file WAV di esempio, confrontare con Python

### Dettaglio Fase 4 — API + Azioni

- [ ] Implementare `JarvisApi::queryAsync()` con POST JSON
- [ ] Parsing risposta: estrazione del campo `response`
- [ ] Implementare `DesktopActions::parseAndExecute()` con regex
- [ ] Eseguire azioni: finestre, apri, avvia, cerca
- [ ] Test: curl verso Jarvis API, verificare parsing risposta

### Dettaglio Fase 5 — Chat

- [ ] Implementare `ConversationModel` con QAbstractListModel
- [ ] Persistenza JSON conversazioni su `~/.config/jarvis-agent/conversations.json`
- [ ] Implementare `MessageWidget` con QPainter bolla
- [ ] Implementare `ChatWindow` con QSplitter + QListView + scroll area + input
- [ ] eventFilter per Enter/Shift+Enter
- [ ] Markdown rendering: bold, italic, code, code blocks
- [ ] Test: invio messaggio, visualizzazione bubble, cambio conversazione

### Dettaglio Fase 6 — Config + Tema + IPC

- [ ] Implementare `ConfigDialog` con KConfigDialog e tutte le pagine
- [ ] Integrare tema con KColorSchemeManager
- [ ] Implementare `ThemeManager::applyTheme()` per system/chiaro/scuro
- [ ] Implementare `IpcServer` con QHttpServer
- [ ] Aggiornare `jarvis-trigger.sh` per usare curl
- [ ] Configurare KGlobalAccel per Meta+V

### Dettaglio Fase 7 — Packaging + Test

- [ ] Scrivere test unitari con QTest (Vad, Config, API parser, Chat model)
- [ ] Scrivere RPM spec `jarvis-agent.spec` per OBS
- [ ] Aggiornare `install.sh`
- [ ] Aggiornare `jarvis-agent.desktop`
- [ ] Scrivere `jarvis-agent.appdata.xml` per KDE Discover
- [ ] Documentazione build in README

---

## 7. Migrazione graduale (ponte opzionale)

Se preferisci un approccio incrementale invece della riscrittura totale:

```
Fase ponte: C++ chiama Python via QProcess
┌──────────────────────┐
│  C++ (Qt6/KF6)       │
│  - StatusNotifier     │
│  - ChatWindow         │
│  - ConfigDialog       │
│          │            │
│  QProcess↓↑JSON       │
│          │            │
│  Python agent (esistente)  │
│  - AudioCapture+VAD        │
│  - Whisper STT             │
│  - TTS engine              │
│  - Jarvis API              │
└──────────────────────────┘
```

Poi, un backend alla volta, riscrivi in C++ e rimuovi la dipendenza Python.

**Raccomandazione**: riscrittura diretta. 1794 linee Python sono un ambito ben delimitato, il codice C++ risulterà probabilmente di 2500-3500 linee (per via dei tipi espliciti e header). Ogni modulo è indipendente e testabile separatamente.

---

## 8. Rischi e mitigazioni

| Rischio | Probabilità | Impatto | Mitigazione |
|---------|-------------|---------|-------------|
| whisper.cpp non compila su Tumbleweed | Media | Alto | Testare build su macchina target; usare prebuilt binary da OBS |
| Qt6 Multimedia API cambia | Bassa | Medio | API `QAudioSource` è stabile da Qt6.0; test contro qt6-base 6.11 |
| WebRTC VAD licenza (BSD 3-clause) | Bassa | Basso | Includere attribuzione in NOTICE; compatibile con progetto AGPL |
| Performance STT su CPU senza GPU | Media | Medio | `tiny` model richiede ~1x RT; su CPU moderna < 1s per 5s audio |
| Integrazione KF6 assente su altre distro | Bassa | Basso | Target primario: OpenSUSE Tumbleweed; per altre distro documentare build |
| Gestione thread audio complessa | Media | Medio | QThread + QMutex + QWaitCondition; test con AddressSanitizer |

---

## 9. File da eliminare / archiviare

Dopo il porting completo:

| File | Destinazione |
|------|-------------|
| `jarvis/desktop_agent/jarvis_agent.py` | Archiviato in `legacy/jarvis_agent.py` |
| `jarvis/desktop_agent/requirements-agent.txt` | Sostituito da RPM dependencies |
| `jarvis/desktop_agent/install.sh` | Riscritto per procedura CMake |
| `jarvis/desktop_agent/jarvis-trigger.sh` | Aggiornato per `curl localhost:9876` |
| `jarvis/desktop_agent/setup-kde-shortcut.sh` | Aggiornato per KGlobalAccel |

---
